"""Report generation support: improvement score and Mongo-backed report/manifest metadata."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, TYPE_CHECKING

import pandas as pd

from league_stats_runner.analysis.improvement import column_mean, score_categories
from league_stats_common.core.champions import (
    build_label,
    champion_display_name,
    champion_slug,
    role_display,
)
from league_stats_common.core.models import Recommendation
from league_stats_common.infra.report_store import open_report_store
from league_stats_runner.presentation.brand_assets import refresh_saved_report_branding

if TYPE_CHECKING:
    from league_stats_common.infra.ddragon_assets import DDragonAssets


def utc_now_iso() -> str:
    """UTC timestamp for ``generated_at`` (ISO-8601, sortable, JS-parseable)."""
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def game_creation_ms_to_iso(game_creation_ms: int) -> str:
    """Convert a Riot match ``gameCreation`` millisecond timestamp to ISO-8601 UTC."""
    if game_creation_ms <= 0:
        return ""
    return (
        datetime.fromtimestamp(game_creation_ms / 1000, tz=timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def is_group_player_label(player: str) -> bool:
    """True when the label represents a pooled multi-account run."""
    return "," in player


@dataclass(frozen=True)
class ScoreComponent:
    """One category of the improvement score."""

    name: str
    score: float  # 0-100
    value: str
    hint: str


def improvement_score(
    matches_df: pd.DataFrame, *, role: str = "MIDDLE"
) -> tuple[float, list[ScoreComponent]]:
    """Compute the composite improvement score (0-100) and its category components.

    Each component is a lane-dependent category score (Economy, Fight, Laning, …)
    built from several underlying metrics. Benchmarks are role-aware targets
    derived from static tier data; the score tracks progress between runs for
    the same build, not cross-player comparison.

    Args:
        matches_df: Master per-game table.
        role: Riot team position (``TOP``, ``MIDDLE``, ``JUNGLE``, ...).

    Returns:
        Tuple of overall score and the per-category components.
    """
    if matches_df.empty:
        return 0.0, []

    from league_stats_runner.analysis.combat import prefers_cc_over_dpm
    from league_stats_peers.analysis.peer.benchmarks import try_role_benchmark
    from league_stats_common.core.role_metrics import role_profile

    profile = role_profile(role)
    gold = try_role_benchmark("GOLD", role) or {}
    use_cc = prefers_cc_over_dpm(
        role, avg_damage_share=column_mean(matches_df, "damage_share")
    )

    categories = score_categories(
        profile.score_components,
        matches_df,
        gold=gold,
        role=profile.role,
        use_cc=use_cc,
    )
    components = [
        ScoreComponent(name=c.name, score=c.score, value=c.value, hint=c.hint)
        for c in categories
        if math.isfinite(c.score)
    ]
    overall = (
        round(sum(c.score for c in components) / len(components), 1) if components else 0.0
    )
    return overall, components


def build_player_builds_nav(
    builds: list[dict[str, Any]],
    *,
    current_champion: str,
    current_role: str,
    assets: "DDragonAssets | None" = None,
    from_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Build sidebar champion links relative to the current report directory."""
    current_slug = champion_slug(current_champion, current_role)
    nav: list[dict[str, Any]] = []
    for build in builds:
        slug = champion_slug(str(build["champion"]), str(build["role"]))
        winrate = float(build.get("winrate", 0.0))
        riot_id = str(build["champion"])
        icon_href = None
        role_icon = None
        if assets is not None and from_dir is not None:
            icon_href = assets.champion_href(riot_id, from_dir=from_dir)
            role_icon = assets.role_href(str(build["role"]), from_dir=from_dir)
        nav.append(
            {
                "label": (
                    f'{build["build_label"]} · {build["games"]} games · '
                    f"{winrate * 100:.0f}% WR"
                ),
                "build_label": str(build["build_label"]),
                "champion": champion_display_name(riot_id),
                "role": str(build["role"]),
                "role_display": str(build.get("role_display", role_display(str(build["role"])))),
                "games": int(build.get("games", 0)),
                "winrate": winrate,
                "href": f"../{slug}/report.json",
                "selected": slug == current_slug,
                "champion_icon": icon_href,
                "role_icon": role_icon,
            }
        )
    return nav


def build_manifest_entry(
    *,
    champion: str,
    role: str,
    games: int,
    winrate: float,
) -> dict[str, Any]:
    """Create one manifest build entry with a report-relative href."""
    slug = champion_slug(champion, role)
    return {
        "champion": champion,
        "role": role,
        "role_display": role_display(role),
        "build_label": build_label(champion, role),
        "games": games,
        "winrate": round(winrate, 3),
        "href": f"{slug}/report.json",
    }


def save_build_record(
    player_slug: str,
    build_slug: str,
    meta: dict[str, Any],
    *,
    match_ids: Iterable[str] = (),
) -> None:
    """Persist report listing metadata (old ``meta.json``) to Mongo.

    Args:
        player_slug: The player-level report group slug.
        build_slug: The champion+lane build slug within that group.
        meta: Serializable metadata (player, champion, lane, stats...).
        match_ids: Match ids this build was analysed with, so a later
            ``should_skip_unchanged_build`` can tell whether new games are
            already covered without loading the full report body.
    """
    with open_report_store() as store:
        store.save_build(player_slug, build_slug, meta, match_ids=match_ids)


def discover_player_builds(player_slug: str) -> list[dict[str, Any]]:
    """Every completed build report for a player, from Mongo.

    Args:
        player_slug: The player-level report group slug (old
            ``output/reports/{player}/`` directory name).

    Returns:
        Build metadata dicts sorted by game count (most played first).
        Each entry includes an ``href`` relative to the player.
    """
    with open_report_store() as store:
        return store.list_builds(player_slug)


def refresh_report_indexes(
    output_dir: Path,
    template_dir: Path,
    *,
    player_dir: Path | None = None,
    player_label: str | None = None,
    assets: "DDragonAssets | None" = None,
) -> None:
    """Refresh saved-report branding.

    Historically also rebuilt a ``manifest.json`` player hub file; that hub is
    now served live from Mongo (``discover_player_builds``), so there is
    nothing left to precompute here beyond branding assets. Kept as a single
    call site (rather than inlining ``refresh_saved_report_branding`` at every
    caller) so a future post-report-write hook has one place to attach to.

    Args:
        output_dir: Root output directory.
        template_dir: Unused; kept for call-site compatibility.
        player_dir: Unused; kept for call-site compatibility.
        player_label: Unused; kept for call-site compatibility.
        assets: Unused; kept for call-site compatibility.
    """
    _ = template_dir, player_dir, player_label, assets
    refresh_saved_report_branding(output_dir)


def discover_reports() -> list[dict[str, Any]]:
    """Every saved report's listing metadata across every player, from Mongo.

    Returns:
        Report metadata dicts sorted by ``generated_at`` (newest first).
    """
    with open_report_store() as store:
        entries = store.list_all_builds()
    entries.sort(key=lambda entry: entry.get("generated_at", ""), reverse=True)
    return entries


def score_badge(recommendation: Recommendation) -> str:
    """CSS badge class for a recommendation's priority.

    Args:
        recommendation: The recommendation.

    Returns:
        One of ``high``/``medium``/``low``.
    """
    if recommendation.priority >= 2.0:
        return "high"
    if recommendation.priority >= 1.2:
        return "medium"
    return "low"
