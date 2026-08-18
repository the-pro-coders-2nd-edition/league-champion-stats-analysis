"""The Career track pool: what a block can be about, and how its rungs are set.

Two separate questions, deliberately not conflated:

* ``build_rungs`` -- *can* this track produce three strictly-increasing rungs
  from the player's own numbers? Almost always yes, so the ladder can always
  show three blocks. Peer-driven tracks step toward peer p75 when peer
  percentiles exist and toward the player's own p75 otherwise ("do what your
  good games already do, every game").
* ``is_significant`` -- *should* this track go first? This is the coach's own
  gate (the thresholds its tied rules already use). It decides ordering, never
  whether a block exists: a healthy player still gets a full ladder, just one
  built from stretch goals rather than flagged weaknesses.
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
from league_stats.analysis.career.window import player_mean, player_median, player_quantile
from league_stats.analysis.coach.engine import (
    MIN_GOLD_AT_DEATH,
    MIN_OBJECTIVE_PRESENCE,
)
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
    if p50 is None:
        return None
    ceiling = ctx.peer_p75.get(column)
    if ceiling is None or ceiling <= p50:
        ceiling = player_quantile(ctx.matches_df, column, 0.75)
    if ceiling is None:
        return None
    gap = ceiling - p50
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
    """The next whole decile strictly above a 0-1 rate, capped at 1.0.

    Nudging by one point before rounding keeps the ask monotonic: a player on
    exactly 60% is asked for 70%, and one on 61% is asked for 70% too rather
    than being jumped to 80%.
    """
    percent = min(100, int(math.ceil((rate * 100 + 1) / 10) * 10))
    return percent / 100


def _map_presence(ctx: TrackContext) -> tuple[Rung, ...] | None:
    if ctx.objectives_df.empty or "present" not in ctx.objectives_df.columns:
        return None
    presence = float(pd.to_numeric(ctx.objectives_df["present"], errors="coerce").dropna().mean())
    if not math.isfinite(presence):
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
    """One 100g step below the player's current average.

    The floor is a "don't demand the unreasonable" guard for players above the
    norm; a player already under it gets a genuinely tighter target instead of
    one loosened back up to the norm.
    """
    stepped = int(average // GOLD_STEP) * GOLD_STEP - GOLD_STEP
    if average > floor:
        return max(floor, stepped)
    return max(GOLD_STEP, stepped)


def _economy_discipline(ctx: TrackContext) -> tuple[Rung, ...] | None:
    recall = player_mean(ctx.matches_df, "avg_unspent_gold")
    fights = player_mean(ctx.matches_df, "avg_unspent_gold_per_fight")
    death = player_mean(ctx.matches_df, "avg_gold_at_death")
    if recall is None or fights is None or death is None:
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


# The deaths-per-game split point _rule_early_deaths tests on.
EARLY_DEATHS_SIGNAL: Final[float] = 2.0


def is_significant(spec: TrackSpec, ctx: TrackContext) -> bool:
    """Whether this track's tied coaching signal currently fires for this build.

    Purely a priority signal. A track that is not significant is still offered --
    it just queues behind every track that is.
    """
    checker = _SIGNIFICANCE.get(spec.key)
    return bool(checker(ctx)) if checker is not None else False


def _behind_peers(ctx: TrackContext, column: str) -> bool:
    peer_p75 = ctx.peer_p75.get(column)
    p50 = player_median(ctx.matches_df, column)
    return peer_p75 is not None and p50 is not None and p50 < peer_p75


def _significant_laning_income(ctx: TrackContext) -> bool:
    return _behind_peers(ctx, "cspm")


def _significant_fight_impact(ctx: TrackContext) -> bool:
    return _behind_peers(ctx, "damage_share")


def _significant_vision_uptime(ctx: TrackContext) -> bool:
    return normalize_role(ctx.role) == "UTILITY" and _behind_peers(ctx, "vspm")


def _significant_death_discipline(ctx: TrackContext) -> bool:
    avg = player_mean(ctx.matches_df, "deaths_pre20")
    return avg is not None and avg >= EARLY_DEATHS_SIGNAL


def _significant_map_presence(ctx: TrackContext) -> bool:
    if ctx.objectives_df.empty or "present" not in ctx.objectives_df.columns:
        return False
    presence = pd.to_numeric(ctx.objectives_df["present"], errors="coerce").dropna()
    return not presence.empty and float(presence.mean()) < MIN_OBJECTIVE_PRESENCE


def _significant_economy_discipline(ctx: TrackContext) -> bool:
    recall = player_mean(ctx.matches_df, "avg_unspent_gold")
    return recall is not None and recall_gold_severity(recall) is not None


_SIGNIFICANCE: Final[dict[str, Any]] = {
    "laning_income": _significant_laning_income,
    "death_discipline": _significant_death_discipline,
    "map_presence": _significant_map_presence,
    "vision_uptime": _significant_vision_uptime,
    "fight_impact": _significant_fight_impact,
    "economy_discipline": _significant_economy_discipline,
}


_BUILDERS: Final[dict[str, Any]] = {
    "laning_income": _laning_income,
    "death_discipline": _death_discipline,
    "map_presence": _map_presence,
    "vision_uptime": _vision_uptime,
    "fight_impact": _fight_impact,
    "economy_discipline": _economy_discipline,
}
