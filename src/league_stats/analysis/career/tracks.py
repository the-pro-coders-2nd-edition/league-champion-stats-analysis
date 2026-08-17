"""The Career track pool: what a block can be about, and how its rungs are set.

A track is eligible exactly when it can produce three strictly-increasing rungs
from the player's own current numbers, so ``build_rungs`` returning ``None`` is
the eligibility answer. Peer-driven tracks need ``peer_p75``; the curated tracks
gate on the same thresholds their tied coach rules already use, so the ladder
never offers a goal the coaching engine would not have flagged.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Container, Final, Sequence

import pandas as pd

from league_stats.analysis.career.models import (
    CLEAR_BAR,
    SETUP_CLEAR_BAR,
    Rung,
)
from league_stats.analysis.career.window import player_mean, player_median
from league_stats.analysis.coach.engine import MIN_GOLD_AT_DEATH, MIN_OBJECTIVE_PRESENCE
from league_stats.analysis.economy import RECALL_GOLD_COMPONENT_MAX, recall_gold_severity
from league_stats.core.role_metrics import normalize_role

DEFAULT_CATEGORY_WEIGHT: Final[float] = 50.0
GOLD_STEP: Final[int] = 100


@dataclass(frozen=True)
class TrackSpec:
    """One family of goals a Career block can be built from."""

    key: str
    name: str
    metric_label: str
    categories: tuple[str, ...]


@dataclass(frozen=True)
class TrackContext:
    """Everything a track needs to decide eligibility and freeze its rungs."""

    matches_df: pd.DataFrame
    objectives_df: pd.DataFrame
    role: str
    peer_p75: dict[str, float] = field(default_factory=dict)


# Category tuples cover every role profile's naming: laners use "Laning",
# jungle "Early game", support "Setup"; the rest are shared across all roles.
TRACK_SPECS: Final[tuple[TrackSpec, ...]] = (
    TrackSpec(
        key="laning_income",
        name="Laning income",
        metric_label="CS per minute",
        categories=("Laning", "Early game", "Setup"),
    ),
    TrackSpec(
        key="death_discipline",
        name="Death discipline",
        metric_label="deaths per phase",
        categories=("Survival",),
    ),
    TrackSpec(
        key="map_presence",
        name="Map presence",
        metric_label="pit and fight attendance",
        categories=("Objectives",),
    ),
    TrackSpec(
        key="vision_uptime",
        name="Vision uptime",
        metric_label="vision score per minute",
        categories=("Vision",),
    ),
    TrackSpec(
        key="fight_impact",
        name="Fight impact",
        metric_label="damage share",
        categories=("Fight",),
    ),
    TrackSpec(
        key="economy_discipline",
        name="Economy discipline",
        metric_label="banked gold",
        categories=("Economy",),
    ),
)

TRACKS_BY_KEY: Final[dict[str, TrackSpec]] = {spec.key: spec for spec in TRACK_SPECS}


def track_spec(key: str) -> TrackSpec | None:
    """Look up a track by its stored key."""
    return TRACKS_BY_KEY.get(key)


def build_rungs(spec: TrackSpec, ctx: TrackContext) -> tuple[Rung, ...] | None:
    """Three frozen rungs for a track, or ``None`` when it is not eligible."""
    builder = _BUILDERS.get(spec.key)
    if builder is None:
        return None
    return builder(ctx)


def rank_track_keys(
    components: Sequence[Any],
    *,
    exclude: Container[str] = (),
) -> list[str]:
    """Track keys ordered weakest category first, re-ranked at every call.

    A track whose category names are absent from this role's improvement score
    gets a neutral weight rather than being dropped, so the pool never shrinks
    because of a role-profile naming difference.
    """
    scores = {str(comp.name): float(comp.score) for comp in components}
    ranked = [
        (
            min(
                (scores[name] for name in spec.categories if name in scores),
                default=DEFAULT_CATEGORY_WEIGHT,
            ),
            index,
            spec.key,
        )
        for index, spec in enumerate(TRACK_SPECS)
        if spec.key not in exclude
    ]
    ranked.sort()
    return [key for _, _, key in ranked]


def _stepped_rungs(
    ctx: TrackContext,
    *,
    column: str,
    template: str,
    precision: int,
    display_scale: float = 1.0,
    display_precision: int | None = None,
) -> tuple[Rung, ...] | None:
    """Rungs stepping a single metric from the player's p50 toward peer p75."""
    p50 = player_median(ctx.matches_df, column)
    peer_p75 = ctx.peer_p75.get(column)
    if p50 is None or peer_p75 is None:
        return None
    gap = peer_p75 - p50
    if gap <= 0:
        return None
    step = gap / 3
    targets = [round(p50 + step * i, precision) for i in (1, 2, 3)]
    if not (targets[0] < targets[1] < targets[2]):
        return None
    shown = display_precision if display_precision is not None else precision
    return tuple(
        Rung(
            text=template.format(target=f"{target * display_scale:.{shown}f}"),
            column=column,
            comparator="at_least",
            target=target,
            need=CLEAR_BAR,
        )
        for target in targets
    )


def _laning_income(ctx: TrackContext) -> tuple[Rung, ...] | None:
    return _stepped_rungs(
        ctx,
        column="cspm",
        template="{target} CS per minute in 15 of 20 games",
        precision=1,
    )


def _vision_uptime(ctx: TrackContext) -> tuple[Rung, ...] | None:
    if normalize_role(ctx.role) != "UTILITY":
        return None
    return _stepped_rungs(
        ctx,
        column="vspm",
        template="{target} vision score per minute in 15 of 20 games",
        precision=2,
    )


def _fight_impact(ctx: TrackContext) -> tuple[Rung, ...] | None:
    return _stepped_rungs(
        ctx,
        column="damage_share",
        template="{target}% team damage share in 15 of 20 games",
        precision=3,
        display_scale=100.0,
        display_precision=0,
    )


def _death_discipline(ctx: TrackContext) -> tuple[Rung, ...] | None:
    avg = player_mean(ctx.matches_df, "deaths_pre20")
    if avg is None:
        return None
    first = max(1, round(avg) - 1)
    second = max(1, first - 1)
    if first == second:
        return None
    if "deaths_before_neutral_objective" not in ctx.matches_df.columns:
        return None
    return (
        Rung(
            text=f"Under {first} deaths before 20 min in 15 of 20 games",
            column="deaths_pre20",
            comparator="under",
            target=float(first),
            need=CLEAR_BAR,
        ),
        Rung(
            text=f"Under {second} deaths before 20 min in 15 of 20 games",
            column="deaths_pre20",
            comparator="under",
            target=float(second),
            need=CLEAR_BAR,
        ),
        Rung(
            text="No death in the objective setup window in 12 of 20 games",
            column="deaths_before_neutral_objective",
            comparator="under",
            target=1.0,
            need=SETUP_CLEAR_BAR,
        ),
    )


def _next_decile(rate: float) -> float:
    """Round a 0-1 rate up to the next decile and add one more, capped at 1.0."""
    percent = min(100, int(math.ceil(rate * 100 / 10) * 10) + 10)
    return percent / 100


def _map_presence(ctx: TrackContext) -> tuple[Rung, ...] | None:
    if ctx.objectives_df.empty or "present" not in ctx.objectives_df.columns:
        return None
    presence = float(pd.to_numeric(ctx.objectives_df["present"], errors="coerce").dropna().mean())
    if not math.isfinite(presence) or presence >= MIN_OBJECTIVE_PRESENCE:
        return None
    fights = player_mean(ctx.matches_df, "tf_participation")
    if fights is None or "control_wards" not in ctx.matches_df.columns:
        return None
    pit_target = _next_decile(presence)
    fight_target = _next_decile(fights)
    return (
        Rung(
            text=f"Present at {pit_target * 100:.0f}% of pit takes in 15 of 20 games",
            column="objectives_present_rate",
            comparator="at_least",
            target=pit_target,
            need=CLEAR_BAR,
        ),
        Rung(
            text="One control ward per game in 15 of 20 games",
            column="control_wards",
            comparator="at_least",
            target=1.0,
            need=CLEAR_BAR,
        ),
        Rung(
            text=f"Attend {fight_target * 100:.0f}% of teamfights in 15 of 20 games",
            column="tf_participation",
            comparator="at_least",
            target=fight_target,
            need=CLEAR_BAR,
        ),
    )


def _gold_target(average: float, floor: int) -> int:
    """One 100g step below the player's current average, never under the floor."""
    return max(floor, int(average // GOLD_STEP) * GOLD_STEP - GOLD_STEP)


def _economy_discipline(ctx: TrackContext) -> tuple[Rung, ...] | None:
    recall = player_mean(ctx.matches_df, "avg_unspent_gold")
    fights = player_mean(ctx.matches_df, "avg_unspent_gold_per_fight")
    death = player_mean(ctx.matches_df, "avg_gold_at_death")
    if recall is None or fights is None or death is None:
        return None
    if recall_gold_severity(recall) is None:
        return None
    recall_target = _gold_target(recall, RECALL_GOLD_COMPONENT_MAX)
    fight_target = _gold_target(fights, RECALL_GOLD_COMPONENT_MAX)
    death_target = _gold_target(death, MIN_GOLD_AT_DEATH)
    return (
        Rung(
            text=f"Under {recall_target}g banked before recall in 15 of 20 games",
            column="avg_unspent_gold",
            comparator="under",
            target=float(recall_target),
            need=CLEAR_BAR,
        ),
        Rung(
            text=f"Under {fight_target}g banked entering fights in 15 of 20 games",
            column="avg_unspent_gold_per_fight",
            comparator="under",
            target=float(fight_target),
            need=CLEAR_BAR,
        ),
        Rung(
            text=f"Under {death_target}g banked on death in 15 of 20 games",
            column="avg_gold_at_death",
            comparator="under",
            target=float(death_target),
            need=CLEAR_BAR,
        ),
    )


_BUILDERS: Final[dict[str, Any]] = {
    "laning_income": _laning_income,
    "death_discipline": _death_discipline,
    "map_presence": _map_presence,
    "vision_uptime": _vision_uptime,
    "fight_impact": _fight_impact,
    "economy_discipline": _economy_discipline,
}
