"""Personal-baseline game score for a single match."""

from __future__ import annotations

from typing import Any

from league_stats_runner.analysis.improvement import (
    is_meaningful_healing,
    is_meaningful_shielding,
)
from league_stats_runner.analysis.statistics import feature_label
from league_stats_common.core.models import (
    GameScoreBreakdown,
    GameScoreDimension,
    GameScoreIngredient,
)
from league_stats_common.core.role_metrics import ScoreMetricSpec, role_profile
from league_stats_runner.presentation.metric_colors import normalize_count_for_duration

# Soft objectives scoring — sparse per-game epic samples need a wide band.
_OBJ_PRESENCE_SPAN = 0.40
_OBJ_PRESENCE_FALLBACK_MID = 0.55
_OBJ_ACCOUNTED_SPAN = 0.40
_OBJ_ACCOUNTED_FALLBACK_MID = 0.55
_OBJ_UNPRODUCTIVE_SPAN = 0.35
_OBJ_DEAD_BEFORE_SPAN = 1.5
_OBJ_DEAD_BEFORE_FALLBACK_MID = 0.5
_OBJ_ACCOUNTED_WEIGHT = 0.60
_OBJ_UNPRODUCTIVE_WEIGHT = 0.15
_OBJ_DEAD_BEFORE_WEIGHT = 0.25
_REFERENCE_GAME_MIN = 30.0

# Tighter than Form Tracker so a clearly good/bad game can leave the 35–65 band.
_GAME_REVIEW_SPANS: dict[str, float] = {
    "gd10": 200.0,
    "gd15": 200.0,
    "xpd10": 200.0,
    "cs10": 10.0,
    "csd10": 10.0,
    "deaths": 1.5,
    "deaths_pre14": 1.25,
    "avg_unspent_gold": 250.0,
    "first_item_min": 1.5,
    "kill_participation": 0.10,
    "damage_share": 0.08,
    "gold_share": 0.05,
    "damage_taken_share": 0.08,
    "tf_participation": 0.12,
    "tf_won_share": 0.12,
    "lane_priority": 0.10,
    "objectives_present_rate": 0.10,
    "objectives_accounted_for_rate": 0.10,
    "unproductive_absence_rate": 0.10,
    "vspm": 0.8,
    "control_wards": 1.25,
    "roams_pre15": 1.25,
    "early_ganks": 1.25,
    "ccpm": 0.4,
    "hpm": 80.0,
    "spm": 80.0,
    "kp15": 0.10,
}

# Supports post much higher vision numbers; widen bands so routine ward output
# does not inflate Vision. ~4.0 VS/min is world-class — reserve top scores for that tier.
_UTILITY_GAME_REVIEW_SPANS: dict[str, float] = {
    "vspm": 2.0,
    "control_wards": 2.5,
}

_RATE_METRICS = frozenset(
    {
        "kill_participation",
        "damage_share",
        "gold_share",
        "damage_taken_share",
        "tf_participation",
        "tf_won_share",
        "lane_priority",
        "objectives_present_rate",
        "kp15",
    }
)

# Game-total counts that grow with match length — compare on a 30-minute basis.
# Shares, per-minute rates, and fixed early-window stats are left alone.
_DURATION_COUNT_METRICS = frozenset(
    {
        "deaths",
        "control_wards",
        "deaths_before_neutral_objective",
        "solo_deaths",
        "greed_deaths",
        "wards_placed",
        "wards_killed",
    }
)

_LOWER_IS_BETTER = frozenset(
    {
        "deaths",
        "avg_unspent_gold",
        "deaths_pre14",
        "first_item_min",
        "deaths_before_neutral_objective",
    }
)

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


def _metric_direction(column: str, *, declared: str | None = None) -> str:
    if declared in {"higher", "lower"}:
        return declared
    return "lower" if column in _LOWER_IS_BETTER else "higher"


def _metric_label(column: str) -> str:
    return feature_label(column)


def _span_for(column: str, *, role: str) -> float | None:
    if role == "UTILITY" and column in _UTILITY_GAME_REVIEW_SPANS:
        return _UTILITY_GAME_REVIEW_SPANS[column]
    if column in _GAME_REVIEW_SPANS:
        return _GAME_REVIEW_SPANS[column]
    if column in _RATE_METRICS or column.endswith("_rate"):
        return 0.10
    return None


def _durations(
    game_row: dict[str, Any],
    baseline_means: dict[str, float],
) -> tuple[float, float]:
    duration = float(game_row.get("duration_min") or _REFERENCE_GAME_MIN)
    base_duration = float(baseline_means.get("duration_min") or _REFERENCE_GAME_MIN)
    return max(duration, 1.0), max(base_duration, 1.0)


def _comparable_values(
    column: str,
    game_value: float,
    baseline: float,
    *,
    game_row: dict[str, Any],
    baseline_means: dict[str, float],
) -> tuple[float, float]:
    """Return game/baseline values on a comparable scale (duration-normalized when needed)."""
    if column not in _DURATION_COUNT_METRICS:
        return game_value, baseline
    duration, base_duration = _durations(game_row, baseline_means)
    return (
        normalize_count_for_duration(game_value, duration),
        normalize_count_for_duration(baseline, base_duration),
    )


def _objective_accounted_span(baseline_means: dict[str, float]) -> float:
    """Widen accounted-for band for players who historically sidelane more."""
    side_lane = float(baseline_means.get("side_lane_share", 0.0))
    solo = float(baseline_means.get("solo_share", 0.0))
    bonus = min(0.15, max(0.0, (side_lane - 0.25) * 0.4 + (solo - 0.25) * 0.3))
    return _OBJ_ACCOUNTED_SPAN + bonus


def _score_objectives_dimension(
    game_row: dict[str, Any],
    baseline_means: dict[str, float],
    *,
    hint: str,
) -> GameScoreDimension:
    """Objectives score using accounted macro and unproductive absence."""
    accounted = game_row.get("objectives_accounted_for_rate")
    if accounted is None:
        accounted = game_row.get("objectives_present_rate")
    if accounted is None:
        return GameScoreDimension(name="Objectives", score=50, hint=hint, ingredients=[])

    accounted_mid = float(
        baseline_means.get(
            "objectives_accounted_for_rate",
            baseline_means.get("objectives_present_rate", _OBJ_ACCOUNTED_FALLBACK_MID),
        )
    )
    accounted_span = _objective_accounted_span(baseline_means)
    accounted_score = _clamp_unit((float(accounted) - accounted_mid) / accounted_span)

    unproductive = game_row.get("unproductive_absence_rate")
    dead = game_row.get("deaths_before_neutral_objective")
    ingredients: list[GameScoreIngredient] = [
        GameScoreIngredient(
            column="objectives_accounted_for_rate",
            label="Accounted macro",
            score=_to_percent_score(accounted_score),
            game_value=float(accounted),
            baseline_value=accounted_mid,
            weight=_OBJ_ACCOUNTED_WEIGHT,
            direction="higher",
        )
    ]

    unprod_score = 0.0
    if unproductive is not None:
        unprod_mid = float(baseline_means.get("unproductive_absence_rate", 0.15))
        unprod_score = _clamp_unit((unprod_mid - float(unproductive)) / _OBJ_UNPRODUCTIVE_SPAN)
        ingredients.append(
            GameScoreIngredient(
                column="unproductive_absence_rate",
                label="Unproductive absence",
                score=_to_percent_score(unprod_score),
                game_value=float(unproductive),
                baseline_value=unprod_mid,
                weight=_OBJ_UNPRODUCTIVE_WEIGHT,
                direction="lower",
            )
        )

    survival = 0.0
    dead_mid = float(
        baseline_means.get("deaths_before_neutral_objective", _OBJ_DEAD_BEFORE_FALLBACK_MID)
    )
    if dead is not None:
        game_dead, base_dead = _comparable_values(
            "deaths_before_neutral_objective",
            float(dead),
            dead_mid,
            game_row=game_row,
            baseline_means=baseline_means,
        )
        survival = _clamp_unit((base_dead - game_dead) / _OBJ_DEAD_BEFORE_SPAN)
        ingredients.append(
            GameScoreIngredient(
                column="deaths_before_neutral_objective",
                label="Deaths before objective",
                score=_to_percent_score(survival),
                game_value=float(dead),
                baseline_value=dead_mid,
                weight=_OBJ_DEAD_BEFORE_WEIGHT,
                direction="lower",
            )
        )

    if dead is not None and unproductive is not None:
        blended = (
            _OBJ_ACCOUNTED_WEIGHT * accounted_score
            + _OBJ_UNPRODUCTIVE_WEIGHT * unprod_score
            + _OBJ_DEAD_BEFORE_WEIGHT * survival
        )
    elif dead is not None:
        blended = 0.75 * accounted_score + 0.25 * survival
    elif unproductive is not None:
        blended = (
            _OBJ_ACCOUNTED_WEIGHT * accounted_score
            + _OBJ_UNPRODUCTIVE_WEIGHT * unprod_score
        ) / (_OBJ_ACCOUNTED_WEIGHT + _OBJ_UNPRODUCTIVE_WEIGHT)
    else:
        blended = accounted_score

    return GameScoreDimension(
        name="Objectives",
        score=_to_percent_score(blended),
        hint=hint,
        ingredients=ingredients,
    )


def _raw_improvement(
    column: str,
    game_value: float,
    baseline: float,
    *,
    game_row: dict[str, Any],
    baseline_means: dict[str, float],
    direction: str,
) -> float:
    game_cmp, base_cmp = _comparable_values(
        column,
        game_value,
        baseline,
        game_row=game_row,
        baseline_means=baseline_means,
    )
    improvement = game_cmp - base_cmp
    if direction == "lower":
        improvement = base_cmp - game_cmp
    return improvement


def _component_score(
    column: str,
    game_value: float | None,
    baseline: float | None,
    *,
    game_row: dict[str, Any],
    baseline_means: dict[str, float],
    direction: str,
    role: str,
) -> int | None:
    if game_value is None:
        return None
    if baseline is None:
        return 50

    span = _span_for(column, role=role)
    if span is None or span == 0:
        return 50

    improvement = _raw_improvement(
        column,
        float(game_value),
        float(baseline),
        game_row=game_row,
        baseline_means=baseline_means,
        direction=direction,
    )
    return _to_percent_score(_clamp_unit(improvement / span))


def _score_ingredient(
    metric: ScoreMetricSpec,
    game_row: dict[str, Any],
    baseline_means: dict[str, float],
    *,
    role: str,
) -> GameScoreIngredient | None:
    game_value = game_row.get(metric.column)
    if game_value is None:
        return None
    try:
        game_num = float(game_value)
    except (TypeError, ValueError):
        return None

    baseline = baseline_means.get(metric.column)
    # Skip incidental ally heal/shield on catch supports (Thresh, Pyke, …).
    ref = float(baseline) if baseline is not None else game_num
    if metric.column == "hpm" and not is_meaningful_healing(ref):
        return None
    if metric.column == "spm" and not is_meaningful_shielding(ref):
        return None

    direction = _metric_direction(metric.column, declared=metric.direction)
    score = _component_score(
        metric.column,
        game_num,
        baseline,
        game_row=game_row,
        baseline_means=baseline_means,
        direction=direction,
        role=role,
    )
    if score is None:
        return None

    return GameScoreIngredient(
        column=metric.column,
        label=_metric_label(metric.column),
        score=score,
        game_value=game_num,
        baseline_value=float(baseline) if baseline is not None else None,
        weight=float(metric.weight),
        direction="lower" if direction == "lower" else "higher",
    )


def _dimension_score(ingredients: list[GameScoreIngredient]) -> int:
    if not ingredients:
        return 50
    total_weight = sum(item.weight for item in ingredients)
    if total_weight <= 0:
        return 50
    return round(sum(item.score * item.weight for item in ingredients) / total_weight)


def compute_game_score(
    game_row: dict[str, Any],
    baseline_means: dict[str, float],
    *,
    role: str,
) -> GameScoreBreakdown:
    """Score one game against personal baseline means using role-native categories."""
    profile = role_profile(role)
    dimensions: list[GameScoreDimension] = []

    for spec in profile.score_components:
        if spec.name == "Objectives":
            dimensions.append(
                _score_objectives_dimension(game_row, baseline_means, hint=spec.hint)
            )
            continue

        ingredients: list[GameScoreIngredient] = []
        for metric in spec.metrics:
            if metric.column == "objectives_present_rate":
                continue
            ingredient = _score_ingredient(metric, game_row, baseline_means, role=role)
            if ingredient is not None:
                ingredients.append(ingredient)

        dimensions.append(
            GameScoreDimension(
                name=spec.name,
                score=_dimension_score(ingredients),
                hint=spec.hint,
                ingredients=ingredients,
            )
        )

    overall = (
        round(sum(dim.score for dim in dimensions) / len(dimensions)) if dimensions else 50
    )
    return GameScoreBreakdown(
        overall=overall,
        tier=_score_tier(overall),
        dimensions=dimensions,
    )
