"""Ranked Form Tracker stories: one driver + optional habit + action."""

from __future__ import annotations

from league_stats.analysis.progression.metrics import BEHAVIORAL_DEATH_METRICS
from league_stats.core.models import FormStory, MetricDelta, Recommendation, RecommendationTone

STORY_LIMIT = 3

_DEATH_RATE_METRICS = frozenset(key for key, _label, _direction in BEHAVIORAL_DEATH_METRICS)
_LANING_METRICS = frozenset({"gd10", "cs10", "csd10", "xpd10", "gd15", "lane_priority", "roams_pre15"})
_SIGNED_LANE_METRICS = frozenset({"gd10", "cs10", "gd15", "xpd10", "csd10"})

_CHILD_METRICS: dict[str, frozenset[str]] = {
    "deaths": _DEATH_RATE_METRICS,
}

_FALLBACK_ACTIONS: dict[str, dict[str, str]] = {
    "deaths": {
        "fix": "Tighten reset timing after plates and kills — stop dying for one more wave.",
        "keep": "Keep the same survival habits that cut your deaths.",
    },
    "win": {
        "fix": "Review laning and death timing in recent losses before changing your build.",
        "keep": "Stay with the habits that are converting more games lately.",
    },
    "greed_death_rate": {
        "fix": "After plates or a kill, reset before river — don't stay for one more wave.",
        "keep": "Keep resetting cleanly instead of greedy lingering.",
    },
    "vspm": {
        "fix": "Buy more control wards and place them before objectives.",
        "keep": "Keep the control-ward habits that improved your map info.",
    },
    "gd10": {
        "fix": "Review early trades and wave control in your last losing lanes.",
        "keep": "Keep the early trading pattern that built this gold lead.",
    },
    "cs10": {
        "fix": "Prioritize last-hitting under pressure over low-percentage trades.",
        "keep": "Keep the same wave management that raised your CS.",
    },
}


def _story_priority(delta: MetricDelta) -> float:
    significance = 1.5 if delta.significant else 0.5
    effect = abs(delta.effect_size or delta.delta_pct or delta.delta)
    return round(effect * significance + (delta.recent_n + delta.baseline_n) / 100, 3)


def _format_driver(delta: MetricDelta) -> str:
    if delta.metric == "win" or delta.metric.endswith("_rate"):
        direction = "up" if delta.delta >= 0 else "down"
        return (
            f"{delta.baseline * 100:.0f}% → {delta.recent * 100:.0f}% "
            f"({direction} {abs(delta.delta) * 100:.0f})"
        )
    if delta.metric in _SIGNED_LANE_METRICS:
        return f"{delta.baseline:+.0f} → {delta.recent:+.0f} ({delta.delta:+.0f})"
    if delta.metric in {"deaths", "deaths_pre14"}:
        return f"{delta.baseline:.1f} → {delta.recent:.1f} ({delta.delta:+.1f})"
    return f"{delta.baseline:.2f} → {delta.recent:.2f} ({delta.delta:+.2f})"


def _tone_for(delta: MetricDelta) -> str:
    return "keep" if delta.verdict == "improved" else "fix"


def _default_title(delta: MetricDelta) -> str:
    if delta.verdict == "improved":
        return f"{delta.label} improved"
    if delta.verdict == "regressed":
        return f"{delta.label} slipped"
    return delta.label


def _fallback_action(delta: MetricDelta) -> str:
    tone = _tone_for(delta)
    by_metric = _FALLBACK_ACTIONS.get(delta.metric)
    if by_metric and tone in by_metric:
        return by_metric[tone]
    if tone == "keep":
        return f"Keep the habits that lifted your {delta.label.lower()}."
    return f"Review recent games where {delta.label.lower()} hurt you and adjust one habit."


def _match_recommendation(
    delta: MetricDelta,
    recommendations: list[Recommendation],
    used_titles: set[str],
) -> Recommendation | None:
    tone = RecommendationTone.POSITIVE if delta.verdict == "improved" else RecommendationTone.NEGATIVE
    for rec in recommendations:
        if rec.title in used_titles or rec.tone != tone:
            continue
        # Coach rules are metric-specific; match via evidence mentioning the label,
        # or title containing the label / known titles for that metric.
        label_l = delta.label.lower()
        if label_l in rec.title.lower() or label_l in rec.evidence.lower():
            return rec
        if delta.metric == "win" and "win rate" in rec.title.lower():
            return rec
        if delta.metric == "deaths" and "death" in rec.title.lower() and "greed" not in rec.title.lower():
            return rec
        if delta.metric == "greed_death_rate" and "greed" in rec.title.lower():
            return rec
        if delta.metric == "vspm" and "vision" in rec.title.lower():
            return rec
        if delta.metric in {"gd10", "cs10"} and delta.label.lower() in rec.title.lower():
            return rec
    return None


def _habit_keywords(metric: str) -> tuple[str, ...]:
    if metric == "deaths" or metric in _DEATH_RATE_METRICS:
        if metric == "solo_death_rate":
            return ("solo",)
        if metric == "greed_death_rate":
            return ("greed",)
        if metric == "gank_death_rate":
            return ("gank",)
        if metric == "outnumbered_death_rate":
            return ("outnumbered",)
        if metric == "death_before_neutral_objective_rate":
            return ("objective",)
        return ("greed", "solo", "gank", "outnumbered", "objective", "deaths moved")
    if metric in _LANING_METRICS or metric in {"dpm", "damage_share", "gold_share"}:
        return ("keystone", "build")
    return ()


def _habit_from_shifts(metric: str, shifts: list[str], used: set[str]) -> str | None:
    keywords = _habit_keywords(metric)
    if not keywords:
        return None
    for shift in shifts:
        if shift in used:
            continue
        lower = shift.lower()
        if any(keyword in lower for keyword in keywords):
            used.add(shift)
            return shift
    return None


def _habit_from_child_delta(parent: MetricDelta, deltas: list[MetricDelta], used_metrics: set[str]) -> str | None:
    children = _CHILD_METRICS.get(parent.metric)
    if not children:
        return None
    candidates = [
        delta
        for delta in deltas
        if delta.metric in children
        and delta.metric not in used_metrics
        and delta.verdict != "inline"
        and abs(delta.delta) >= 0.08
    ]
    if not candidates:
        return None
    candidates.sort(key=_story_priority, reverse=True)
    child = candidates[0]
    used_metrics.add(child.metric)
    return f"{child.label}: {_format_driver(child)}"


def _candidate_deltas(deltas: list[MetricDelta]) -> list[MetricDelta]:
    movers = [delta for delta in deltas if delta.verdict in {"improved", "regressed"}]
    movers.sort(key=_story_priority, reverse=True)
    return movers


def build_form_stories(
    deltas: list[MetricDelta],
    *,
    behavioral_shifts: list[str] | None = None,
    recommendations: list[Recommendation] | None = None,
    limit: int = STORY_LIMIT,
) -> list[FormStory]:
    """Compose ranked stories from metric movers, habit shifts, and coaching actions."""
    shifts = list(behavioral_shifts or [])
    recs = list(recommendations or [])
    used_shifts: set[str] = set()
    used_metrics: set[str] = set()
    used_rec_titles: set[str] = set()
    stories: list[FormStory] = []

    # Prefer parent deaths over its rate children when both move.
    child_suppressed: set[str] = set()
    metric_map = {delta.metric: delta for delta in deltas}
    if "deaths" in metric_map and metric_map["deaths"].verdict != "inline":
        child_suppressed |= _DEATH_RATE_METRICS

    for delta in _candidate_deltas(deltas):
        if len(stories) >= limit:
            break
        if delta.metric in used_metrics or delta.metric in child_suppressed:
            continue
        used_metrics.add(delta.metric)
        tone = _tone_for(delta)
        rec = _match_recommendation(delta, recs, used_rec_titles)
        title = rec.title if rec else _default_title(delta)
        action = rec.detail if rec else _fallback_action(delta)
        if rec:
            used_rec_titles.add(rec.title)

        habit = _habit_from_shifts(delta.metric, shifts, used_shifts)
        if habit is None:
            habit = _habit_from_child_delta(delta, deltas, used_metrics)

        stories.append(
            FormStory(
                tone=tone,  # type: ignore[arg-type]
                metric=delta.metric,
                title=title,
                driver=_format_driver(delta),
                habit=habit,
                action=action,
                priority=_story_priority(delta),
            )
        )

    # Prefer a mix: if we only have keeps and a fix exists later, already ranked by priority.
    return stories[:limit]
