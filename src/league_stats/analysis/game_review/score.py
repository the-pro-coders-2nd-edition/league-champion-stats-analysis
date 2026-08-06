"""Personal-baseline game score for a single match."""

from __future__ import annotations

from typing import Any

from league_stats.core.models import GameScoreBreakdown
from league_stats.core.role_metrics import role_profile
from league_stats.presentation.metric_colors import (
    score_deaths_per_game,
    score_form_delta,
)

# Map ingredient columns onto the fixed game-review score dimensions.
_COLUMN_TO_DIMENSION: dict[str, str] = {
    "gd10": "laning",
    "csd10": "laning",
    "deaths_pre14": "laning",
    "cs10": "laning",
    "early_ganks": "laning",
    "roams_pre15": "laning",
    "kp15": "laning",
    "lane_priority": "laning",
    "deaths": "survival",
    "avg_unspent_gold": "survival",
    "first_item_min": "survival",
    "damage_share": "impact",
    "ccpm": "impact",
    "kill_participation": "impact",
    "tf_participation": "impact",
    "tf_won_share": "impact",
    "gold_share": "impact",
    "damage_taken_share": "impact",
    "hpm": "impact",
    "spm": "impact",
    "vspm": "vision",
    "control_wards": "vision",
    "objectives_present_rate": "objectives",
}

_SCORE_DIMENSIONS = ("laning", "survival", "impact", "vision", "objectives")

_LOWER_IS_BETTER = frozenset(
    {"deaths", "avg_unspent_gold", "deaths_pre14", "first_item_min"}
)

# Presence rates jump in large steps (often 0/3, 1/3, 2/3…). The Form Tracker
# ±12pp span maps those straight to 0/100; use a wide personal band instead.
_OBJ_PRESENCE_SPAN = 0.40
_OBJ_PRESENCE_FALLBACK_MID = 0.55
_OBJ_DEAD_BEFORE_SPAN = 1.5
_OBJ_DEAD_BEFORE_FALLBACK_MID = 0.5
_OBJ_PRESENCE_WEIGHT = 0.75
_OBJ_DEAD_BEFORE_WEIGHT = 0.25


def _score_tier(overall: int) -> str:
    if overall >= 90:
        return "S"
    if overall >= 75:
        return "A"
    if overall >= 60:
        return "B"
    if overall >= 45:
        return "C"
    return "D"


def _clamp_unit(value: float) -> float:
    return max(-1.0, min(1.0, value))


def _to_percent_score(raw: float | None) -> int:
    if raw is None:
        return 50
    return max(0, min(100, round((raw + 1.0) * 50)))


def _metric_direction(column: str) -> str:
    return "lower" if column in _LOWER_IS_BETTER else "higher"


def _score_objectives_dimension(
    game_row: dict[str, Any],
    baseline_means: dict[str, float],
) -> int:
    """Softer objectives score for sparse per-game epic-monster samples."""
    rate = game_row.get("objectives_present_rate")
    if rate is None:
        return 50

    mid = float(baseline_means.get("objectives_present_rate", _OBJ_PRESENCE_FALLBACK_MID))
    presence = _clamp_unit((float(rate) - mid) / _OBJ_PRESENCE_SPAN)

    dead = game_row.get("deaths_before_neutral_objective")
    if dead is None:
        return _to_percent_score(presence)

    dead_mid = float(
        baseline_means.get("deaths_before_neutral_objective", _OBJ_DEAD_BEFORE_FALLBACK_MID)
    )
    # Lower deaths-before-objective is better.
    survival = _clamp_unit((dead_mid - float(dead)) / _OBJ_DEAD_BEFORE_SPAN)
    blended = _OBJ_PRESENCE_WEIGHT * presence + _OBJ_DEAD_BEFORE_WEIGHT * survival
    return _to_percent_score(blended)


def _component_score(
    column: str,
    game_value: float | None,
    baseline: float | None,
    *,
    game_row: dict[str, Any],
) -> int:
    if game_value is None:
        return 50
    if column == "objectives_present_rate":
        # Handled by `_score_objectives_dimension` — keep a safe fallback.
        mid = float(baseline) if baseline is not None else _OBJ_PRESENCE_FALLBACK_MID
        return _to_percent_score(_clamp_unit((float(game_value) - mid) / _OBJ_PRESENCE_SPAN))
    if baseline is None:
        if column == "deaths":
            duration = float(game_row.get("duration_min") or 30.0)
            return _to_percent_score(score_deaths_per_game(float(game_value), duration_min=duration))
        return 50

    direction = _metric_direction(column)
    improvement = float(game_value) - float(baseline)
    if direction == "lower":
        improvement = float(baseline) - float(game_value)

    if column in {"gd10", "cs10", "gd15", "xpd10", "csd10"}:
        # score_form_delta applies gold (±300) vs CS (±15) spans.
        return _to_percent_score(score_form_delta(column, float(game_value) - float(baseline)))

    if column == "deaths":
        duration = float(game_row.get("duration_min") or 30.0)
        return _to_percent_score(score_deaths_per_game(float(game_value), duration_min=duration))

    return _to_percent_score(score_form_delta(column, improvement))


def compute_game_score(
    game_row: dict[str, Any],
    baseline_means: dict[str, float],
    *,
    role: str,
) -> GameScoreBreakdown:
    """Score one game against personal baseline means."""
    profile = role_profile(role)
    dimension_scores: dict[str, list[int]] = {key: [] for key in _SCORE_DIMENSIONS}

    for spec in profile.score_components:
        for metric in spec.metrics:
            dimension = _COLUMN_TO_DIMENSION.get(metric.column)
            if dimension not in dimension_scores:
                continue
            if metric.column == "objectives_present_rate":
                # Replaced wholesale below — skip the Form Tracker span path.
                continue
            game_value = game_row.get(metric.column)
            if game_value is None:
                continue
            baseline = baseline_means.get(metric.column)
            dimension_scores[dimension].append(
                _component_score(
                    metric.column, float(game_value), baseline, game_row=game_row
                )
            )

    def dim_avg(key: str) -> int:
        values = dimension_scores[key]
        return round(sum(values) / len(values)) if values else 50

    breakdown = {key: dim_avg(key) for key in _SCORE_DIMENSIONS}
    if game_row.get("objectives_present_rate") is not None:
        breakdown["objectives"] = _score_objectives_dimension(game_row, baseline_means)
    overall = round(sum(breakdown.values()) / len(breakdown))
    return GameScoreBreakdown(
        overall=overall,
        tier=_score_tier(overall),
        **breakdown,
    )
