"""Diff-specific coaching recommendations triggered by significant metric deltas."""

from __future__ import annotations

from league_stats_common.core.models import MetricDelta, Recommendation, RecommendationTone


def _priority(delta: MetricDelta) -> float:
    significance = 1.5 if delta.significant else 0.5
    effect = abs(delta.effect_size or delta.delta_pct or delta.delta)
    return round(effect * significance + (delta.recent_n + delta.baseline_n) / 100, 3)


def _rule_winrate_improved(delta: MetricDelta) -> Recommendation | None:
    if delta.metric != "win" or delta.verdict != "improved" or not delta.significant:
        return None
    return Recommendation(
        category="Form",
        title="Win rate trending up",
        detail="Stay with the habits that are converting more games — don't shake up your build yet.",
        evidence=(
            f"Win rate moved from {delta.baseline * 100:.0f}% to {delta.recent * 100:.0f}% "
            f"(p={delta.p_value:.3f})."
        ),
        action="Keep the habits that are converting — don't shake the build yet",
        tone=RecommendationTone.POSITIVE,
        p_value=delta.p_value,
        effect_size=delta.effect_size,
        priority=_priority(delta),
        sample_size=delta.recent_n,
    )


def _rule_winrate_regressed(delta: MetricDelta) -> Recommendation | None:
    if delta.metric != "win" or delta.verdict != "regressed" or not delta.significant:
        return None
    return Recommendation(
        category="Form",
        title="Recent win rate dip",
        detail="Review laning and death timing in recent losses before changing your build.",
        evidence=(
            f"Win rate dropped from {delta.baseline * 100:.0f}% to {delta.recent * 100:.0f}% "
            f"(p={delta.p_value:.3f})."
        ),
        action="Review laning and death timing in recent losses",
        tone=RecommendationTone.NEGATIVE,
        p_value=delta.p_value,
        effect_size=delta.effect_size,
        priority=_priority(delta),
        sample_size=delta.recent_n,
    )


def _rule_deaths_regressed(delta: MetricDelta) -> Recommendation | None:
    if delta.metric != "deaths" or delta.verdict != "regressed" or not delta.significant:
        return None
    return Recommendation(
        category="Form",
        title="Deaths creeping up",
        detail="Tighten reset timing after plates and kills — stop dying for one more wave.",
        evidence=(
            f"Deaths/game rose from {delta.baseline:.1f} to {delta.recent:.1f} "
            f"(d={delta.effect_size:.2f}, p={delta.p_value:.3f})."
        ),
        action="Reset after plates and kills — don't stay for one more wave",
        tone=RecommendationTone.NEGATIVE,
        p_value=delta.p_value,
        effect_size=delta.effect_size,
        priority=_priority(delta),
        sample_size=delta.recent_n,
    )


def _rule_laning_improved(delta: MetricDelta) -> Recommendation | None:
    if delta.metric not in {"gd10", "cs10"} or delta.verdict != "improved" or not delta.significant:
        return None
    return Recommendation(
        category="Form",
        title=f"{delta.label} improved",
        detail="Keep the same early trading pattern and wave control that built this lead.",
        evidence=(
            f"{delta.label} moved from {delta.baseline:+.0f} to {delta.recent:+.0f} "
            f"(p={delta.p_value:.3f})."
        ),
        action="Keep the early trading and wave control that built this lead",
        tone=RecommendationTone.POSITIVE,
        p_value=delta.p_value,
        effect_size=delta.effect_size,
        priority=_priority(delta),
        sample_size=delta.recent_n,
    )


def _rule_greed_deaths(delta: MetricDelta) -> Recommendation | None:
    if delta.metric != "greed_death_rate" or delta.verdict != "regressed":
        return None
    if not delta.significant and abs(delta.delta) < 0.10:
        return None
    return Recommendation(
        category="Form",
        title="More greed deaths lately",
        detail="After plates or a kill, reset before river — don't stay for one more wave.",
        evidence=(
            f"Greed death rate rose from {delta.baseline * 100:.0f}% to {delta.recent * 100:.0f}%."
        ),
        action="After plates or a kill, reset before river",
        tone=RecommendationTone.NEGATIVE,
        p_value=delta.p_value,
        effect_size=delta.effect_size,
        priority=_priority(delta),
        sample_size=delta.recent_n,
    )


def _rule_vision_improved(delta: MetricDelta) -> Recommendation | None:
    if delta.metric != "vspm" or delta.verdict != "improved" or not delta.significant:
        return None
    return Recommendation(
        category="Form",
        title="Vision trending up",
        detail="Keep buying control wards and placing them before objectives.",
        evidence=f"Vision/min rose from {delta.baseline:.2f} to {delta.recent:.2f}.",
        action="Keep buying control wards before objectives",
        tone=RecommendationTone.POSITIVE,
        p_value=delta.p_value,
        effect_size=delta.effect_size,
        priority=_priority(delta),
        sample_size=delta.recent_n,
    )


def _rule_vision_regressed(delta: MetricDelta) -> Recommendation | None:
    if delta.metric != "vspm" or delta.verdict != "regressed" or not delta.significant:
        return None
    return Recommendation(
        category="Form",
        title="Vision dropped recently",
        detail="Buy more control wards and place them before major objectives.",
        evidence=f"Vision/min fell from {delta.baseline:.2f} to {delta.recent:.2f}.",
        action="Buy more control wards before major objectives",
        tone=RecommendationTone.NEGATIVE,
        p_value=delta.p_value,
        effect_size=delta.effect_size,
        priority=_priority(delta),
        sample_size=delta.recent_n,
    )


def _rule_generic_regression(delta: MetricDelta) -> Recommendation | None:
    if not delta.significant or delta.verdict != "regressed":
        return None
    if delta.metric in {"win", "deaths", "greed_death_rate", "vspm"}:
        return None
    return Recommendation(
        category="Form",
        title=f"{delta.label} slipped",
        detail=f"Pick one recent game where {delta.label.lower()} hurt you and change one habit next game.",
        evidence=f"Baseline {delta.baseline:.2f} → recent {delta.recent:.2f}.",
        action=f"Change one habit tied to {delta.label.lower()} next game",
        tone=RecommendationTone.NEGATIVE,
        p_value=delta.p_value,
        effect_size=delta.effect_size,
        priority=_priority(delta) * 0.8,
        sample_size=delta.recent_n,
    )


_FORM_RULES = (
    _rule_winrate_improved,
    _rule_winrate_regressed,
    _rule_deaths_regressed,
    _rule_laning_improved,
    _rule_greed_deaths,
    _rule_vision_improved,
    _rule_vision_regressed,
    _rule_generic_regression,
)


def generate_form_recommendations(
    deltas: list[MetricDelta],
    *,
    existing_titles: set[str] | None = None,
    limit: int = 3,
) -> list[Recommendation]:
    """Generate diff-specific coaching tips ranked by priority."""
    seen = existing_titles or set()
    recommendations: list[Recommendation] = []
    for rule in _FORM_RULES:
        for delta in deltas:
            try:
                rec = rule(delta)
            except Exception:
                rec = None
            if rec is None or rec.title in seen:
                continue
            seen.add(rec.title)
            recommendations.append(rec)
    recommendations.sort(key=lambda rec: rec.priority, reverse=True)
    return recommendations[:limit]
