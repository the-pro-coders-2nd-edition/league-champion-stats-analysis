"""End-to-end report generation orchestration."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Any

import pandas as pd

from league_stats_runner.analysis.coach.engine import VISIBLE_RECOMMENDATIONS
from league_stats_runner.analysis.deaths import deaths_dataframe
from league_stats_runner.analysis.matchups import matchup_recommendation
from league_stats_peers.analysis.peer import build_peer_comparison, comparisons_dataframe
from league_stats_common.core.champions import champion_display_name, champion_slug
from league_stats_common.core.config import (
    GAME_WINDOW_OPTIONS,
    QUEUE_FILTER_OPTIONS,
    QUEUE_SUBTITLE_LABELS,
    RANKED_FLEX_QUEUE_ID,
    RANKED_SOLO_QUEUE_ID,
    AppConfig,
)
from league_stats_common.core.models import (
    MatchRecord,
    PeerComparisonResult,
    ProgressionComparison,
    RankedEntry,
    GameReviewPayload,
    GameReviewQueueBundle,
    format_rank_division,
    format_solo_rank_label,
    player_rank_fields,
    queue_rank_fields,
)
from league_stats_common.infra.ddragon_assets import DDragonAssets
from league_stats_common.infra.riot_api import RiotApiClient
from league_stats_runner.ingest.parser import BuildPool, discover_build_pools
from league_stats_runner.pipeline.bundles import (
    build_all_ranked_ladder,
    build_window_bundle,
    career_view_for_queue,
    bundle_to_template_context,
    default_game_window_key,
    default_queue_filter_key,
    filter_records_by_accounts,
    filter_records_by_queue,
    game_window_options,
    queue_filter_options,
    serialize_report_views_json,
    slice_records,
)
from league_stats_runner.pipeline.view_models import peer_row_display
from league_stats_runner.pipeline.fetch import (
    group_records,
    load_all_records,
)
from league_stats_runner.pipeline.frames import AnalysisFrames, build_analysis_frames
from league_stats_runner.pipeline.progression import (
    build_progression_exports,
    build_progression_views,
    progression_to_template_context,
)
from league_stats_runner.analysis.game_review.hints import game_review_tooltips
from league_stats_runner.pipeline.game_review import build_game_review_views, game_review_to_template_context
from league_stats_runner.pipeline.services import PlayerContext, Services
from league_stats_runner.pipeline.summaries import (
    ReportStats,
    build_domain_summaries,
    build_export_summary,
    compute_report_stats,
    generate_recommendations,
)
from league_stats_runner.presentation.brand_assets import brand_context
from league_stats_runner.presentation.career import awaiting_peers_career_view
from league_stats_runner.presentation.export import Exporter
from league_stats_runner.presentation.graphs import ChartIconResolver, GraphFactory
from league_stats_runner.presentation.report import (
    build_manifest_entry,
    build_player_builds_nav,
    discover_player_builds,
    game_creation_ms_to_iso,
    refresh_report_indexes,
    save_build_record,
    utc_now_iso,
)
from league_stats_runner.infra.derived import KIND_SLICE, open_derived_store, slice_fingerprint
from league_stats_common.infra.report_store import open_report_store
from league_stats_runner.presentation.report_json import context_to_json
from league_stats_common.utils import get_logger


class NoEligibleBuildsError(RuntimeError):
    """Raised when no champion+lane build has enough qualifying games."""


_APEX_TIERS = frozenset({"MASTER", "GRANDMASTER", "CHALLENGER"})


def _merge_manifest_with_disk(
    manifest_builds: list[dict[str, Any]], player_slug: str
) -> list[dict[str, Any]]:
    """Keep sidebar links for every previously saved report, preferring live pool stats."""
    by_slug: dict[str, dict[str, Any]] = {
        champion_slug(str(entry["champion"]), str(entry["role"])): entry
        for entry in manifest_builds
    }
    for saved in discover_player_builds(player_slug):
        champion = str(saved.get("champion", "")).strip()
        role = str(saved.get("role", "")).strip()
        if not champion or not role:
            continue
        slug = champion_slug(champion, role)
        if slug in by_slug:
            continue
        by_slug[slug] = build_manifest_entry(
            champion=champion,
            role=role,
            games=int(saved.get("games", 0) or 0),
            winrate=float(saved.get("winrate", 0.0) or 0.0),
        )
    return sorted(
        by_slug.values(),
        key=lambda entry: (int(entry.get("games", 0)), str(entry.get("build_label", ""))),
        reverse=True,
    )


def _meta_players(
    config: AppConfig, profile_players: list[dict[str, Any]] | None
) -> list[dict[str, Any]]:
    """Build the ``players`` list for report meta, preserving icon/rank fields."""
    from league_stats_common.core.models import player_rank_fields

    if profile_players:
        shaped: list[dict[str, Any]] = []
        for player in profile_players:
            entry: dict[str, Any] = {
                "riot_id": str(player.get("riot_id", "")),
                "tagline": str(player.get("tagline", "")),
            }
            if not entry["riot_id"] or not entry["tagline"]:
                continue
            raw_icon = player.get("profile_icon_id")
            if raw_icon is not None:
                try:
                    entry["profile_icon_id"] = int(raw_icon)
                except (TypeError, ValueError):
                    pass
            entry.update(player_rank_fields(player))
            shaped.append(entry)
        if shaped:
            return shaped
    return [
        {"riot_id": player.riot_id, "tagline": player.tagline}
        for player in config.players
    ]


def _account_icon_hrefs(
    meta_players: list[dict[str, Any]],
    asset_catalog: DDragonAssets,
    run_dir: Path,
) -> dict[str, str]:
    """Map ``RiotID#Tag`` labels to relative profile-icon hrefs."""
    icons: dict[str, str] = {}
    for player in meta_players:
        label = f"{player['riot_id']}#{player['tagline']}"
        raw_icon = player.get("profile_icon_id")
        if raw_icon is None:
            continue
        try:
            icon_id = int(raw_icon)
        except (TypeError, ValueError):
            continue
        asset_catalog.ensure_profile_icon(icon_id)
        href = asset_catalog.profile_icon_href(icon_id, from_dir=run_dir)
        if not href:
            continue
        icons[label] = href
        icons[label.casefold()] = href
    return icons


@dataclass
class BuildBatch:
    """Parsed records and eligible builds for one player (or pooled group)."""

    pools: list[BuildPool]
    records: list[MatchRecord]
    manifest_builds: list[dict[str, Any]]
    primary_puuid: str
    profile_players: list[dict[str, Any]] = field(default_factory=list)


def should_skip_unchanged_build(
    config: AppConfig,
    pool: BuildPool,
    records: list[MatchRecord],
    new_match_ids: frozenset[str] | set[str] | None,
) -> bool:
    """Skip re-analysis when an existing report has no newly fetched games.

    When ``new_match_ids`` is ``None`` (no fetch this run), never skip — callers
    that regenerate from cache always re-analyse. Builds without a report are
    always analysed.
    """
    if new_match_ids is None:
        return False
    build_slug = champion_slug(pool.champion, pool.role)
    with open_report_store() as store:
        if not store.has_build(config.reports_group_slug, build_slug):
            return False
    return new_match_ids.isdisjoint(record.match_id for record in records)


def live_block_goal_columns(career: dict[str, Any] | None) -> tuple[str, ...]:
    """Columns the live Career block's goals are judged on, if any.

    Game Review's curated key-stat list does not cover every column a goal can
    be built from, so these are threaded into Game Review separately to keep
    "Career goals for this game" complete regardless of which metric a goal
    happens to use.
    """
    if not career or not career.get("has_career"):
        return ()
    blocks = career.get("blocks") or []
    live = next((block for block in blocks if block.get("is_active")), None)
    if not live:
        return ()
    return tuple(
        goal["column"] for goal in live.get("goals", []) if goal.get("column")
    )


def report_needs_peer_comparison(config: AppConfig, pool: BuildPool) -> bool:
    """Whether the saved build report is still missing rank peer comparison."""
    build_slug = champion_slug(pool.champion, pool.role)
    with open_report_store() as store:
        meta = store.get_build(config.reports_group_slug, build_slug)
    if meta is not None and "has_peer_comparison" in meta:
        return not bool(meta.get("has_peer_comparison"))
    # `rank_comparison.csv` is a file export (out of scope for this Mongo
    # migration -- only report.json/meta.json/manifest.json/summary.json/
    # progression.json/.md moved), so this fallback still checks disk.
    run_dir = (
        config.output_dir
        / "reports"
        / config.reports_group_slug
        / build_slug
    )
    return not (run_dir / "rank_comparison.csv").is_file()


def patch_report_peer_comparison(
    config: AppConfig, pool: BuildPool, peer_comparison: PeerComparisonResult
) -> bool:
    """Cheaply rewrite an already-rendered ``report.json``'s peer-comparison
    fields in place, without re-running the analysis pipeline.

    Design "Progressive peer-comparison updates during live sampling" §3.2:
    RUNNER's stage-B wait loop calls this for every *interim*
    `NotifyPeerBaselineReady` callback (still refining) instead of the full
    `analyze_build` pass -- the score/lane/economy/etc. cards stay whatever
    Stage A rendered (peer-blind) until the *terminal* callback, which still
    goes through the normal `analyze_build` call (and is the only place
    Career computes, §3.3). Only `peer_comparison`/`peer_rows`/
    `has_peer_comparison` and `generated_at` are updated here -- deliberately
    narrow, so an interim push during a slow live sample is cheap (no graph
    rendering, no export writing, no career computation).

    Returns ``False`` (a no-op) when this pool has no `report.json` yet to
    patch -- callers should fall back to a full `analyze_build` in that case,
    the same as they would for a brand-new build.
    """
    run_dir = (
        config.output_dir
        / "reports"
        / config.reports_group_slug
        / champion_slug(pool.champion, pool.role)
    )
    report_json_path = run_dir / "report.json"
    if not report_json_path.is_file():
        return False
    try:
        context = json.loads(report_json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False

    context["has_peer_comparison"] = True
    context["peer_comparison"] = peer_comparison
    context["peer_rows"] = [
        peer_row_display(row.model_dump()) for row in peer_comparison.comparisons
    ]
    generated_at = utc_now_iso()
    context["generated_at"] = generated_at

    tmp_json_path = report_json_path.with_suffix(".json.tmp")
    tmp_json_path.write_text(json.dumps(context_to_json(context)), encoding="utf-8")
    os.replace(tmp_json_path, report_json_path)

    meta_path = run_dir / "meta.json"
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {}
        meta["has_peer_comparison"] = True
        meta["generated_at"] = generated_at
        write_report_meta(run_dir, meta)
    return True


def write_full_exports(
    config: AppConfig,
    records: list[MatchRecord],
    run_dir: Path,
    *,
    peer_comparison: PeerComparisonResult | None,
    ranked: RankedEntry | None,
    frames=None,
    report_stats=None,
    game_review: GameReviewPayload | None = None,
) -> dict[str, Any]:
    """Write CSV/JSON exports from the full (all games) dataset."""
    analysis_frames = frames or build_analysis_frames(records)
    summaries = build_domain_summaries(analysis_frames, records)
    stats_bundle = report_stats or compute_report_stats(analysis_frames, run_dir)

    summary = build_export_summary(
        config,
        analysis_frames,
        summaries,
        stats_bundle,
        peer_comparison=peer_comparison,
        ranked=ranked,
        records_count=len(records),
        game_review=game_review,
    )

    matchups_export = analysis_frames.matchups_df.copy()
    if not matchups_export.empty:
        matchups_export["recommendation"] = matchups_export.apply(
            lambda row: matchup_recommendation(row, role=config.role),
            axis=1,
        )
    corr_export = (
        stats_bundle.corr.reset_index().rename(columns={"index": "feature"})
        if not stats_bundle.corr.empty
        else stats_bundle.corr
    )

    recommendations = generate_recommendations(
        analysis_frames,
        stats_bundle.stats,
        config,
        peer_comparison=peer_comparison,
        records_count=len(records),
    )

    export_tables: dict[str, pd.DataFrame] = {
        "matches": analysis_frames.matches_df,
        "deaths": analysis_frames.deaths_df,
        "timeline": analysis_frames.timeline_df,
        "matchups": matchups_export,
        "vision": analysis_frames.vision_df,
        "items": analysis_frames.items_df,
        "runes": analysis_frames.runes_df,
        "objectives": analysis_frames.objectives_df,
        "teamfights": analysis_frames.teamfights_df,
        "correlations": corr_export,
    }
    if peer_comparison is not None:
        export_tables["rank_comparison"] = comparisons_dataframe(peer_comparison)

    exporter = Exporter(run_dir)
    exporter.write_all(
        tables=export_tables,
        recommendations=recommendations,
        build_label=config.build_label,
    )
    # Stage-A rewrites must not leave a prior peer export looking "ready".
    if peer_comparison is None:
        stale = run_dir / "rank_comparison.csv"
        if stale.is_file():
            stale.unlink()
    return summary


def _peer_salt(peer_comparison: PeerComparisonResult | None) -> str:
    """Fingerprint of the peer baseline a slice was built against."""
    if peer_comparison is None:
        return "nopeer"
    return hashlib.sha256(peer_comparison.model_dump_json().encode()).hexdigest()[:12]


def build_report_views(
    config: AppConfig,
    records: list[MatchRecord],
    graphs_dir: Path,
    *,
    peer_comparison: PeerComparisonResult | None = None,
    still_refining: bool = False,
    assets: DDragonAssets | None = None,
    shared_stats: Any = None,
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, PeerComparisonResult | None]],
    str,
]:
    """Build the queue x game-window dashboard bundles for one record set.

    Returns the serializable views, the peer result per queue/window slice,
    and the default queue key.
    """
    solo_count = sum(1 for record in records if record.queue_id == RANKED_SOLO_QUEUE_ID)
    flex_count = sum(1 for record in records if record.queue_id == RANKED_FLEX_QUEUE_ID)

    window_specs: list[tuple[str, int | None]] = [
        (str(size), size) for size in GAME_WINDOW_OPTIONS
    ]
    window_specs.append(("all", None))

    # Slices are cached on the full record set, not just their own games: every
    # slice's feature-importance figure and figure hints come from the shared
    # win predictor, which is trained on all records. So a new game correctly
    # invalidates every slice, and the cache pays off on re-renders that add no
    # games -- regenerate, account-view rebuilds, repeated requests.
    fullset_salt = slice_fingerprint(
        [record.match_id for record in records], salt="fullset"
    )
    peer_salt = _peer_salt(peer_comparison)
    graphs_salt = graphs_dir.as_posix()

    report_views: dict[str, dict[str, Any]] = {}
    view_peers: dict[str, dict[str, PeerComparisonResult | None]] = {}
    # One ladder for the whole report, spanning both ranked queues. Injected into
    # every slice below rather than rebuilt per slice, and only the all-ranked
    # views actually render it.
    #
    # Career is never attempted without a resolved, TERMINAL peer comparison:
    # Stage A (base stats) always passes ``peer_comparison=None`` here, and
    # advancing the ladder against an empty peer baseline used to just get
    # told "not ready" and return an awaiting_peers snapshot -- Stage A skips
    # calling advance_career at all and renders the same loading shape
    # directly. Design "Progressive peer-comparison updates during live
    # sampling" §3.3 extends this: an *interim* peer comparison
    # (``still_refining=True``, still improving in the background) also
    # renders the awaiting_peers shape -- Career only ever computes once,
    # against the terminal result, never against a still-refining interim one
    # that could change again a few seconds later. The actual "exactly once
    # even under a duplicate terminal delivery" guard lives one layer up, in
    # `worker.py`'s stage-B loop (a per-pool boolean, same shape as
    # `seen_stage_b`) -- this function is pure and has no call-spanning state
    # of its own to guard with.
    career_ladder = (
        build_all_ranked_ladder(config, records, peer_comparison)
        if peer_comparison is not None and not still_refining
        else awaiting_peers_career_view()
    )
    with open_derived_store() as derived:
        for queue_key in QUEUE_FILTER_OPTIONS:
            queue_records = filter_records_by_queue(records, queue_key)
            queue_total = len(queue_records)
            queue_peer = peer_comparison if queue_key == "solo" else None
            queue_label = QUEUE_SUBTITLE_LABELS[queue_key]
            windows: dict[str, dict[str, Any]] = {}
            window_peers: dict[str, PeerComparisonResult | None] = {}
            for window_key, limit in window_specs:
                sliced = slice_records(queue_records, limit)
                fingerprint = slice_fingerprint(
                    [record.match_id for record in sliced],
                    salt="|".join(
                        (
                            config.champion,
                            config.role,
                            queue_key,
                            window_key,
                            queue_label,
                            fullset_salt,
                            peer_salt,
                            graphs_salt,
                        )
                    ),
                )
                cached = derived.get(KIND_SLICE, fingerprint)
                if cached is not None:
                    windows[window_key] = cached["bundle"]
                    raw_peer = cached.get("peer")
                    window_peers[window_key] = (
                        PeerComparisonResult.model_validate(raw_peer)
                        if raw_peer is not None
                        else None
                    )
                    continue

                bundle = build_window_bundle(
                    config,
                    sliced,
                    graphs_dir,
                    peer_comparison=queue_peer,
                    queue_label=queue_label,
                    assets=assets,
                    shared_stats=shared_stats,
                )
                window_peer = bundle.pop("_peer_result", None)
                window_peers[window_key] = window_peer
                serializable = {k: v for k, v in bundle.items() if not k.startswith("_")}
                windows[window_key] = serializable
                derived.put(
                    KIND_SLICE,
                    fingerprint,
                    {
                        "bundle": serializable,
                        "peer": (
                            window_peer.model_dump(mode="json")
                            if window_peer is not None
                            else None
                        ),
                    },
                )
            for bundle_view in windows.values():
                bundle_view["career"] = career_view_for_queue(queue_key, career_ladder)
            report_views[queue_key] = {
                "total_games": queue_total,
                "default_window": default_game_window_key(queue_total),
                "window_options": game_window_options(queue_total),
                "windows": windows,
            }
            view_peers[queue_key] = window_peers
        derived.evict_to_budget()

    return report_views, view_peers, default_queue_filter_key(solo_count, flex_count)


# How large a group still gets its account subsets precomputed into the payload.
#
# Zero, because precomputing cost far more than it saved. A 3-account group shipped
# a full report for all 6 subsets -- 100 MB of a 123 MB payload, 81% of it -- and
# the default view uses none of them: reportState.js maps the "all" key to the base
# payload, not to a subset. So every reader paid 100 MB on every page load and every
# build switch to make a checkbox they may never tick feel instant.
#
# POST /api/players/{slug}/builds/{build_slug}/account-views already computes any
# combination on demand, and selectAccountKey already caches the result per session
# behind a spinner; groups larger than the old limit of 4 have always worked that
# way. Raise this again to trade payload size back for an instant first tick.
ACCOUNT_FULL_COMBINATION_LIMIT = 0


def account_view_key(labels: list[str] | tuple[str, ...]) -> str:
    """Stable views key for one account subset (sorted ``|``-joined labels)."""
    return "|".join(sorted(labels))


def account_subset_keys(
    labels: list[str], *, full_combination_limit: int = ACCOUNT_FULL_COMBINATION_LIMIT
) -> list[tuple[str, ...]]:
    """Proper account subsets to precompute (the full set is the main report).

    Empty at the default limit of 0: every combination is computed on demand.
    """
    if full_combination_limit <= 0:
        return []
    ordered = sorted(labels)
    if len(ordered) <= full_combination_limit:
        subsets: list[tuple[str, ...]] = []
        for size in range(1, len(ordered)):
            subsets.extend(combinations(ordered, size))
        return subsets
    return [(label,) for label in ordered]


def build_account_subset_views(
    config: AppConfig,
    records: list[MatchRecord],
    graphs_dir: Path,
    *,
    assets: DDragonAssets | None = None,
    account_icons: dict[str, str] | None = None,
    run_dir: Path | None = None,
) -> dict[str, Any]:
    """Dashboard + Form Tracker + Game Review views for one account slice.

    Peer comparison is intentionally omitted: the report's peer baseline is
    anchored to the primary player and would be misleading for subsets.
    """
    records = sorted(records, key=lambda record: record.game_creation_ms, reverse=True)
    frames = build_analysis_frames(records)
    stats = compute_report_stats(frames, run_dir or graphs_dir.parent)
    report_views, _, default_queue = build_report_views(
        config,
        records,
        graphs_dir,
        assets=assets,
        shared_stats=stats,
    )
    progression_views = build_progression_views(
        config, records, graphs_dir, assets=assets
    )
    game_review = build_game_review_views(
        config,
        records,
        frames,
        graphs_dir=graphs_dir,
        assets=assets,
        from_dir=run_dir,
        account_icons=account_icons,
    )
    return {
        "queue_filter_default": default_queue,
        "report_views": report_views,
        "progression_views": progression_views,
        "game_review_views": {
            queue_key: bundle.model_dump()
            for queue_key, bundle in game_review.queues.items()
        },
    }


def run_analysis(
    config: AppConfig,
    records: list[MatchRecord],
    *,
    peer_comparison: PeerComparisonResult | None = None,
    still_refining: bool = False,
    ranked: RankedEntry | None = None,
    player_builds: list[dict[str, Any]] | None = None,
    assets: DDragonAssets | None = None,
    profile_players: list[dict[str, Any]] | None = None,
    full_frames: AnalysisFrames | None = None,
    report_stats: ReportStats | None = None,
) -> str:
    """Run every analysis, write exports and save the report to Mongo.

    Returns the saved report's ``"{player_slug}/{build_slug}"`` reference
    (old callers expected a ``report.json`` path; nothing report-shaped is
    written to disk anymore -- see ``ReportStore``).

    ``full_frames``/``report_stats`` let a caller that already computed them
    for this exact record set (e.g. the web worker's peer-comparison pass,
    which re-analyses the same build right after its base pass) skip redoing
    the single most expensive step -- deepdive frame building plus the
    RandomForest/correlation/clustering stats train -- neither of which
    depends on ``peer_comparison``.
    """
    log = get_logger("pipeline")
    if not records:
        log.error("No qualifying ranked %s games found.", config.build_label)
        raise ValueError(f"No qualifying ranked {config.build_label} games found.")

    records = sorted(records, key=lambda record: record.game_creation_ms, reverse=True)
    total_games = len(records)
    solo_count = sum(1 for record in records if record.queue_id == RANKED_SOLO_QUEUE_ID)
    flex_count = sum(1 for record in records if record.queue_id == RANKED_FLEX_QUEUE_ID)

    run_dir = config.report_dir
    run_dir.mkdir(parents=True, exist_ok=True)
    graphs_dir = config.run_graphs_dir
    graphs_dir.mkdir(parents=True, exist_ok=True)

    asset_catalog = assets or DDragonAssets(config)
    asset_catalog.ensure_downloaded()
    asset_catalog.ensure_champion_ability_icons(config.champion)

    meta_players = _meta_players(config, profile_players)
    account_icons = _account_icon_hrefs(meta_players, asset_catalog, run_dir)

    if full_frames is None:
        frames_start = time.monotonic()
        full_frames = build_analysis_frames(records)
        log.info("Built analysis frames for %d games in %.1fs", total_games, time.monotonic() - frames_start)
    if report_stats is None:
        stats_start = time.monotonic()
        report_stats = compute_report_stats(full_frames, run_dir)
        log.info("Trained report stats (RandomForest/correlation/clustering) in %.1fs", time.monotonic() - stats_start)

    # Career must build before Game Review: the live block's goal columns are
    # only known once the ladder has advanced, and Game Review needs them to
    # carry each goal's raw value even when it falls outside the curated
    # Overview key-stat list (see career_goal_values below).
    report_views_start = time.monotonic()
    report_views, view_peers, default_queue = build_report_views(
        config,
        records,
        graphs_dir,
        peer_comparison=peer_comparison,
        still_refining=still_refining,
        assets=asset_catalog,
        shared_stats=report_stats,
    )
    log.info("Built report views in %.1fs", time.monotonic() - report_views_start)

    default_window = report_views[default_queue]["default_window"]
    default_bundle = report_views[default_queue]["windows"][default_window]
    default_peer = view_peers.get(default_queue, {}).get(default_window)
    goal_columns = live_block_goal_columns(default_bundle.get("career"))

    game_review_start = time.monotonic()
    game_review = build_game_review_views(
        config,
        records,
        full_frames,
        graphs_dir=graphs_dir,
        assets=asset_catalog,
        from_dir=run_dir,
        account_icons=account_icons,
        goal_columns=goal_columns,
    )
    log.info("Built game review views in %.1fs", time.monotonic() - game_review_start)

    exports_start = time.monotonic()
    summary = write_full_exports(
        config,
        records,
        run_dir,
        peer_comparison=peer_comparison,
        ranked=ranked,
        frames=full_frames,
        report_stats=report_stats,
        game_review=game_review,
    )
    log.info("Wrote full exports in %.1fs", time.monotonic() - exports_start)
    GraphFactory(
        graphs_dir,
        icon_resolver=ChartIconResolver(
            from_dir=run_dir,
            champion_href=asset_catalog.champion_chart_source,
            item_href=asset_catalog.item_chart_source,
            keystone_href=asset_catalog.keystone_chart_source,
            map_source=asset_catalog.map_chart_source,
            map_path=asset_catalog.map_icon_path(),
        ),
    ).death_heatmap_png(deaths_dataframe(records))

    progression_views = build_progression_views(
        config,
        records,
        graphs_dir,
        assets=asset_catalog,
    )
    default_preset = progression_views[default_queue]["default_preset"]
    default_progression = progression_views[default_queue]["presets"][default_preset]
    progression_comparison: ProgressionComparison | None = None
    if default_progression.get("comparison"):
        progression_comparison = ProgressionComparison.model_validate(default_progression["comparison"])
    progression_json, progression_md = build_progression_exports(progression_comparison)

    default_game_review = game_review.queues.get(default_queue) or game_review.queues.get("all")
    game_review_views = {
        queue_key: bundle.model_dump()
        for queue_key, bundle in game_review.queues.items()
    }

    region_display = config.routing_platform.upper()
    report_players: list[dict[str, Any]] = []
    for index, player in enumerate(meta_players):
        icon_href = None
        raw_icon = player.get("profile_icon_id")
        if raw_icon is not None:
            try:
                icon_href = asset_catalog.profile_icon_href(
                    int(raw_icon), from_dir=run_dir
                )
            except (TypeError, ValueError):
                icon_href = None
        entry: dict[str, Any] = {
            "riot_id": str(player["riot_id"]),
            "tagline": str(player["tagline"]),
            "label": f"{player['riot_id']}#{player['tagline']}",
            "profile_icon": icon_href,
            "is_main": index == 0,
            "region": region_display,
        }
        for queue in ("solo", "flex"):
            rank = queue_rank_fields(player, queue)
            if not rank:
                continue
            tier = str(rank[f"{queue}_tier"])
            division = str(rank.get(f"{queue}_rank") or "")
            lp = rank.get(f"{queue}_lp")
            entry[f"{queue}_rank_label"] = format_solo_rank_label(tier, division, lp)
            entry[f"{queue}_rank_division"] = format_rank_division(
                tier, division, apex_tiers=_APEX_TIERS
            )
            entry[f"{queue}_lp"] = lp
            asset_catalog.ensure_rank_emblem(tier)
            rank_icon = asset_catalog.rank_emblem_href(tier, from_dir=run_dir)
            if rank_icon:
                entry[f"{queue}_rank_icon"] = rank_icon
        report_players.append(entry)

    account_filter_json = "{}"
    if len(report_players) > 1:
        members: list[dict[str, Any]] = []
        for entry in report_players:
            label = str(entry["label"])
            games = sum(
                1
                for record in records
                if (record.account or "").casefold() == label.casefold()
            )
            members.append({**entry, "key": label, "games": games})
        member_labels = [member["key"] for member in members]
        subsets = account_subset_keys(member_labels)
        subset_views: dict[str, Any] = {}
        subsets_start = time.monotonic()
        log.info("Building %d account-subset view(s) for %s", len(subsets), config.build_label)
        for subset in subsets:
            subset_records = filter_records_by_accounts(records, set(subset))
            if not subset_records:
                continue
            subset_views[account_view_key(subset)] = build_account_subset_views(
                config,
                subset_records,
                graphs_dir,
                assets=asset_catalog,
                account_icons=account_icons,
                run_dir=run_dir,
            )
        log.info(
            "Built %d account-subset view(s) for %s in %.1fs",
            len(subsets),
            config.build_label,
            time.monotonic() - subsets_start,
        )
        account_filter_json = serialize_report_views_json(
            {
                "enabled": True,
                "full_combinations": len(member_labels)
                <= ACCOUNT_FULL_COMBINATION_LIMIT,
                "members": members,
                "default_key": "all",
                "views": subset_views,
            }
        )

    context: dict[str, Any] = {
        **brand_context(from_dir=run_dir, output_dir=config.output_dir),
        "build_label": config.build_label,
        "champion": champion_display_name(config.champion),
        "champion_icon": asset_catalog.champion_href(config.champion, from_dir=run_dir),
        "role_icon_href": asset_catalog.role_href(config.role, from_dir=run_dir),
        "role_display": config.role_display,
        "player_name": config.players_label,
        "report_players": report_players,
        "recommendation_visible_count": VISIBLE_RECOMMENDATIONS,
        "queue_filter_default": default_queue,
        "queue_filter_options": queue_filter_options(solo_count, flex_count),
        "game_window_default": default_window,
        "game_window_total": report_views[default_queue]["total_games"],
        "game_window_options": report_views[default_queue]["window_options"],
        "queue_label": default_bundle.get("queue_label", QUEUE_SUBTITLE_LABELS[default_queue]),
        "report_views_json": serialize_report_views_json(report_views),
        "progression_views_json": serialize_report_views_json(progression_views),
        "progression_default": default_preset,
        "game_review_json": serialize_report_views_json(game_review_views),
        "game_review_tooltips_json": serialize_report_views_json(
            game_review_tooltips(role=config.role)
        ),
        "account_filter_json": account_filter_json,
        "chatbot_stats": summary,
        # Web-served reports proxy chat through the backend and poll for
        # peer-analysis completion; local CLI reports embed the key directly.
        "chat_endpoint": config.chat_endpoint,
        "status_endpoint": config.status_endpoint,
        # Web-served reports get a "Player page" link in the nav.
        "player_page_href": (
            f"/players/{config.reports_group_slug}" if config.status_endpoint else None
        ),
        "report_slug": champion_slug(config.champion, config.role),
        "refresh_champion": config.champion,
        "refresh_role": config.role,
        "show_cs_stats": config.role.upper() != "UTILITY",
        "show_split_push_stats": config.role.upper() == "TOP",
        "chat_report_ref": (
            f"{config.reports_group_slug}/{champion_slug(config.champion, config.role)}"
        ),
        "gemini_api_key": None if config.chat_endpoint else config.gemini_api_key,
    }
    context.update(
        bundle_to_template_context(default_bundle, peer_comparison=default_peer)
    )
    context.update(progression_to_template_context(default_progression))
    if default_game_review is not None:
        context.update(
            game_review_to_template_context(default_game_review, recent_n=game_review.recent_n)
        )
    else:
        context.update(
            game_review_to_template_context(
                GameReviewQueueBundle(available=False, games_count=0),
                recent_n=game_review.recent_n,
            )
        )
    if player_builds:
        context["player_builds"] = build_player_builds_nav(
            player_builds,
            current_champion=config.champion,
            current_role=config.role,
            assets=asset_catalog,
            from_dir=run_dir,
        )

    context.setdefault("generated_at", utc_now_iso())

    player_slug = config.reports_group_slug
    build_slug = champion_slug(config.champion, config.role)
    report_payload = context_to_json(context)

    generated_at = context.get("generated_at", "")
    primary_icon = next(
        (
            int(player["profile_icon_id"])
            for player in meta_players
            if player.get("profile_icon_id") is not None
        ),
        None,
    )
    save_build_record(
        player_slug,
        build_slug,
        {
            "player": config.players_label,
            "riot_id": config.riot_id,
            "tagline": config.tagline,
            "players": meta_players,
            "profile_icon_id": primary_icon,
            "champion": config.champion,
            "role": config.role,
            "role_display": config.role_display,
            "build_label": config.build_label,
            "games": total_games,
            "winrate": default_bundle["overview"]["winrate"],
            "generated_at": generated_at,
            "last_game_at": (
                game_creation_ms_to_iso(max(record.game_creation_ms for record in records))
                if records
                else ""
            ),
            "score": default_bundle.get("score", 0),
            "score_color": default_bundle.get("score_color", ""),
            "score_verdict_label": default_bundle.get("score_verdict_label", ""),
            "has_peer_comparison": peer_comparison is not None,
        },
        match_ids=(record.match_id for record in records),
    )
    with open_report_store() as store:
        store.save_body(
            player_slug,
            build_slug,
            report=report_payload,
            summary=summary,
            progression_json=progression_json,
            progression_md=progression_md,
        )
    refresh_report_indexes(
        config.output_dir,
        config.template_dir,
        player_dir=config.player_reports_dir,
        player_label=config.players_label,
        assets=asset_catalog,
    )
    report_ref = f"{player_slug}/{build_slug}"
    log.info("Done. Wrote report %s", report_ref)
    return report_ref


def ensure_platform(client: RiotApiClient, records: list[MatchRecord], config: AppConfig) -> None:
    """Pick the league-v4 platform host from match ids or config."""
    if records:
        inferred = RiotApiClient.infer_platform_from_match_id(records[0].match_id)
        if inferred:
            client.set_platform(inferred)
            return
    if config.platform:
        client.set_platform(config.platform)


def run_with_peer(
    config: AppConfig,
    services: Services,
    puuid: str,
    records: list[MatchRecord],
    *,
    ranked: RankedEntry | None = None,
    player_builds: list[dict[str, Any]] | None = None,
    skip_peer: bool = False,
) -> str:
    """Fetch rank, optionally build peer comparison and run the analysis pipeline."""
    if ranked is None:
        ensure_platform(services.client, records, config)
        ranked = services.client.fetch_solo_rank(puuid)
    peer = None
    if not skip_peer:
        matches_df = pd.DataFrame([r.to_row() for r in records])
        peer = build_peer_comparison(
            services.client,
            services.store,
            matches_df,
            records,
            puuid,
            ranked,
            champion=config.champion,
            role=config.role,
            progress=services.progress,
        )
    return run_analysis(
        config,
        records,
        peer_comparison=peer,
        ranked=ranked,
        player_builds=player_builds,
        assets=services.assets,
    )


def prepare_builds(
    services: Services,
    player_contexts: list[PlayerContext],
) -> BuildBatch:
    """Discover eligible builds and parse every stored record once.

    Raises:
        NoEligibleBuildsError: When no champion+lane build qualifies.
    """
    log = get_logger("pipeline")
    puuids = [context.puuid for context in player_contexts]
    primary_puuid = player_contexts[0].puuid

    pools = discover_build_pools(
        services.store,
        puuids,
        services.config,
        min_games=services.config.min_games,
    )
    if not pools:
        raise NoEligibleBuildsError(
            f"No champion+lane reports with at least {services.config.min_games} "
            "ranked games found."
        )

    services.assets.ensure_downloaded()

    account_by_puuid = {context.puuid: context.label for context in player_contexts}
    all_records = load_all_records(services, puuids, account_by_puuid=account_by_puuid)
    # Nav / hub list every eligible build even when analysis is scoped to one.
    manifest_builds: list[dict[str, Any]] = []
    for pool in pools:
        grouped = group_records(all_records, pool.champion, pool.role)
        winrate = float(sum(r.win for r in grouped) / len(grouped)) if grouped else 0.0
        manifest_builds.append(
            build_manifest_entry(
                champion=pool.champion,
                role=pool.role,
                games=len(grouped),
                winrate=winrate,
            )
        )

    analysis_pools = pools
    if services.config.filter_champion:
        analysis_pools = [
            p for p in analysis_pools if p.champion == services.config.filter_champion
        ]
    if services.config.filter_role:
        normalized = services.config.filter_role
        analysis_pools = [p for p in analysis_pools if p.role == normalized]
    if not analysis_pools:
        raise NoEligibleBuildsError(
            f"No champion+lane reports with at least {services.config.min_games} "
            "ranked games found."
        )

    log.info(
        "Found %d eligible build(s) with >= %d games: %s",
        len(analysis_pools),
        services.config.min_games,
        ", ".join(pool.build_label for pool in analysis_pools),
    )
    if len(analysis_pools) < len(pools):
        log.info(
            "Scoped analysis to %d of %d build(s); nav keeps all %d",
            len(analysis_pools),
            len(pools),
            len(manifest_builds),
        )

    # Always keep sibling reports in the Champions nav, even when a build
    # drops below min_games or analysis is scoped to a single refresh.
    manifest_builds = _merge_manifest_with_disk(
        manifest_builds, services.config.reports_group_slug
    )

    return BuildBatch(
        pools=analysis_pools,
        records=all_records,
        manifest_builds=manifest_builds,
        primary_puuid=primary_puuid,
        profile_players=[context.as_player_dict() for context in player_contexts],
    )


def resolve_ranked(
    services: Services, batch: BuildBatch, records: list[MatchRecord]
) -> RankedEntry | None:
    """Pick the platform host and fetch the player's solo queue rank."""
    ensure_platform(services.client, records, services.config)
    return services.client.fetch_solo_rank(batch.primary_puuid)


@dataclass
class BuildAnalysisResult:
    """A build's saved-report reference plus the record-only work behind it.

    ``full_frames``/``report_stats`` depend only on this pool's records, not
    on peer comparison, so a caller that re-analyses the same pool right after
    (the web worker's peer-comparison pass) can pass them straight back in
    and skip redoing the RandomForest/correlation/clustering train.
    """

    path: str | None
    full_frames: AnalysisFrames | None = None
    report_stats: ReportStats | None = None


def analyze_build(
    services: Services,
    batch: BuildBatch,
    pool: BuildPool,
    *,
    ranked: RankedEntry | None,
    peer_comparison: PeerComparisonResult | None,
    still_refining: bool = False,
    full_frames: AnalysisFrames | None = None,
    report_stats: ReportStats | None = None,
) -> BuildAnalysisResult:
    """Run the full analysis + render for one champion+lane build."""
    log = get_logger("pipeline")
    records = group_records(batch.records, pool.champion, pool.role)
    if len(records) < services.config.min_games:
        log.warning("Skipping %s: only %d games after parse", pool.build_label, len(records))
        return BuildAnalysisResult(path=None)
    build_config = services.config.model_copy(
        update={"champion": pool.champion, "role": pool.role}
    )
    build_config.report_dir.mkdir(parents=True, exist_ok=True)
    build_config.run_graphs_dir.mkdir(parents=True, exist_ok=True)
    if full_frames is None or report_stats is None:
        # Same ordering run_analysis applies internally, so a frame/stats pair
        # computed here is interchangeable with one it would have built itself.
        sorted_records = sorted(
            records, key=lambda record: record.game_creation_ms, reverse=True
        )
        if full_frames is None:
            full_frames = build_analysis_frames(sorted_records)
        if report_stats is None:
            report_stats = compute_report_stats(full_frames, build_config.report_dir)
    path = run_analysis(
        build_config,
        records,
        peer_comparison=peer_comparison,
        still_refining=still_refining,
        ranked=ranked,
        player_builds=batch.manifest_builds,
        assets=services.assets,
        profile_players=batch.profile_players,
        full_frames=full_frames,
        report_stats=report_stats,
    )
    return BuildAnalysisResult(path=path, full_frames=full_frames, report_stats=report_stats)


