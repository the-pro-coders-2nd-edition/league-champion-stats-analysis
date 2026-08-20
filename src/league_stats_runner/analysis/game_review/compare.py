"""Per-game comparison rows vs personal baseline."""

from __future__ import annotations

from typing import Any

from league_stats_runner.analysis.game_review.hints import (
    game_review_key_stat_directions_for_role,
    game_review_key_stats_for_role,
)
from league_stats_runner.analysis.progression.metrics import progression_metrics_for_role
from league_stats_common.core.config import GAME_REVIEW_MAX_COMPARISONS
from league_stats_common.core.models import GameComparisonRow
from league_stats_runner.presentation.metric_colors import interpolate_metric_color, score_form_delta


def _verdict(delta: float, direction: str, metric: str, baseline: float) -> str:
    """Classify a gap as above/below/on_par relative to personal baseline."""
    if metric in ("gd10", "cs10", "gd15") and abs(delta) < 30:
        return "on_par"
    threshold = max(abs(baseline) * 0.08, 0.05) if baseline else 0.05
    if direction == "higher":
        if delta > threshold:
            return "above"
        if delta < -threshold:
            return "below"
    else:
        if delta < -threshold:
            return "above"
        if delta > threshold:
            return "below"
    return "on_par"


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


def _is_share_metric(metric: str) -> bool:
    key = metric.lower()
    return (
        "share" in key
        or "participation" in key
        or key.endswith("_rate")
        or key == "objectives_present_rate"
    )


def _is_gold_diff_metric(metric: str) -> bool:
    key = metric.lower()
    return key in {"gd10", "gd15"} or "gold_diff" in key or "gold diff" in key


def _format_delta_display(metric: str, value: float) -> str:
    rounded = _round_comparison(metric, value)
    if _is_share_metric(metric):
        scaled = round(rounded * 100)
        return f"{scaled:+d}%" if scaled != 0 else "0%"
    if _is_gold_diff_metric(metric):
        gold = round(rounded)
        return f"{gold:+d}" if gold != 0 else "0"
    decimals = _comparison_decimals(metric)
    formatted = f"{rounded:.{decimals}f}".rstrip("0").rstrip(".")
    if rounded > 0:
        return f"+{formatted}"
    return formatted if rounded < 0 else "0"


def _delta_is_effectively_zero(metric: str, value: float) -> bool:
    return _format_delta_display(metric, value) in {"0", "+0", "0%", "+0%"}


def _gap_label(metric: str, delta: float, verdict: str) -> str:
    if verdict == "on_par":
        return "same as your avg"
    if _delta_is_effectively_zero(metric, delta):
        return "close to your avg"
    return f"{_format_delta_display(metric, delta)} vs your avg"


def _verdict_label(metric: str, delta: float, verdict: str) -> str:
    if verdict == "on_par":
        return "Same"
    if _delta_is_effectively_zero(metric, delta):
        return "Close"
    if verdict == "above":
        return "Above"
    if verdict == "below":
        return "Below"
    return verdict.replace("_", " ").title()


def _row_for_metric(
    metric: str,
    *,
    label: str,
    game_value: float,
    baseline: float,
    direction: str,
) -> GameComparisonRow:
    delta = game_value - baseline
    verdict = _verdict(delta, direction, metric, baseline)
    rounded_delta = _round_comparison(metric, delta)
    return GameComparisonRow(
        metric=metric,
        label=label,
        game_value=_round_comparison(metric, game_value),
        benchmark_value=_round_comparison(metric, baseline),
        delta=rounded_delta,
        verdict=verdict,
        gap_color=_gap_color(metric, delta, direction, verdict),
        gap_label=_gap_label(metric, rounded_delta, verdict),
        verdict_label=_verdict_label(metric, rounded_delta, verdict),
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
    *,
    role: str,
) -> list[GameComparisonRow]:
    """Baseline delta for every Game Review overview key stat."""
    rows: list[GameComparisonRow] = []
    directions = game_review_key_stat_directions_for_role(role)
    for metric, (label, _) in game_review_key_stats_for_role(role).items():
        game_value = game_row.get(metric)
        baseline = baseline_means.get(metric)
        if game_value is None or baseline is None:
            continue
        direction = directions.get(metric, "higher")
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
