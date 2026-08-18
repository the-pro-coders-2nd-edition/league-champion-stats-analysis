"""The step bank: many candidate goals per category, ranked by what a player needs.

A Career block is a *category* (Survival, Vision, Objectives, ...). Its three goals
are the three most important steps drawn from that category's bank, so two players
weak at Survival do not get the same block: one is told to stop chasing greed
deaths and giving shutdowns, the other to stay alive through the setup window.

Each step declares:

* ``specificity`` -- how much evidence had to be true for it to be worth offering.
  3 means a named habit was diagnosed in this player's games, 2 that the metric
  sits below their own baseline, 1 that it is a stretch goal anyone could take.
* ``severity(ctx)`` -- how badly it is currently missed, used to order steps that
  share a specificity. Higher is worse.
* ``roles`` -- empty for every role. A metric can be meaningless or inverted
  off-role, which is why a jungler was previously handed a CS-per-minute goal.

A step builds exactly one ``Rung``. Progression comes from the block recycling
against a moved median, not from three rungs on one metric.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Final, Sequence

from league_stats.analysis.career.models import CLEAR_BAR, SETUP_CLEAR_BAR, Rung
from league_stats.analysis.career.window import (
    player_mean,
    player_median,
    player_quantile,
    recent_window,
)
from league_stats.core.role_metrics import normalize_role

# Canonical category ids. Role profiles name the same idea differently -- laners
# have "Laning", jungle "Early game", support "Setup" -- so the bank keys on an id
# and maps outward to those display names.
CATEGORY_LANING: Final[str] = "laning"
CATEGORY_SURVIVAL: Final[str] = "survival"
CATEGORY_OBJECTIVES: Final[str] = "objectives"
CATEGORY_VISION: Final[str] = "vision"
CATEGORY_FIGHT: Final[str] = "fight"
CATEGORY_ECONOMY: Final[str] = "economy"
CATEGORY_UTILITY: Final[str] = "utility"

# How far past the anchor a single block may ask a player to go.
MAX_STEP_STRETCH: Final[float] = 0.15

# Where a target is anchored, as a quantile of the baseline games.
#
# The clear bar asks for the target in 15 of 20 games -- three quarters of them.
# Anchoring on the median made that incoherent: a player exceeds their own median
# in about half their games by definition, so a median-plus-stretch target quietly
# demanded a level increase *and* a jump in consistency from under half your games
# to three quarters. A live report asked one player for 60% pit presence in 15 of
# 20 games while they were hitting 60% in 2 of their last 10.
#
# Anchoring low means the anchor is a level the player already reaches in most
# games, so the stretch is the only thing being asked for.
ANCHOR_QUANTILE: Final[float] = 0.35

# Games the anchor is computed over. Deliberately wider than the 20-game
# measurement window: a percentile taken from 20 games swings hard on one bad run,
# and a frozen target that moved with the noise would be unfair either way.
BASELINE_GAMES: Final[int] = 100

# Severity a step must clear before its declared specificity counts. Below this
# the evidence did not really fire, and a step that claimed to diagnose a habit
# the player does not have would tell a clean player to stop doing something they
# never do. Such a step is still offered -- as a stretch goal, ranked last.
DIAGNOSIS_FLOOR: Final[float] = 0.05


@dataclass(frozen=True)
class StepSpec:
    """One candidate goal in the bank."""

    key: str
    category: str
    specificity: int
    build: Callable[["StepContext"], Rung | None]
    severity: Callable[["StepContext"], float] = lambda ctx: 0.0
    roles: tuple[str, ...] = ()

    def serves_role(self, role: str) -> bool:
        """Whether this step is offered to a role."""
        return not self.roles or normalize_role(role) in self.roles


# Imported lazily by tracks.py to avoid a cycle; the shape it needs is TrackContext.
StepContext = object


# --- single-rung shapes ----------------------------------------------------


def _baseline(ctx, column: str, quantile: float) -> float | None:
    """A quantile of a column over the most recent :data:`BASELINE_GAMES` games."""
    return player_quantile(recent_window(ctx.matches_df, BASELINE_GAMES), column, quantile)


def _stepped(
    ctx,
    *,
    column: str,
    template: str,
    precision: int,
    cap: float | None = None,
    display_scale: float = 1.0,
    display_precision: int | None = None,
    need: int = CLEAR_BAR,
) -> Rung | None:
    """One rung a stretch above the level the player already reaches in most games.

    Anchored at :data:`ANCHOR_QUANTILE` of the last :data:`BASELINE_GAMES` games,
    stretched by :data:`MAX_STEP_STRETCH`. Peer p75 only pulls the target *down*,
    so a player is never asked to go past their rank's 75th percentile merely
    because the stretch said so; a peer number at or below the anchor is ignored,
    since it would ask for nothing.

    Peer percentiles cover 16 metrics (``BENCHMARK_METRIC_KEYS``), so most steps
    in the bank have no peer number at all and the stretch is the whole story.
    """
    anchor = _baseline(ctx, column, ANCHOR_QUANTILE)
    if anchor is None or anchor <= 0:
        return None
    ceiling = anchor * (1 + MAX_STEP_STRETCH)
    peer = ctx.peer_p75.get(column)
    if peer is not None and anchor < peer < ceiling:
        ceiling = peer
    if cap is not None:
        ceiling = min(ceiling, cap)
    if ceiling <= anchor:
        return None
    target = round(ceiling, precision)
    if target <= round(anchor, precision):
        return None
    shown = display_precision if display_precision is not None else precision
    return Rung(
        text=template.format(target=f"{target * display_scale:.{shown}f}"),
        column=column,
        comparator="at_least",
        target=target,
        need=need,
    )


def _stepped_under(
    ctx,
    *,
    column: str,
    template: str,
    precision: int,
    display_scale: float = 1.0,
    display_precision: int | None = None,
    need: int = CLEAR_BAR,
) -> Rung | None:
    """One rung a stretch below the level the player already stays under.

    The mirror of :func:`_stepped`: for a lower-is-better metric the value you are
    already under in most games is the *upper* quantile, so the anchor is
    ``1 - ANCHOR_QUANTILE`` and the stretch subtracts.
    """
    anchor = _baseline(ctx, column, 1 - ANCHOR_QUANTILE)
    if anchor is None or anchor <= 0:
        return None
    target = round(anchor * (1 - MAX_STEP_STRETCH), precision)
    if target <= 0 or target >= round(anchor, precision):
        return None
    shown = display_precision if display_precision is not None else precision
    return Rung(
        text=template.format(target=f"{target * display_scale:.{shown}f}"),
        column=column,
        comparator="under",
        target=target,
        need=need,
    )


def _line(ctx, *, column: str, target: float, text: str, need: int = CLEAR_BAR) -> Rung | None:
    """One rung on a fixed, self-explaining line. No target has to be invented."""
    if column not in ctx.matches_df.columns:
        return None
    return Rung(text=text, column=column, comparator="at_least", target=target, need=need)


def _none_of(ctx, *, column: str, text: str, need: int = CLEAR_BAR) -> Rung | None:
    """One rung asking for none of something."""
    if column not in ctx.matches_df.columns:
        return None
    return Rung(text=text, column=column, comparator="under", target=1.0, need=need)


def _integer_under(ctx, *, column: str, template: str, need: int = CLEAR_BAR) -> Rung | None:
    """One rung one whole unit below the player's rounded average."""
    avg = player_mean(ctx.matches_df, column)
    if avg is None:
        return None
    target = max(1, round(avg) - 1)
    return Rung(
        text=template.format(target=target),
        column=column,
        comparator="under",
        target=float(target),
        need=need,
    )


# --- severity helpers ------------------------------------------------------


def _mean_of(ctx, column: str) -> float:
    value = player_mean(ctx.matches_df, column)
    return 0.0 if value is None else value


def _shortfall(ctx, column: str, norm: float) -> float:
    """How far below a reference line the player's median sits, as a fraction."""
    p50 = player_median(ctx.matches_df, column)
    if p50 is None or norm <= 0:
        return 0.0
    return max(0.0, (norm - p50) / norm)


def _below_zero_share(ctx, column: str) -> float:
    """Share of games where a diff column is negative. The lead-loss signal."""
    if column not in ctx.matches_df.columns:
        return 0.0
    import pandas as pd

    series = pd.to_numeric(ctx.matches_df[column], errors="coerce").dropna()
    if series.empty:
        return 0.0
    return float((series < 0).mean())


# --- the bank --------------------------------------------------------------

STEP_BANK: Final[tuple[StepSpec, ...]] = (
    # --- Laning / early game ------------------------------------------------
    StepSpec(
        key="cs_per_minute", category=CATEGORY_LANING, specificity=1,
        build=lambda c: _stepped(
            c, column="cspm", template="{target} CS per minute in 15 of 20 games", precision=1
        ),
        severity=lambda c: _shortfall(c, "cspm", c.peer_p75.get("cspm", 7.0)),
    ),
    StepSpec(
        key="even_at_10", category=CATEGORY_LANING, specificity=3,
        build=lambda c: _line(
            c, column="gd10", target=0.0, text="Even or ahead in gold at 10 min in 15 of 20 games"
        ),
        severity=lambda c: _below_zero_share(c, "gd10"),
    ),
    StepSpec(
        key="hold_lead_to_15", category=CATEGORY_LANING, specificity=3,
        build=lambda c: _line(
            c, column="gd15", target=0.0, text="Even or ahead in gold at 15 min in 15 of 20 games"
        ),
        severity=lambda c: _below_zero_share(c, "gd15"),
    ),
    StepSpec(
        key="hold_lead_to_20", category=CATEGORY_LANING, specificity=3,
        build=lambda c: _line(
            c, column="gd20", target=0.0, text="Even or ahead in gold at 20 min in 15 of 20 games"
        ),
        severity=lambda c: _below_zero_share(c, "gd20"),
    ),
    StepSpec(
        key="even_xp_at_10", category=CATEGORY_LANING, specificity=2,
        build=lambda c: _line(
            c, column="xpd10", target=0.0, text="Even or ahead in XP at 10 min in 15 of 20 games"
        ),
        severity=lambda c: _below_zero_share(c, "xpd10"),
    ),
    StepSpec(
        key="no_tower_dive_deaths", category=CATEGORY_LANING, specificity=3,
        build=lambda c: _none_of(
            c, column="under_enemy_tower_laning_deaths",
            text="No death under the enemy tower in 15 of 20 games",
        ),
        severity=lambda c: _mean_of(c, "under_enemy_tower_laning_deaths"),
    ),
    StepSpec(
        key="no_lane_gank_deaths", category=CATEGORY_LANING, specificity=3,
        build=lambda c: _none_of(
            c, column="gank_deaths_laning", text="Survive the lane phase unganked in 15 of 20 games",
        ),
        severity=lambda c: _mean_of(c, "gank_deaths_laning"),
    ),
    StepSpec(
        key="cs_at_10", category=CATEGORY_LANING, specificity=2,
        build=lambda c: _stepped(
            c, column="cs10", template="{target} CS by 10 min in 15 of 20 games", precision=0
        ),
        severity=lambda c: _shortfall(c, "cs10", 75.0),
    ),
    StepSpec(
        key="jungle_gold_at_10", category=CATEGORY_LANING, specificity=2, roles=("JUNGLE",),
        build=lambda c: _stepped(
            c, column="gold10", template="{target} gold by 10 min in 15 of 20 games", precision=0
        ),
        severity=lambda c: _shortfall(c, "gold10", 3800.0),
    ),
    StepSpec(
        key="early_gank_pressure", category=CATEGORY_LANING, specificity=2,
        roles=("JUNGLE", "UTILITY"),
        build=lambda c: _stepped(
            c, column="early_ganks", template="{target} early ganks in 15 of 20 games", precision=1
        ),
        severity=lambda c: _shortfall(c, "early_ganks", c.peer_p75.get("early_ganks", 2.0)),
    ),
    # --- Survival -----------------------------------------------------------
    StepSpec(
        key="deaths_before_20", category=CATEGORY_SURVIVAL, specificity=2,
        build=lambda c: _integer_under(
            c, column="deaths_pre20", template="Under {target} deaths before 20 min in 15 of 20 games"
        ),
        severity=lambda c: _mean_of(c, "deaths_pre20") / 5.0,
    ),
    StepSpec(
        key="deaths_before_14", category=CATEGORY_SURVIVAL, specificity=2,
        build=lambda c: _integer_under(
            c, column="deaths_pre14", template="Under {target} deaths before 14 min in 15 of 20 games"
        ),
        severity=lambda c: _mean_of(c, "deaths_pre14") / 4.0,
    ),
    StepSpec(
        key="greed_discipline", category=CATEGORY_SURVIVAL, specificity=3,
        build=lambda c: _none_of(
            c, column="greed_deaths", text="No greed death in 15 of 20 games"
        ),
        severity=lambda c: _mean_of(c, "greed_deaths"),
    ),
    StepSpec(
        key="no_solo_deaths", category=CATEGORY_SURVIVAL, specificity=3,
        build=lambda c: _none_of(
            c, column="solo_deaths", text="No death caught alone in 15 of 20 games"
        ),
        severity=lambda c: _mean_of(c, "solo_deaths") / 2.0,
    ),
    StepSpec(
        key="no_outnumbered_deaths", category=CATEGORY_SURVIVAL, specificity=3,
        build=lambda c: _none_of(
            c, column="outnumbered_deaths", text="No death while outnumbered in 15 of 20 games"
        ),
        severity=lambda c: _mean_of(c, "outnumbered_deaths") / 2.0,
    ),
    StepSpec(
        key="shutdown_hygiene", category=CATEGORY_SURVIVAL, specificity=3,
        build=lambda c: _stepped_under(
            c, column="shutdown_given",
            template="Hand over under {target}g of shutdowns in 15 of 20 games", precision=0,
        ),
        severity=lambda c: min(1.0, _mean_of(c, "shutdown_given") / 1000.0),
    ),
    StepSpec(
        key="time_alive", category=CATEGORY_SURVIVAL, specificity=2,
        build=lambda c: _stepped_under(
            c, column="time_dead_s", template="Under {target}s spent dead in 15 of 20 games",
            precision=0,
        ),
        severity=lambda c: min(1.0, _mean_of(c, "time_dead_s") / 300.0),
    ),
    StepSpec(
        key="setup_window_survival", category=CATEGORY_SURVIVAL, specificity=3,
        build=lambda c: _none_of(
            c, column="deaths_before_neutral_objective",
            text="No death in the objective setup window in 12 of 20 games", need=SETUP_CLEAR_BAR,
        ),
        severity=lambda c: _mean_of(c, "deaths_before_neutral_objective"),
    ),
    # --- Objectives ---------------------------------------------------------
    StepSpec(
        key="pit_presence", category=CATEGORY_OBJECTIVES, specificity=2,
        build=lambda c: _stepped(
            c, column="objectives_present_rate", cap=1.0,
            template="Present at {target}% of pit takes in 15 of 20 games",
            precision=3, display_scale=100.0, display_precision=0,
        ),
        severity=lambda c: _shortfall(c, "objectives_present_rate", 0.75),
    ),
    StepSpec(
        key="control_ward_each_game", category=CATEGORY_OBJECTIVES, specificity=1,
        build=lambda c: _line(
            c, column="control_wards", target=1.0, text="One control ward per game in 15 of 20 games"
        ),
        severity=lambda c: _shortfall(c, "control_wards", 2.0),
    ),
    StepSpec(
        key="teamfight_attendance", category=CATEGORY_OBJECTIVES, specificity=2,
        build=lambda c: _stepped(
            c, column="tf_participation", cap=1.0,
            template="Attend {target}% of teamfights in 15 of 20 games",
            precision=3, display_scale=100.0, display_precision=0,
        ),
        severity=lambda c: _shortfall(c, "tf_participation", 0.75),
    ),
    StepSpec(
        key="objective_trades", category=CATEGORY_OBJECTIVES, specificity=3,
        build=lambda c: _stepped(
            c, column="objective_trade_success_rate", cap=1.0,
            template="Come out ahead on {target}% of objective trades in 15 of 20 games",
            precision=3, display_scale=100.0, display_precision=0,
        ),
        severity=lambda c: _shortfall(c, "objective_trade_success_rate", 0.5),
    ),
    StepSpec(
        key="absence_value", category=CATEGORY_OBJECTIVES, specificity=3,
        build=lambda c: _stepped_under(
            c, column="unproductive_absence_rate",
            template="Under {target}% of pits skipped for nothing, in 15 of 20 games",
            precision=3, display_scale=100.0, display_precision=0,
        ),
        severity=lambda c: min(1.0, _mean_of(c, "unproductive_absence_rate") * 2.0),
    ),
    StepSpec(
        key="tower_pressure", category=CATEGORY_OBJECTIVES, specificity=2,
        build=lambda c: _stepped(
            c, column="towers_taken", template="{target} towers taken in 15 of 20 games", precision=1
        ),
        severity=lambda c: _shortfall(c, "towers_taken", 3.0),
    ),
    # --- Vision -------------------------------------------------------------
    StepSpec(
        key="vision_per_minute", category=CATEGORY_VISION, specificity=1,
        build=lambda c: _stepped(
            c, column="vspm", template="{target} vision score per minute in 15 of 20 games",
            precision=2,
        ),
        severity=lambda c: _shortfall(c, "vspm", c.peer_p75.get("vspm", 1.1)),
    ),
    StepSpec(
        key="early_vision", category=CATEGORY_VISION, specificity=2,
        build=lambda c: _stepped(
            c, column="vspm10", template="{target} vision score per minute before 10, in 15 of 20",
            precision=2,
        ),
        severity=lambda c: _shortfall(c, "vspm10", 1.0),
    ),
    StepSpec(
        key="ward_clearing", category=CATEGORY_VISION, specificity=2,
        build=lambda c: _stepped(
            c, column="wards_killed", template="{target} enemy wards cleared in 15 of 20 games",
            precision=1,
        ),
        severity=lambda c: _shortfall(c, "wards_killed", 5.0),
    ),
    StepSpec(
        key="pit_vision", category=CATEGORY_VISION, specificity=3,
        build=lambda c: _stepped(
            c, column="avg_wards_before_objective",
            template="{target} wards down before an objective, in 15 of 20 games", precision=1,
        ),
        severity=lambda c: _shortfall(c, "avg_wards_before_objective", 2.0),
    ),
    StepSpec(
        key="wards_placed", category=CATEGORY_VISION, specificity=1,
        build=lambda c: _stepped(
            c, column="wards_placed", template="{target} wards placed in 15 of 20 games", precision=0
        ),
        severity=lambda c: _shortfall(c, "wards_placed", 14.0),
    ),
    # --- Fight --------------------------------------------------------------
    StepSpec(
        key="damage_share", category=CATEGORY_FIGHT, specificity=1,
        build=lambda c: _stepped(
            c, column="damage_share", cap=1.0,
            template="{target}% team damage share in 15 of 20 games",
            precision=3, display_scale=100.0, display_precision=0,
        ),
        severity=lambda c: _shortfall(c, "damage_share", c.peer_p75.get("damage_share", 0.25)),
    ),
    StepSpec(
        key="fight_selection", category=CATEGORY_FIGHT, specificity=3,
        build=lambda c: _stepped(
            c, column="pct_advantaged_fights", cap=1.0,
            template="Take {target}% of your fights with numbers up, in 15 of 20 games",
            precision=3, display_scale=100.0, display_precision=0,
        ),
        severity=lambda c: _shortfall(c, "pct_advantaged_fights", 0.5),
    ),
    StepSpec(
        key="kill_participation_15", category=CATEGORY_FIGHT, specificity=2,
        build=lambda c: _stepped(
            c, column="kp15", cap=1.0,
            template="{target}% kill participation by 15 min, in 15 of 20 games",
            precision=3, display_scale=100.0, display_precision=0,
        ),
        severity=lambda c: _shortfall(c, "kp15", 0.6),
    ),
    StepSpec(
        key="teamfights_won", category=CATEGORY_FIGHT, specificity=2,
        build=lambda c: _stepped(
            c, column="tf_won_share", cap=1.0,
            template="Win {target}% of the teamfights you join, in 15 of 20 games",
            precision=3, display_scale=100.0, display_precision=0,
        ),
        severity=lambda c: _shortfall(c, "tf_won_share", 0.5),
    ),
    StepSpec(
        key="cc_per_minute", category=CATEGORY_FIGHT, specificity=2,
        build=lambda c: _stepped(
            c, column="ccpm", template="{target} CC score per minute in 15 of 20 games", precision=1
        ),
        severity=lambda c: _shortfall(c, "ccpm", c.peer_p75.get("ccpm", 10.0)),
    ),
    # --- Economy ------------------------------------------------------------
    StepSpec(
        key="first_item_timing", category=CATEGORY_ECONOMY, specificity=2,
        build=lambda c: _stepped_under(
            c, column="first_item_min", template="First item by {target} minutes in 15 of 20 games",
            precision=1,
        ),
        severity=lambda c: min(1.0, max(0.0, (_mean_of(c, "first_item_min") - 9.0) / 6.0)),
    ),
    StepSpec(
        key="gold_banking", category=CATEGORY_ECONOMY, specificity=3,
        build=lambda c: _stepped_under(
            c, column="avg_unspent_gold",
            template="Carry under {target}g unspent in 15 of 20 games", precision=0,
        ),
        severity=lambda c: min(1.0, _mean_of(c, "avg_unspent_gold") / 1500.0),
    ),
    StepSpec(
        key="gold_at_death", category=CATEGORY_ECONOMY, specificity=3,
        build=lambda c: _stepped_under(
            c, column="avg_gold_at_death",
            template="Die holding under {target}g in 15 of 20 games", precision=0,
        ),
        severity=lambda c: min(1.0, _mean_of(c, "avg_gold_at_death") / 1500.0),
    ),
    StepSpec(
        key="first_recall", category=CATEGORY_ECONOMY, specificity=2,
        build=lambda c: _stepped_under(
            c, column="first_recall_min", template="First recall by {target} minutes in 15 of 20 games",
            precision=1,
        ),
        severity=lambda c: min(1.0, max(0.0, (_mean_of(c, "first_recall_min") - 4.0) / 6.0)),
    ),
    # --- Utility (support) --------------------------------------------------
    StepSpec(
        key="utility_cc", category=CATEGORY_UTILITY, specificity=2, roles=("UTILITY",),
        build=lambda c: _stepped(
            c, column="ccpm", template="{target} CC score per minute in 15 of 20 games", precision=1
        ),
        severity=lambda c: _shortfall(c, "ccpm", c.peer_p75.get("ccpm", 10.0)),
    ),
    StepSpec(
        key="utility_roams", category=CATEGORY_UTILITY, specificity=2, roles=("UTILITY", "JUNGLE"),
        build=lambda c: _stepped(
            c, column="roams_pre15", template="{target} roams before 15 min in 15 of 20 games",
            precision=1,
        ),
        severity=lambda c: _shortfall(c, "roams_pre15", c.peer_p75.get("roams_pre15", 3.0)),
    ),
    StepSpec(
        key="utility_sustain", category=CATEGORY_UTILITY, specificity=2, roles=("UTILITY",),
        build=lambda c: _stepped(
            c, column="hpm", template="{target} healing per minute in 15 of 20 games", precision=0
        ),
        severity=lambda c: _shortfall(c, "hpm", 300.0),
    ),
    StepSpec(
        key="utility_pit_vision", category=CATEGORY_UTILITY, specificity=3, roles=("UTILITY",),
        build=lambda c: _stepped(
            c, column="avg_wards_before_objective",
            template="{target} wards down before an objective, in 15 of 20 games", precision=1,
        ),
        severity=lambda c: _shortfall(c, "avg_wards_before_objective", 2.0),
    ),
)

STEPS_BY_KEY: Final[dict[str, StepSpec]] = {step.key: step for step in STEP_BANK}

# Every category a block can be built from, in tie-break order.
BLOCK_CATEGORY_KEYS: Final[tuple[str, ...]] = (
    CATEGORY_LANING,
    CATEGORY_SURVIVAL,
    CATEGORY_OBJECTIVES,
    CATEGORY_VISION,
    CATEGORY_FIGHT,
    CATEGORY_ECONOMY,
    CATEGORY_UTILITY,
)


def steps_for_category(category: str, role: str) -> list[StepSpec]:
    """Every step in a category's bank that this role is offered."""
    return [
        step for step in STEP_BANK if step.category == category and step.serves_role(role)
    ]


def rank_steps(steps: Sequence[StepSpec], ctx) -> list[StepSpec]:
    """Steps ordered by what the player most needs to work on.

    Most specific *applicable* step first: a diagnosis only outranks a stretch goal
    when its evidence actually fired for this player, then by how badly it is
    missed. A step whose evidence is absent keeps its place in the bank but drops
    to stretch-goal rank, so nobody is told to stop a habit they do not have.
    """
    order = {step.key: index for index, step in enumerate(steps)}
    scored = [(step, _safe_severity(step, ctx)) for step in steps]
    return [
        step
        for step, _ in sorted(
            scored,
            key=lambda pair: (
                -effective_specificity(pair[0], pair[1]),
                -pair[1],
                order[pair[0].key],
            ),
        )
    ]


def effective_specificity(step: StepSpec, severity: float) -> int:
    """A step's specificity, demoted to a stretch goal when its evidence is absent."""
    return step.specificity if severity > DIAGNOSIS_FLOOR else 1


def _safe_severity(step: StepSpec, ctx) -> float:
    """Severity, treating a missing column or bad data as "not a problem"."""
    try:
        value = float(step.severity(ctx))
    except Exception:  # noqa: BLE001 - a broken severity must not break the ladder
        return 0.0
    return value if value == value else 0.0  # NaN check
