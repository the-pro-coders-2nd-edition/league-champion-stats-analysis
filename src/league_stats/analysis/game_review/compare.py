"""Per-game comparison rows vs personal baseline."""

from __future__ import annotations

from typing import Any

from league_stats.analysis.game_review.hints import (
    GAME_REVIEW_KEY_STATS,
    GAME_REVIEW_KEY_STAT_DIRECTIONS,
)
from league_stats.analysis.progression.metrics import progression_metrics_for_role
from league_stats.core.config import GAME_REVIEW_MAX_COMPARISONS
from league_stats.core.models import GameComparisonRow
from league_stats.presentation.metric_colors import interpolate_metric_color, score_form_delta


def _verdict(delta: float, direction: str) -> str:
    if abs(delta) < 1e-9:
        return "on_par"
    if direction == "higher":
        return "above" if delta > 0 else "below"
    return "below" if delta > 0 else "above"


def _improvement_delta(delta: float, direction: str) -> float:
    """Positive value means this game beat the baseline on this metric."""
    return delta if direction == "higher" else -delta


def _gap_color(metric: str, delta: float, direction: str, verdict: str) -> str:
    """Red/green intensity scaled by how far this game sits from baseline."""
    if verdict == "on_par":
        return ""
    score = score_form_delta(metric, _improvement_delta(delta, direction))
    if score is None:
        return ""
    return interpolate_metric_color(score)


def _top_rows(rows: list[GameComparisonRow]) -> list[GameComparisonRow]:
    ranked = sorted(rows, key=lambda row: abs(row.delta), reverse=True)
    return ranked[:GAME_REVIEW_MAX_COMPARISONS]


def _comparison_decimals(metric: str) -> int:
    if metric in {
        "win",
        "kill_participation",
        "damage_share",
        "objectives_present_rate",
        "lane_priority",
    } or metric.endswith("_rate"):
        return 2
    if metric in {
        "deaths",
        "deaths_pre14",
        "control_wards",
        "gd10",
        "gd15",
        "cs10",
        "early_ganks",
        "roams_pre15",
        "avg_unspent_gold",
        "solo_deaths",
        "greed_deaths",
        "fights_disadvantaged",
    }:
        return 0
    return 1


def _round_comparison(metric: str, value: float) -> float:
    return round(value, _comparison_decimals(metric))


def _row_for_metric(
    metric: str,
    *,
    label: str,
    game_value: float,
    baseline: float,
    direction: str,
) -> GameComparisonRow:
    delta = game_value - baseline
    verdict = _verdict(delta, direction)
    return GameComparisonRow(
        metric=metric,
        label=label,
        game_value=_round_comparison(metric, game_value),
        benchmark_value=_round_comparison(metric, baseline),
        delta=_round_comparison(metric, delta),
        verdict=verdict,
        gap_color=_gap_color(metric, delta, direction, verdict),
    )


def compare_to_baseline(
    game_row: dict[str, Any],
    baseline_means: dict[str, float],
    *,
    role: str,
) -> list[GameComparisonRow]:
    """Compare one game to personal baseline means."""
    rows: list[GameComparisonRow] = []
    for spec in progression_metrics_for_role(role):
        if spec.source != "matches_df":
            continue
        game_value = game_row.get(spec.metric)
        baseline = baseline_means.get(spec.metric)
        if game_value is None or baseline is None:
            continue
        rows.append(
            _row_for_metric(
                spec.metric,
                label=spec.label,
                game_value=float(game_value),
                baseline=float(baseline),
                direction=spec.direction,
            )
        )
    return _top_rows(rows)


def compare_key_stats_to_baseline(
    game_row: dict[str, Any],
    baseline_means: dict[str, float],
) -> list[GameComparisonRow]:
    """Baseline delta for every Game Review overview key stat."""
    rows: list[GameComparisonRow] = []
    for metric, (label, _) in GAME_REVIEW_KEY_STATS.items():
        game_value = game_row.get(metric)
        baseline = baseline_means.get(metric)
        if game_value is None or baseline is None:
            continue
        direction = GAME_REVIEW_KEY_STAT_DIRECTIONS.get(metric, "higher")
        rows.append(
            _row_for_metric(
                metric,
                label=label,
                game_value=float(game_value),
                baseline=float(baseline),
                direction=direction,
            )
        )
    return rows
