"""The Career track pool: what a block can be about, and how its rungs are set.

Two separate questions, deliberately not conflated:

* ``build_rungs`` -- *can* this track produce three strictly-increasing rungs
  from the player's own numbers? Almost always yes, so the ladder can always
  fill every block. Peer-driven tracks step toward peer p75 when peer
  percentiles exist and toward the player's own p75 otherwise ("do what your
  good games already do, every game"), and never ask for more than
  ``MAX_BLOCK_STRETCH`` above the player's median in one block.
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
    GOALS_PER_BLOCK,
    SETUP_CLEAR_BAR,
    Rung,
)
from league_stats.analysis.career.steps import (
    BLOCK_CATEGORY_KEYS as _BANK_CATEGORIES,
    CATEGORY_ECONOMY,
    CATEGORY_FIGHT,
    CATEGORY_LANING,
    CATEGORY_OBJECTIVES,
    CATEGORY_SURVIVAL,
    CATEGORY_UTILITY,
    CATEGORY_VISION,
    rank_steps,
    steps_for_category,
)
from league_stats.analysis.career.window import player_mean, player_median, player_quantile
from league_stats.analysis.coach.engine import MIN_OBJECTIVE_PRESENCE
from league_stats.analysis.improvement import _FIRST_ITEM_BAND
from league_stats.core.role_metrics import normalize_role

DEFAULT_CATEGORY_WEIGHT: Final[float] = 50.0

# How far above their own median a single block may ask a player to go.
# Peer p75 is the long-run destination, not a one-block demand: a player at
# 0.72 vision/min asked straight for a 1.25 peer p75 has no realistic path
# inside one 20-game window. Capping each block at +20% and letting the track
# recycle walks the same player up 0.72 -> 0.86 -> 1.04 -> 1.24 across
# successive blocks, so the target stays "one step ahead" instead of unreachable.
MAX_BLOCK_STRETCH: Final[float] = 0.20


@dataclass(frozen=True)
class TrackSpec:
    """One step in the bank: a block a Career ladder can be built from.

    ``specificity`` ranks how much evidence had to be true for the step to be
    worth offering, and is the primary sort key when filling the ladder:

    * 3 -- a named problem was diagnosed in this player's games (they take greed
      deaths, they lose leads by 20 minutes, their objective trades lose value).
    * 2 -- directional: this metric sits below the player's own baseline or the
      role norm, so there is room, but no specific habit is implicated.
    * 1 -- a stretch goal that applies to anyone. Never a diagnosis.

    ``roles`` empties to "every role". It exists because a metric can be
    meaningless or inverted off-role: a jungler handed a CS-per-minute laning
    goal is the bug it was added to fix.
    """

    key: str
    name: str
    metric_label: str
    categories: tuple[str, ...]
    specificity: int = 1
    roles: tuple[str, ...] = ()

    def serves_role(self, role: str) -> bool:
        """Whether this step is offered to a role."""
        return not self.roles or normalize_role(role) in self.roles


@dataclass(frozen=True)
class TrackContext:
    """Everything a track needs to decide eligibility and freeze its rungs."""

    matches_df: pd.DataFrame
    objectives_df: pd.DataFrame
    role: str
    peer_p75: dict[str, float] = field(default_factory=dict)


# Category tuples cover every role profile's naming: laners use "Laning",
# jungle "Early game", support "Setup"; the rest are shared across all roles.
# A block is a category. Its three goals are the three most important steps from
# that category's bank (see steps.py), so two players weak at the same category do
# not get the same block.
TRACK_SPECS: Final[tuple[TrackSpec, ...]] = (
    TrackSpec(
        key=CATEGORY_LANING,
        name="Lane and early game",
        metric_label="early gold, CS and safety",
        categories=("Laning", "Early game", "Setup"),
    ),
    TrackSpec(
        key=CATEGORY_SURVIVAL,
        name="Survival",
        metric_label="how and when you die",
        categories=("Survival",),
    ),
    TrackSpec(
        key=CATEGORY_OBJECTIVES,
        name="Objectives",
        metric_label="pit presence and trades",
        categories=("Objectives",),
    ),
    TrackSpec(
        key=CATEGORY_VISION,
        name="Vision",
        metric_label="vision placed, cleared and timed",
        categories=("Vision",),
    ),
    TrackSpec(
        key=CATEGORY_FIGHT,
        name="Fighting",
        metric_label="fight impact and selection",
        categories=("Fight",),
    ),
    TrackSpec(
        key=CATEGORY_ECONOMY,
        name="Economy",
        metric_label="gold spent and carried",
        categories=("Economy",),
    ),
    TrackSpec(
        key=CATEGORY_UTILITY,
        name="Utility",
        metric_label="what your team gets from you",
        categories=("Utility",),
        roles=("UTILITY",),
    ),
    # Retired block shapes. Ladders on disk store their track key and
    # _drop_orphaned_blocks purges any block whose key left the pool, so these stay
    # resolvable for display until the block they belong to retires naturally.
    TrackSpec(
        key="laning_income", name="Laning income", metric_label="CS per minute",
        categories=("Laning", "Early game", "Setup"),
    ),
    TrackSpec(
        key="death_discipline", name="Death discipline", metric_label="deaths per phase",
        categories=("Survival",),
    ),
    TrackSpec(
        key="map_presence", name="Map presence", metric_label="pit and fight attendance",
        categories=("Objectives",),
    ),
    TrackSpec(
        key="vision_uptime", name="Vision uptime", metric_label="vision score per minute",
        categories=("Vision",),
    ),
    TrackSpec(
        key="fight_impact", name="Fight impact", metric_label="damage share",
        categories=("Fight",),
    ),
    TrackSpec(
        key="item_spike", name="Item spike", metric_label="first item timing",
        categories=("Economy",),
    ),
)

# Keys that only exist so a ladder written before the bank keeps rendering. They
# are never chosen for a new block.
LEGACY_TRACK_KEYS: Final[frozenset[str]] = frozenset(
    {"laning_income", "death_discipline", "map_presence", "vision_uptime",
     "fight_impact", "item_spike"}
)

TRACKS_BY_KEY: Final[dict[str, TrackSpec]] = {spec.key: spec for spec in TRACK_SPECS}


def track_spec(key: str) -> TrackSpec | None:
    """Look up a track by its stored key."""
    return TRACKS_BY_KEY.get(key)


def build_rungs(spec: TrackSpec, ctx: TrackContext) -> tuple[Rung, ...] | None:
    """Three frozen goals for a block, or ``None`` when the category cannot fill.

    A category block draws the three highest-ranked steps its bank can actually
    build for this player. A legacy key still routes to its old single-metric
    builder so a ladder written before the bank keeps regenerating coherently.
    """
    if spec.key in _BANK_CATEGORIES:
        return _rungs_from_bank(spec.key, ctx)
    builder = _BUILDERS.get(spec.key)
    if builder is None:
        return None
    return builder(ctx)


def _rungs_from_bank(category: str, ctx: TrackContext) -> tuple[Rung, ...] | None:
    """The best buildable steps in a category, most needed first.

    Aims for ``GOALS_PER_BLOCK`` but ships what it can get. A build whose match
    history is missing columns -- a thin sample, an older cached payload, a queue
    with no timeline data -- would otherwise produce no block at all, and a short
    block is worth more to the reader than an empty ladder.
    """
    chosen: list[Rung] = []
    seen_columns: set[str] = set()
    for step in rank_steps(steps_for_category(category, ctx.role), ctx):
        if len(chosen) == GOALS_PER_BLOCK:
            break
        rung = step.build(ctx)
        # Two goals on the same column would read as one goal stated twice.
        if rung is None or rung.column in seen_columns:
            continue
        seen_columns.add(rung.column)
        chosen.append(rung)
    return tuple(chosen) if chosen else None


def selectable_track_keys() -> tuple[str, ...]:
    """Block keys a new block may be built from: categories, never legacy keys."""
    return _BANK_CATEGORIES


def rank_track_keys(
    components: Sequence[Any],
    *,
    exclude: Container[str] = (),
    role: str = "",
) -> list[str]:
    """Block keys ordered weakest category first, re-ranked at every call.

    Only category blocks are offered; the legacy single-metric keys exist purely so
    a ladder written before the bank keeps rendering. A block whose category names
    are absent from this role's improvement score gets a neutral weight rather than
    being dropped, so the pool never shrinks over a naming difference.
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
        and spec.key not in LEGACY_TRACK_KEYS
        and (not role or spec.serves_role(role))
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
    ceiling = min(ceiling, p50 * (1 + MAX_BLOCK_STRETCH))
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


def _stepped_rungs_under(
    ctx: TrackContext,
    *,
    column: str,
    template: str,
    precision: int,
) -> tuple[Rung, ...] | None:
    """Rungs stepping a lower-is-better metric from the player's p50 toward p25."""
    p50 = player_median(ctx.matches_df, column)
    if p50 is None:
        return None
    floor = ctx.peer_p75.get(column)
    if floor is None or floor >= p50:
        floor = player_quantile(ctx.matches_df, column, 0.25)
    if floor is None:
        return None
    floor = max(floor, p50 * (1 - MAX_BLOCK_STRETCH))
    gap = p50 - floor
    if gap <= 0:
        return None
    step = gap / 3
    targets = [round(p50 - step * i, precision) for i in (1, 2, 3)]
    if not (targets[0] > targets[1] > targets[2]):
        return None
    return tuple(
        Rung(
            text=template.format(target=f"{target:.{precision}f}"),
            column=column,
            comparator="under",
            target=target,
            need=CLEAR_BAR,
        )
        for target in targets
    )


def _own_stepped_rungs(
    ctx: TrackContext,
    *,
    column: str,
    template: str,
    precision: int,
    cap: float | None = None,
    display_scale: float = 1.0,
    display_precision: int | None = None,
    floor: float = 0.0,
) -> tuple[Rung, ...] | None:
    """Rungs stepping a metric from the player's p50 up to their own stretch cap.

    For the metrics that have no peer percentile -- which is most of them, since
    ``BENCHMARK_METRIC_KEYS`` covers 16 -- the ceiling has to be intrinsic. This
    walks p50 to ``p50 * (1 + MAX_BLOCK_STRETCH)`` with no peer input at all.
    """
    p50 = player_median(ctx.matches_df, column)
    if p50 is None or p50 <= floor:
        return None
    ceiling = p50 * (1 + MAX_BLOCK_STRETCH)
    if cap is not None:
        ceiling = min(ceiling, cap)
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


def _own_stepped_under(
    ctx: TrackContext,
    *,
    column: str,
    template: str,
    precision: int,
    display_scale: float = 1.0,
    display_precision: int | None = None,
) -> tuple[Rung, ...] | None:
    """Rungs walking a lower-is-better metric down from the player's own p50."""
    p50 = player_median(ctx.matches_df, column)
    if p50 is None or p50 <= 0:
        return None
    floor = p50 * (1 - MAX_BLOCK_STRETCH)
    step = (p50 - floor) / 3
    if step <= 0:
        return None
    targets = [round(p50 - step * i, precision) for i in (1, 2, 3)]
    if not (targets[0] > targets[1] > targets[2]):
        return None
    shown = display_precision if display_precision is not None else precision
    return tuple(
        Rung(
            text=template.format(target=f"{target * display_scale:.{shown}f}"),
            column=column,
            comparator="under",
            target=target,
            need=CLEAR_BAR,
        )
        for target in targets
    )


def _absolute_lines(
    ctx: TrackContext,
    lines: Sequence[tuple[str, float, str]],
) -> tuple[Rung, ...] | None:
    """Rungs on fixed lines rather than derived targets.

    Used where a number is meaningful on its own -- even gold at 10 minutes, zero
    greed deaths -- so no ceiling has to be invented and no peer data is needed.
    """
    rungs = []
    for column, target, text in lines:
        if column not in ctx.matches_df.columns:
            return None
        comparator = "at_least" if target >= 0 else "at_least"
        rungs.append(
            Rung(text=text, column=column, comparator=comparator, target=target, need=CLEAR_BAR)
        )
    return tuple(rungs) if len(rungs) == GOALS_PER_BLOCK else None


def _zero_tolerance(
    ctx: TrackContext,
    lines: Sequence[tuple[str, str, int]],
) -> tuple[Rung, ...] | None:
    """Rungs asking for none of something, at a target of one."""
    rungs = []
    for column, text, need in lines:
        if column not in ctx.matches_df.columns:
            return None
        rungs.append(
            Rung(text=text, column=column, comparator="under", target=1.0, need=need)
        )
    return tuple(rungs) if len(rungs) == GOALS_PER_BLOCK else None


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


def _item_spike(ctx: TrackContext) -> tuple[Rung, ...] | None:
    return _stepped_rungs_under(
        ctx,
        column="first_item_min",
        template="First item by {target} minutes in 15 of 20 games",
        precision=1,
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


def _slower_than_peers(ctx: TrackContext, column: str) -> bool:
    peer_p75 = ctx.peer_p75.get(column)
    p50 = player_median(ctx.matches_df, column)
    return peer_p75 is not None and p50 is not None and p50 > peer_p75


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


def _significant_item_spike(ctx: TrackContext) -> bool:
    p50 = player_median(ctx.matches_df, "first_item_min")
    if p50 is None:
        return False
    if _slower_than_peers(ctx, "first_item_min"):
        return True
    slow_floor, _ = _FIRST_ITEM_BAND.get(normalize_role(ctx.role), (14.0, 9.0))
    return p50 >= slow_floor


_SIGNIFICANCE: Final[dict[str, Any]] = {
    "laning_income": _significant_laning_income,
    "death_discipline": _significant_death_discipline,
    "map_presence": _significant_map_presence,
    "vision_uptime": _significant_vision_uptime,
    "fight_impact": _significant_fight_impact,
    "item_spike": _significant_item_spike,
}


_BUILDERS: Final[dict[str, Any]] = {
    "laning_income": _laning_income,
    "death_discipline": _death_discipline,
    "map_presence": _map_presence,
    "vision_uptime": _vision_uptime,
    "fight_impact": _fight_impact,
    "item_spike": _item_spike,
}
