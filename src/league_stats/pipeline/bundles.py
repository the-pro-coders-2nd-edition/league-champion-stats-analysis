"""Queue/window slicing and dashboard bundle assembly."""

from __future__ import annotations

import json
import math
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pandas as pd

from league_stats.analysis.career.engine import advance_career
from league_stats.analysis.career.tracks import TrackContext
from league_stats.analysis.coach.engine import VISIBLE_RECOMMENDATIONS
from league_stats.analysis.deaths import blind_spot_zones
from league_stats.analysis.items import build_path_stats
from league_stats.analysis.matchups import matchup_recommendation
from league_stats.analysis.peer import peer_comparison_for_window
from league_stats.analysis.runes import rune_setup_stats
from league_stats.analysis.positioning import ROLE_COLUMNS
from league_stats.analysis.statistics import StatisticsEngine, feature_label
from league_stats.core.champions import player_slug, role_display
from league_stats.core.config import (
    DEFAULT_GAME_WINDOW,
    DEFAULT_QUEUE_FILTER,
    GAME_WINDOW_OPTIONS,
    QUEUE_FILTER_OPTIONS,
    QUEUE_LABELS,
    RANKED_FLEX_QUEUE_ID,
    RANKED_SOLO_QUEUE_ID,
    AppConfig,
)
from league_stats.core.models import MatchRecord, PeerComparisonResult
from league_stats.infra.career_store import CareerStore, build_key
from league_stats.infra.ddragon_assets import DDragonAssets
from league_stats.presentation.career import build_career_view, empty_career_view
from league_stats.pipeline.frames import AnalysisFrames, build_analysis_frames, build_overview
from league_stats.utils import get_logger
from league_stats.pipeline.summaries import ReportStats, build_domain_summaries, generate_recommendations
from league_stats.core.role_metrics import role_profile
from league_stats.pipeline.view_models import (
    annotate_card_tiers,
    annotate_matchup_rows,
    card,
    card_entries,
    cards_from_specs,
    format_map_distance,
    overview_card_entries,
    peer_row_display,
    peer_subtitle,
    pct,
    priority_label,
)
from league_stats.presentation.graphs import ChartIconResolver, GraphFactory
from league_stats.presentation.report import improvement_score, score_badge
from league_stats.presentation.tones import p_value as tone_p_value
from league_stats.presentation.tones import priority_tone as tone_priority_tone
from league_stats.presentation.tones import verdict as tone_verdict
from league_stats.presentation.ui_icons import attach_metric_icon_hrefs, icon_fields_for_label, tooltip_for_label


def filter_records_by_queue(records: list[MatchRecord], key: str) -> list[MatchRecord]:
    """Return records for one queue filter key (``solo``, ``flex``, or ``all``)."""
    if key == "solo":
        return [record for record in records if record.queue_id == RANKED_SOLO_QUEUE_ID]
    if key == "flex":
        return [record for record in records if record.queue_id == RANKED_FLEX_QUEUE_ID]
    return records


def filter_records_by_accounts(
    records: list[MatchRecord], accounts: set[str] | None
) -> list[MatchRecord]:
    """Return records played on the given ``RiotID#Tag`` accounts.

    ``None`` or an empty set means no filtering. Labels are compared
    case-insensitively because cached match payloads may differ in casing.
    """
    if not accounts:
        return records
    wanted = {label.casefold() for label in accounts}
    return [
        record for record in records if (record.account or "").casefold() in wanted
    ]


def queue_filter_options(solo_count: int, flex_count: int) -> list[dict[str, Any]]:
    """Toggle metadata for the queue filter bar."""
    total = solo_count + flex_count
    return [
        {"key": "solo", "label": QUEUE_LABELS["solo"], "enabled": solo_count > 0},
        {"key": "flex", "label": QUEUE_LABELS["flex"], "enabled": flex_count > 0},
        {"key": "all", "label": QUEUE_LABELS["all"], "enabled": total > 0},
    ]


def default_queue_filter_key(solo_count: int, flex_count: int) -> str:
    """Pick the initial queue filter, preferring solo when available."""
    if solo_count > 0:
        return DEFAULT_QUEUE_FILTER
    if flex_count > 0:
        return "flex"
    return "all"


def slice_records(records: list[MatchRecord], limit: int | None) -> list[MatchRecord]:
    """Return the most recent ``limit`` games, or all when ``limit`` is ``None``."""
    if limit is None:
        return records
    return records[:limit]


def default_game_window_key(total_games: int) -> str:
    """Pick the initial dashboard window."""
    if total_games >= DEFAULT_GAME_WINDOW:
        return str(DEFAULT_GAME_WINDOW)
    return "all"


def serialize_report_views_json(report_views: dict[str, dict[str, Any]]) -> str:
    """JSON-encode queue/window snapshots for safe embedding in a ``<script>`` tag."""
    encoded = json.dumps(report_views, default=str)
    return encoded.replace("</", r"<\/")


def game_window_options(total_games: int) -> list[dict[str, Any]]:
    """Toggle metadata for the report template."""
    options = [
        {"key": str(size), "label": f"Last {size}", "enabled": total_games >= size}
        for size in GAME_WINDOW_OPTIONS
    ]
    options.append({"key": "all", "label": "All", "enabled": True})
    return options


def _evidence_summary(rec: Any) -> str:
    """Plain-language confidence sentence for a recommendation."""
    sample = int(rec.sample_size or 0)
    p_value = rec.p_value
    if p_value is not None and p_value <= 0.01:
        strength = "a very strong pattern"
    elif p_value is not None and p_value <= 0.05:
        strength = "a strong pattern"
    elif p_value is not None:
        strength = "an early signal"
    else:
        strength = "a consistent pattern"
    games = f"across {sample} of your games" if sample else "in your games"
    return f"This is {strength} {games}."


def _evidence_rows(rec: Any) -> list[dict[str, Any]]:
    """Key/value evidence rows for a recommendation's Evidence disclosure."""
    rows: list[dict[str, Any]] = [{"label": "Evidence", "value": rec.evidence}]
    sample = int(rec.sample_size or 0)
    if sample:
        rows.append({"label": "Sample", "value": f"{sample} games"})
    rows.append({"label": "Significance", "value": tone_p_value(rec.p_value)})
    return rows


def _recommendation_payload(rec: Any) -> dict[str, Any]:
    """Serialize one coaching recommendation for HTML/JSON views."""
    badge = score_badge(rec)
    label = priority_label(badge)
    side = "keep" if rec.tone.value == "positive" else "work"
    return {
        **rec.model_dump(),
        "badge": badge,
        "priority_label": label,
        "priority_tone": tone_priority_tone(label, side),
        "tone": rec.tone.value,
        "confidence": tone_p_value(rec.p_value),
        "evidence_summary": _evidence_summary(rec),
        "evidence_rows": _evidence_rows(rec),
    }


def _finalize_coaching_anchors(bundle: dict[str, Any]) -> None:
    """Assign stable anchor ids to coaching tips by global priority."""
    recs = [
        *bundle.get("positive_recommendations", []),
        *bundle.get("negative_recommendations", []),
    ]
    ranked = sorted(recs, key=lambda rec: rec.get("priority", 0), reverse=True)
    for index, rec in enumerate(ranked):
        rec["anchor"] = f"coaching-tip-{index}"


def _build_top_tips(bundle: dict[str, Any], *, limit: int = 3) -> list[dict[str, Any]]:
    """Return next-game focus tips for the overview hero.

    Prefers fix tips (negative tone). Falls back to strengths only when there
    aren't enough weaknesses. Hero copy uses ``action`` when present.
    """
    key = lambda rec: rec.get("priority", 0)
    negatives = sorted(bundle.get("negative_recommendations", []), key=key, reverse=True)
    positives = sorted(bundle.get("positive_recommendations", []), key=key, reverse=True)
    tips = list(negatives[:limit])
    if len(tips) < limit:
        tips.extend(positives[: limit - len(tips)])
    return tips


_CHIP_STRONG_SCORE = 65.0
_CHIP_FOCUS_SCORE = 45.0


def _overall_score_verdict(score: float) -> tuple[str, str]:
    """CSS color + verdict label for the top-level Improvement score."""
    if score >= _CHIP_STRONG_SCORE:
        return "var(--tone-good-fg)", "Strength"
    if score >= _CHIP_FOCUS_SCORE:
        return "var(--color-text)", "Solid"
    return "var(--tone-bad-fg)", "Focus"


def _annotate_score_components(
    score_components: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach the Strength / Solid / Watch / Focus tone + label to each score card."""
    for comp in score_components:
        raw_score = float(comp.get("score", 0.0))
        score = raw_score if math.isfinite(raw_score) else 0.0
        label, tone = tone_verdict(score)
        comp["tone"] = tone
        comp["verdict"] = label
    return score_components


# Deep Dive section id -> improvement-score category names (first match wins).
_SECTION_SCORE_CATEGORIES: dict[str, tuple[str, ...]] = {
    "lane": ("Laning", "Early game", "Setup"),
    "objectives": ("Objectives",),
    "deaths": ("Survival",),
    "vision": ("Vision",),
    "economy": ("Economy",),
    "teamfights": ("Fight", "Utility"),
}

# Coaching categories -> Deep Dive section id, for verdict enrichment.
_REC_CATEGORY_SECTIONS: dict[str, str] = {
    "Laning": "lane",
    "Economy": "economy",
    "Items": "economy",
    "Vision": "vision",
    "Deaths": "deaths",
    "Teamfights": "teamfights",
    "Objectives": "objectives",
    "Macro": "deaths",
    "Positioning": "teamfights",
}


def _score_verdict_sentence(
    name: str, score: float, *, is_biggest_gap: bool
) -> tuple[str, str, str]:
    """Base verdict sentence, tone, and short label for one section score."""
    if score >= _CHIP_STRONG_SCORE:
        return f"{name} is a clear strength.", "strong", "Strength"
    if score >= _CHIP_FOCUS_SCORE:
        return f"{name} is solid.", "solid", "Solid"
    if is_biggest_gap:
        return f"{name} is your biggest room to improve.", "focus", "Focus"
    return f"{name} needs work.", "focus", "Focus"


def _build_section_verdicts(bundle: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Score callout + optional coaching-link fix per Deep Dive section."""
    components = {
        str(comp.get("name", "")): comp for comp in bundle.get("score_components", [])
    }
    top_fix_by_section: dict[str, dict[str, Any]] = {}
    for rec in bundle.get("negative_recommendations", []):
        section = _REC_CATEGORY_SECTIONS.get(str(rec.get("category", "")))
        if not section:
            continue
        current = top_fix_by_section.get(section)
        if current is None or rec.get("priority", 0) > current.get("priority", 0):
            top_fix_by_section[section] = rec

    section_comps: list[tuple[str, dict[str, Any], float]] = []
    for section, names in _SECTION_SCORE_CATEGORIES.items():
        comp = next((components[name] for name in names if name in components), None)
        if comp is None:
            continue
        section_comps.append((section, comp, float(comp.get("score", 0.0))))

    # Only the single weakest section gets "biggest room to improve".
    biggest_gap_section = ""
    if section_comps:
        biggest_gap_section = min(section_comps, key=lambda item: item[2])[0]

    verdicts: dict[str, dict[str, Any]] = {}
    for section, comp, score in section_comps:
        safe_score = score if math.isfinite(score) else 0.0
        text, tone, label = _score_verdict_sentence(
            str(comp.get("name", "")),
            safe_score,
            is_biggest_gap=(section == biggest_gap_section),
        )
        payload: dict[str, Any] = {
            "text": text,
            "tone": tone,
            "label": label,
            "score": round(safe_score),
            "name": str(comp.get("name", "")),
            "hint": str(comp.get("hint") or ""),
            "value": str(comp.get("value") or ""),
        }
        fix = top_fix_by_section.get(section)
        if fix is not None and (fix.get("action") or fix.get("title")):
            payload["fix"] = {
                "title": str(fix.get("action") or fix.get("title") or ""),
                "anchor": str(fix.get("anchor") or "coaching"),
            }
        verdicts[section] = payload
    return verdicts


def _attach_peer_benchmarks(
    card_lists: list[list[dict[str, Any]]], peer_payload: dict[str, Any]
) -> None:
    """Add 'vs peers' benchmark sub-lines to headline cards with a peer row."""
    rows_by_metric = {
        str(row.get("metric", "")): row
        for row in peer_payload.get("rows", [])
        if row.get("metric")
    }
    if not rows_by_metric:
        return
    tier = str(peer_payload.get("tier") or "").strip()
    peers_label = f"{tier.title()} peers" if tier else "rank peers"
    for entries in card_lists:
        for entry in entries:
            if entry.get("tier") != "headline":
                continue
            metric = str(entry.get("metric", ""))
            if not metric:
                continue
            row = rows_by_metric.get(metric)
            if row is None or row.get("verdict") == "inline":
                continue
            entry["benchmark"] = f"{row['gap']} vs {peers_label}"
            entry["benchmark_tone"] = row.get("verdict", "inline")
            entry["benchmark_color"] = row.get("gap_color", "")


def _build_figure_hints(
    win_corrs: list[Any],
    model: Any,
) -> dict[str, str]:
    """Short chart takeaways for the statistical deep-dive section."""
    hints: dict[str, str] = {}
    if win_corrs:
        top = win_corrs[0]
        hints["win_correlation_bar"] = (
            f"Strongest link with wins: {feature_label(top.feature)} (r = {top.correlation:+.2f})"
        )
    if getattr(model, "trained", False) and not model.feature_importance.empty:
        top_row = model.feature_importance.iloc[0]
        hints["feature_importance"] = f"Top early-game predictor: {feature_label(top_row['feature'])}"
    return hints


def _build_positioning_cards(
    positioning: dict[str, Any],
    player_role: str,
    *,
    assets: DDragonAssets | None = None,
    from_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Build positioning section cards with optional role icons on ally rows."""
    pairs: list[tuple[str, str]] = [
        ("Grouped with team", card(pct(positioning.get("avg_grouped_share")))),
        ("Solo on map", card(pct(positioning.get("avg_solo_share")))),
        ("Side-lane time", card(pct(positioning.get("avg_side_lane_share")))),
        ("Allies nearby", card(positioning.get("avg_allies_nearby"))),
        ("Avg teammate dist", format_map_distance(positioning.get("avg_teammate_distance"))),
    ]
    for role, column in ROLE_COLUMNS.items():
        if role == player_role:
            continue
        pairs.append(
            (f"Dist to {role_display(role)}", format_map_distance(positioning.get(column)))
        )
    entries = card_entries(pairs)
    if assets is not None and from_dir is not None:
        role_by_label = {
            f"Dist to {role_display(role)}": role
            for role in ROLE_COLUMNS
            if role != player_role
        }
        for entry in entries:
            role = role_by_label.get(entry["label"])
            if not role:
                continue
            href = assets.role_href(role, from_dir=from_dir)
            if href:
                entry["role_icon_href"] = href
    return annotate_card_tiers(entries, "positioning")


def _peer_p75(peer_comparison: PeerComparisonResult | None) -> dict[str, float]:
    """Peer 75th percentiles per metric, empty until peer sampling lands them."""
    if peer_comparison is None:
        return {}
    return {
        row.metric: float(row.peer_p75)
        for row in peer_comparison.comparisons
        if row.peer_p75 is not None
    }


def build_career_bundle(
    config: AppConfig,
    frames: AnalysisFrames,
    peer_comparison: PeerComparisonResult | None,
    components: list[Any],
) -> dict[str, Any]:
    """Advance this build's Career ladder and shape it for the templates.

    A Career failure must never cost the reader their whole report, so anything
    unexpected degrades to the empty view with a warning.
    """
    try:
        ctx = TrackContext(
            matches_df=frames.matches_df,
            objectives_df=frames.objectives_df,
            role=config.role,
            peer_p75=_peer_p75(peer_comparison),
        )
        key = build_key(player_slug(config.riot_id, config.tagline), config.champion, config.role)
        with CareerStore(config.career_db_path) as store:
            snapshot = advance_career(store, key, ctx, components)
        return build_career_view(snapshot)
    except Exception as exc:  # noqa: BLE001 - a broken ladder must not break the report
        get_logger("career").warning("Career mode skipped: %s", exc)
        return empty_career_view()


def bundle_to_template_context(
    bundle: dict[str, Any],
    *,
    peer_comparison: PeerComparisonResult | None = None,
) -> dict[str, Any]:
    """Map a window bundle onto the Jinja template field names."""
    context: dict[str, Any] = {
        "total_games": bundle["total_games"],
        "patch_range": bundle["patch_range"],
        "queue_label": bundle.get("queue_label", "ranked solo queue"),
        "overview": bundle["overview"],
        "score": bundle["score"],
        "score_components": bundle["score_components"],
        "figures": bundle["figures"],
        "overview_cards": bundle.get("overview_cards", []),
        "section_verdicts": bundle.get("section_verdicts", {}),
        "lane_cards": bundle["lane_cards"],
        "early_section_title": bundle.get("early_section_title", "Laning"),
        "section_order": bundle.get("section_order", []),
        "economy_cards": bundle["economy_cards"],
        "vision_cards": bundle["vision_cards"],
        "death_cards": bundle["death_cards"],
        "positioning_cards": bundle.get("positioning_cards", []),
        "positioning_hints": bundle.get("positioning_hints", []),
        "teamfight_cards": bundle["teamfight_cards"],
        "objective_macro_cards": bundle.get("objective_macro_cards", []),
        "objective_rows": bundle["objective_rows"],
        "objectives_section_icon": bundle.get("objectives_section_icon"),
        "blind_spots": bundle["blind_spots"],
        "build_paths": bundle["build_paths"],
        "rune_rows": bundle["rune_rows"],
        "matchup_rows": bundle["matchup_rows"],
        "positive_recommendations": bundle["positive_recommendations"],
        "negative_recommendations": bundle["negative_recommendations"],
        "weak_recommendations": bundle.get("weak_recommendations", []),
        "top_tips": bundle.get("top_tips", []),
        "figure_hints": bundle.get("figure_hints", {}),
        "career": bundle.get("career") or empty_career_view(),
        "has_peer_comparison": peer_comparison is not None,
    }
    if peer_comparison is not None:
        context["peer_comparison"] = peer_comparison
        context["peer_rows"] = [peer_row_display(row.model_dump()) for row in peer_comparison.comparisons]
        peer_payload = bundle.get("peer") or {}
        if peer_payload.get("rank_icon"):
            context["peer_rank_icon"] = peer_payload["rank_icon"]
    return context


def build_window_bundle(
    config: AppConfig,
    records: list[MatchRecord],
    graphs_dir: Path,
    *,
    peer_comparison: PeerComparisonResult | None = None,
    queue_label: str = "ranked solo queue",
    assets: DDragonAssets | None = None,
    shared_stats: ReportStats | None = None,
) -> dict[str, Any]:
    """Run the analysis pipeline for one game window and return a JSON bundle."""
    empty: dict[str, Any] = {
        "total_games": 0,
        "patch_range": "—",
        "queue_label": queue_label,
        "overview": {},
        "overview_cards": [],
        "section_verdicts": {},
        "score": 0,
        "score_color": "var(--color-text)",
        "score_verdict_label": "Solid",
        "score_components": [],
        "lane_cards": [],
        "early_section_title": "Laning",
        "section_order": [],
        "economy_cards": [],
        "vision_cards": [],
        "death_cards": [],
        "positioning_cards": [],
        "positioning_hints": [],
        "teamfight_cards": [],
        "objective_rows": [],
        "objective_macro_cards": [],
        "blind_spots": [],
        "build_paths": [],
        "rune_rows": [],
        "matchup_rows": [],
        "positive_recommendations": [],
        "negative_recommendations": [],
        "weak_recommendations": [],
        "top_tips": [],
        "figure_hints": {},
        "figures": {},
        "career": empty_career_view(),
    }
    if not records:
        return empty

    frames = build_analysis_frames(records)
    summaries = build_domain_summaries(frames, records)
    overview = summaries["overview"]
    lane = summaries["laning"]
    economy = summaries["economy"]
    resets = summaries["resets"]
    vision = summaries["vision"]
    deaths_agg = summaries["deaths"]
    positioning_agg = summaries["positioning"]
    fights = summaries["teamfights"]
    objectives_agg = summaries["objectives"]

    slice_stats = StatisticsEngine(frames.matches_df, graphs_dir, role=config.role)
    corr = slice_stats.correlation_matrix()
    win_corrs = slice_stats.win_correlations()
    clusters_df = slice_stats.cluster_games()
    model = shared_stats.model if shared_stats is not None else slice_stats.train_win_predictor()

    window_peer = peer_comparison
    if peer_comparison is not None:
        window_peer = peer_comparison_for_window(peer_comparison, frames.matches_df, records)

    recommendations = generate_recommendations(
        frames,
        slice_stats,
        config,
        peer_comparison=window_peer,
        records_count=len(records),
    )

    score, components = improvement_score(frames.matches_df, role=config.role)
    career = build_career_bundle(config, frames, window_peer, components)
    matchups_export = frames.matchups_df.copy()
    if not matchups_export.empty:
        matchups_export["recommendation"] = matchups_export.apply(
            lambda row: matchup_recommendation(row, role=config.role),
            axis=1,
        )
    matchup_rows = annotate_matchup_rows(
        matchups_export.head(20).to_dict("records") if not matchups_export.empty else [],
        role=config.role,
    )

    icon_resolver = None
    if assets is not None:
        icon_resolver = ChartIconResolver(
            from_dir=graphs_dir.parent,
            champion_href=assets.champion_chart_source,
            item_href=assets.item_chart_source,
            keystone_href=assets.keystone_chart_source,
            map_source=assets.map_chart_source,
            map_path=assets.map_icon_path(),
        )
    graphs = GraphFactory(graphs_dir, icon_resolver=icon_resolver)
    series = [(r.win, r.timeline.gold_series, r.timeline.opp_gold_series) for r in records]
    figures = {
        "gold_diff_timeline": graphs.gold_diff_timeline(series),
        "gd10_histogram": graphs.gd10_histogram(frames.matches_df),
        "deaths_box": graphs.deaths_box(frames.matches_df),
        "cs10_violin": (
            ""
            if config.role.upper() == "UTILITY"
            else graphs.cs10_violin(frames.matches_df)
        ),
        "dpm_scatter": graphs.dpm_scatter(frames.matches_df),
        "vision_trend": graphs.vision_trend(frames.matches_df),
        "death_heatmap": graphs.death_heatmap(frames.deaths_df),
        "correlation_heatmap": graphs.correlation_heatmap(corr),
        "win_correlation_bar": graphs.win_correlation_bar(win_corrs),
        "feature_importance": graphs.feature_importance(model),
        "cluster_scatter": graphs.cluster_scatter(clusters_df),
        "matchup_bar": graphs.matchup_bar(frames.matchups_df),
        "item_winrate_bar": graphs.item_winrate_bar(frames.items_df),
        "rune_winrate_bar": graphs.rune_winrate_bar(rune_setup_stats(frames.runes_df)),
        "objective_timing": graphs.objective_timing(frames.objectives_df),
    }

    profile = role_profile(config.role)
    avg_damage_share = overview.get("avg_damage_share")
    if avg_damage_share is not None:
        avg_damage_share = float(avg_damage_share)
    summary_buckets = {
        "overview": overview,
        "laning": lane,
        "economy": economy,
        "resets": resets,
        "vision": vision,
        "deaths": deaths_agg,
        "teamfights": fights,
        "positioning": positioning_agg,
        "jungle": summaries.get("jungle", {}),
        "utility": summaries.get("utility", {}),
        "split_push": summaries.get("split_push", {}),
    }

    bundle: dict[str, Any] = {
        "total_games": len(records),
        "patch_range": (
            f"{frames.matches_df['patch'].min()} – {frames.matches_df['patch'].max()}"
            if not frames.matches_df.empty
            else "—"
        ),
        "queue_label": queue_label,
        "overview": overview,
        "overview_cards": overview_card_entries(
            overview,
            role=config.role,
            split_push=summaries.get("split_push", {}),
        ),
        "early_section_title": profile.early_section_title,
        "section_order": list(profile.section_order),
        "score": score,
        "score_color": _overall_score_verdict(score)[0],
        "score_verdict_label": _overall_score_verdict(score)[1],
        "score_components": [
            {
                **asdict(component),
                **icon_fields_for_label(component.name),
                **({"tooltip": tooltip} if (tooltip := tooltip_for_label(component.name)) else {}),
            }
            for component in components
        ],
        "lane_cards": cards_from_specs(
            profile.early_game,
            summary_buckets,
            section="early",
            role=config.role,
            avg_damage_share=avg_damage_share,
        ),
        "economy_cards": cards_from_specs(
            profile.economy,
            summary_buckets,
            section="economy",
            role=config.role,
            avg_damage_share=avg_damage_share,
        ),
        "vision_cards": cards_from_specs(
            profile.vision,
            summary_buckets,
            section="vision",
            role=config.role,
            avg_damage_share=avg_damage_share,
        ),
        "death_cards": cards_from_specs(
            profile.deaths,
            summary_buckets,
            section="deaths",
            role=config.role,
            avg_damage_share=avg_damage_share,
        ),
        "teamfight_cards": cards_from_specs(
            profile.teamfights,
            summary_buckets,
            section="teamfight",
            role=config.role,
            avg_damage_share=avg_damage_share,
        ),
        "objective_macro_cards": cards_from_specs(
            profile.objectives,
            summary_buckets,
            section="objectives",
            role=config.role,
            avg_damage_share=avg_damage_share,
        )
        if profile.objectives
        else [],
        "positioning_cards": _build_positioning_cards(
            positioning_agg,
            config.role,
            assets=assets,
            from_dir=graphs_dir.parent if assets is not None else None,
        ),
        "positioning_hints": positioning_agg.get("hints", []),
        "objective_rows": [
            {
                "kind": kind,
                "count": row.get("count"),
                "taken_rate": row.get("taken_rate"),
                "presence_rate": row.get("presence_rate"),
                "early_rate": row.get("early_rate"),
                "dead_before_rate": row.get("dead_before_rate"),
                "avg_wards_before": row.get("avg_wards_before"),
            }
            for kind, row in sorted(objectives_agg.get("by_kind", {}).items())
        ],
        "blind_spots": blind_spot_zones(frames.deaths_df),
        "build_paths": build_path_stats(frames.matches_df).head(10).to_dict("records"),
        "rune_rows": rune_setup_stats(frames.runes_df).to_dict("records"),
        "matchup_rows": matchup_rows,
        "figures": figures,
        "career": career,
    }
    recommendation_payloads = [_recommendation_payload(rec) for rec in recommendations]
    bundle["weak_recommendations"] = [
        payload for payload in recommendation_payloads if payload["badge"] == "low"
    ]
    bundle["positive_recommendations"] = [
        payload
        for payload in recommendation_payloads
        if payload["badge"] != "low" and payload["tone"] == "positive"
    ]
    bundle["negative_recommendations"] = [
        payload
        for payload in recommendation_payloads
        if payload["badge"] != "low" and payload["tone"] == "negative"
    ]
    _finalize_coaching_anchors(bundle)
    bundle["top_tips"] = _build_top_tips(bundle)
    bundle["figure_hints"] = _build_figure_hints(win_corrs, model)
    _annotate_score_components(bundle["score_components"])
    bundle["section_verdicts"] = _build_section_verdicts(bundle)

    if window_peer is not None:
        peer_payload: dict[str, Any] = {
            "subtitle": peer_subtitle(window_peer),
            "rank_label": window_peer.rank_label,
            "rank_badge": window_peer.rank_badge or window_peer.rank_label,
            "build_label": window_peer.build_label,
            "source": window_peer.source,
            "peer_games": window_peer.peer_games,
            "peer_players": window_peer.peer_players,
            "tier": window_peer.tier,
            "confidence": window_peer.confidence,
            "strengths": window_peer.strengths,
            "weaknesses": window_peer.weaknesses,
            "rows": [peer_row_display(row.model_dump()) for row in window_peer.comparisons],
        }
        if assets is not None and window_peer.tier:
            assets.ensure_rank_emblem(window_peer.tier)
            rank_icon = assets.rank_emblem_href(
                window_peer.tier, from_dir=graphs_dir.parent
            )
            if rank_icon:
                peer_payload["rank_icon"] = rank_icon
        bundle["peer"] = peer_payload
        bundle["_peer_result"] = window_peer
        _attach_peer_benchmarks(
            [
                bundle["overview_cards"],
                bundle["lane_cards"],
                bundle["economy_cards"],
                bundle["vision_cards"],
                bundle["death_cards"],
                bundle["positioning_cards"],
                bundle["teamfight_cards"],
                bundle["objective_macro_cards"],
            ],
            peer_payload,
        )

    if assets is not None:
        from_dir = graphs_dir.parent
        bundle["rune_rows"] = assets.enrich_rune_rows(bundle["rune_rows"], from_dir=from_dir)
        bundle["matchup_rows"] = assets.enrich_matchup_rows(bundle["matchup_rows"], from_dir=from_dir)
        bundle["objective_rows"] = assets.enrich_objective_rows(bundle["objective_rows"], from_dir=from_dir)
        bundle["build_paths"] = assets.enrich_build_path_rows(bundle["build_paths"], from_dir=from_dir)
        metric_lists = [
            bundle["overview_cards"],
            bundle["lane_cards"],
            bundle["economy_cards"],
            bundle["vision_cards"],
            bundle["death_cards"],
            bundle["positioning_cards"],
            bundle["teamfight_cards"],
            bundle["score_components"],
        ]
        peer = bundle.get("peer")
        if peer and peer.get("rows"):
            metric_lists.append(peer["rows"])
        for entries in metric_lists:
            attach_metric_icon_hrefs(entries, assets, from_dir=from_dir)

    return bundle


# Re-export for template context consumers
VISIBLE_RECOMMENDATIONS_COUNT = VISIBLE_RECOMMENDATIONS
QUEUE_FILTER_KEYS = QUEUE_FILTER_OPTIONS
