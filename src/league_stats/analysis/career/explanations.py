"""Plain-language "why does this goal exist" text, keyed by the column a Rung targets.

Career goals are generated from a stats column and a stretch target (see
``career/steps.py`` and ``career/tracks.py``), so their auto-built text reads like
"0.8 wards down before an objective, in 15 of 20 games" -- accurate, but it assumes
the reader already knows what the column means and why a coach would ask for it.
This dict is looked up by every Rung-building helper in those two modules and
threaded through to the frontend as a tooltip, so each goal explains itself instead
of leaving the player to guess.
"""

from __future__ import annotations

from typing import Any, Final

import pandas as pd

from league_stats.analysis.career.models import WINDOW
from league_stats.analysis.career.window import recent_window, series_hits

WHY_BY_COLUMN: Final[dict[str, str]] = {
    # --- Laning / early game -------------------------------------------------
    "cspm": (
        "Creep score per minute across the whole game. More farm means more gold, "
        "which buys the items that make every other stat on this page easier."
    ),
    "gd10": (
        "Your gold minus your lane opponent's at the 10-minute mark. Ending lane "
        "ahead means you hit your power spikes first and can force fights on your terms."
    ),
    "gd15": (
        "Your gold lead over your lane opponent at 15 minutes. A lead that survives "
        "past laning phase is one that actually converts into map pressure."
    ),
    "gd20": (
        "Your gold lead over your lane opponent at 20 minutes. Leads bleed out over "
        "time without objectives or fights to spend them on -- holding this one shows "
        "you're using it, not just banking it."
    ),
    "xpd10": (
        "Your experience lead over your lane opponent at 10 minutes. An XP lead "
        "means a level advantage, which usually means winning any trade or all-in."
    ),
    "under_enemy_tower_laning_deaths": (
        "Deaths taken while standing under the enemy's own turret during laning. "
        "These are avoidable -- they mean overextending past the point your team can help."
    ),
    "gank_deaths_laning": (
        "Deaths before 14 minutes to someone other than your direct lane opponent, "
        "i.e. getting caught by a jungler or a roamer. Vision and lane positioning "
        "prevent most of these."
    ),
    "cs10": (
        "Total creep score at the 10-minute mark. An early CS lead compounds into a "
        "gold lead for the rest of the game."
    ),
    "gold10": (
        "Total gold at the 10-minute mark (jungle-specific: farm plus early ganks). "
        "An early gold lead lets you buy your jungle item upgrade sooner and clear faster."
    ),
    "early_ganks": (
        "Successful ganks landed before 15 minutes. Early ganks swing lanes before "
        "either side has scaled, which is when a single kill matters most."
    ),
    # --- Survival --------------------------------------------------------------
    "deaths_pre20": (
        "Deaths before the 20-minute mark. Each early death hands the enemy gold and "
        "map control while you're on a respawn timer, so cutting these has an outsized "
        "effect on the rest of the game."
    ),
    "deaths_pre14": (
        "Deaths before 14 minutes, the laning phase. Dying in lane loses the trade, "
        "the gold, and often the wave -- three losses for one death."
    ),
    "greed_deaths": (
        "Deaths shortly after pushing deep into the side lane alone. These come from "
        "chasing one more wave of gold past the point it's safe to."
    ),
    "solo_deaths": (
        "Deaths with no allies nearby to help or trade back. Isolated deaths are pure "
        "loss -- no allied backup means no chance to turn the fight around."
    ),
    "outnumbered_deaths": (
        "Deaths where more enemies were nearby than allies. Dying outnumbered usually "
        "means being caught out of position rather than losing a fair fight."
    ),
    "shutdown_given": (
        "Gold handed to the enemy team as a shutdown bounty for killing you after you "
        "were fed. Avoiding these keeps your lead from funding the enemy's comeback."
    ),
    "time_dead_s": (
        "Total seconds spent on the respawn timer. Every second dead is a second not "
        "farming, not grouping, and not contesting objectives -- it adds up fast."
    ),
    "deaths_before_neutral_objective": (
        "Deaths in the setup window right before a dragon, herald, or baron take. "
        "Dying here doesn't just cost you -- it usually costs your team the objective too."
    ),
    # --- Objectives --------------------------------------------------------------
    "objectives_present_rate": (
        "Share of epic monster takes (dragon, herald, baron) where you were near the "
        "pit. Missing objectives means your team fights 4-vs-5, or gets nothing for "
        "the vision and setup it spent getting there."
    ),
    "control_wards": (
        "Control wards bought per game. A single control ward denies the enemy vision "
        "of an entire objective pit, which is often worth more than the gold it costs."
    ),
    "tf_participation": (
        "Share of detected teamfights where you took part -- landed a kill or assist, "
        "died, or were close enough to matter. Sitting fights out means your team is "
        "effectively down a player."
    ),
    "objective_trade_success_rate": (
        "Share of cross-map trade attempts where your team came out ahead on map "
        "value. A trade attempt is an epic objective where you were split-pushing, "
        "defending against a split, or a tower or epic swung within about 90 seconds "
        "before to 45 seconds after the pit timer. Gains and losses are scored in "
        "points — plate 0.5, outer turret 2, inner 3, inhib 6; Herald 3, Grubs 2, "
        "Dragon 4, Baron 8, Elder 10 — and the attempt counts as a win when your net "
        "is even or positive (you held while they got nothing, took their tower while "
        "they got the epic, and so on). Securing an epic with no nearby structure "
        "swing is not counted as a trade."
    ),
    "unproductive_absence_rate": (
        "Share of objective takes you skipped without getting anything for it "
        "elsewhere -- no side-lane pressure, no defended tower, no won trade. Skipping "
        "an objective is only worth it if the time bought something."
    ),
    "towers_taken": (
        "Enemy towers you helped bring down. Towers convert a lead into permanent map "
        "space and gold -- a fight won that doesn't take a tower is often wasted."
    ),
    # --- Vision --------------------------------------------------------------
    "vspm": (
        "Vision score per minute. Vision is the main way your team sees ganks and "
        "objective setups coming before they happen, instead of reacting after."
    ),
    "vspm10": (
        "Vision score per minute over just the first 10 minutes. Early vision is what "
        "keeps you safe from ganks before you have the levels or items to fight back."
    ),
    "wards_killed": (
        "Enemy wards you've cleared. Every enemy ward you kill is vision the enemy "
        "paid gold for and loses -- and a blind spot for them going forward."
    ),
    "avg_wards_before_objective": (
        "Wards placed in the two minutes before an epic monster spawns. This is the "
        "vision that actually decides objective fights -- placing it after the fight "
        "starts is too late."
    ),
    "wards_placed": (
        "Total wards placed per game, any type. More eyes on the map means fewer "
        "surprises for your whole team, not just you."
    ),
    # --- Fight --------------------------------------------------------------
    "damage_share": (
        "Your share of your team's total damage to champions. A high share means "
        "fights are actually going through you, not just past you."
    ),
    "pct_advantaged_fights": (
        "Share of fights you joined where your team had more nearby champions than "
        "the enemy. Winning consistently starts with picking fights you're favored in."
    ),
    "kp15": (
        "Share of your team's kills and assists before 15 minutes that you were part "
        "of. High early kill participation means you're where the action is, not "
        "farming alone while your team fights 4-vs-5."
    ),
    "tf_won_share": (
        "Share of the teamfights you joined that your team won. This is the actual "
        "scoreboard for whether showing up to fights is paying off."
    ),
    "ccpm": (
        "Crowd control score per minute. Locking down even one enemy for a couple of "
        "seconds during a fight is often worth more than a kill."
    ),
    # --- Economy --------------------------------------------------------------
    "first_item_min": (
        "The game minute your first completed item finishes. Hitting your first power "
        "spike earlier means you win more of the fights that happen right after it."
    ),
    "avg_unspent_gold": (
        "Gold sitting in your inventory, unspent, right before you recall. Banked gold "
        "does nothing for you on the map -- it's only useful once it's items."
    ),
    "avg_gold_at_death": (
        "Gold you were carrying when you died. Dying with a full inventory of unspent "
        "gold hands the enemy a bigger bounty for a death that also cost you a recall's "
        "worth of shopping."
    ),
    "first_recall_min": (
        "The game minute of your first back-to-base trip. Recalling too late means "
        "farming with a smaller inventory than you could have had, for no benefit."
    ),
    # --- Utility (support) --------------------------------------------------
    "hpm": (
        "Healing delivered to allies per minute. This is the enchanter stat line "
        "that keeps your team's damage dealers alive through a fight."
    ),
    "spm": (
        "Shielding delivered to allies per minute. This is the peel stat line that "
        "absorbs burst before your carries take it."
    ),
    "roams_pre15": (
        "Roams to another lane detected before 15 minutes. Early roams snowball lanes "
        "other than your own, which is often more valuable than farming in a lane "
        "that's already even."
    ),
}


# --- player-specific evidence ----------------------------------------------
#
# The dict above explains what a column *is*. On its own that reads as advice, and
# advice is easy to dismiss: a reader has no way to tell whether the goal was
# picked for them or handed to everyone. These helpers add the numbers that make
# the case -- where they sit today, how many recent games already clear the target,
# and what players at their rank manage -- so the tooltip argues rather than asserts.

# column -> (display scale, decimal places, suffix). Values are shown in the unit
# the goal sentence itself uses, so "60%" in the tooltip means the same 60% as the
# goal above it.
_UNITS: Final[dict[str, tuple[float, int, str]]] = {
    "damage_share": (100.0, 0, "%"),
    "damage_taken_share": (100.0, 0, "%"),
    "objectives_present_rate": (100.0, 0, "%"),
    "tf_participation": (100.0, 0, "%"),
    "tf_won_share": (100.0, 0, "%"),
    "kp15": (100.0, 0, "%"),
    "kill_participation": (100.0, 0, "%"),
    "pct_advantaged_fights": (100.0, 0, "%"),
    "objective_trade_success_rate": (100.0, 0, "%"),
    "unproductive_absence_rate": (100.0, 0, "%"),
    "lane_priority": (100.0, 0, "%"),
    "avg_unspent_gold": (1.0, 0, "g"),
    "avg_unspent_gold_per_fight": (1.0, 0, "g"),
    "avg_gold_at_death": (1.0, 0, "g"),
    "shutdown_given": (1.0, 0, "g"),
    "gold10": (1.0, 0, "g"),
    "gd10": (1.0, 0, "g"),
    "gd15": (1.0, 0, "g"),
    "gd20": (1.0, 0, "g"),
    "xpd10": (1.0, 0, " XP"),
    "time_dead_s": (1.0, 0, "s"),
    "cspm": (1.0, 1, ""),
    "vspm": (1.0, 2, ""),
    "vspm10": (1.0, 2, ""),
    "ccpm": (1.0, 1, ""),
    "hpm": (1.0, 0, ""),
    "spm": (1.0, 0, ""),
    "first_item_min": (1.0, 1, ""),
    "first_recall_min": (1.0, 1, ""),
}
_DEFAULT_UNIT: Final[tuple[float, int, str]] = (1.0, 1, "")

def format_value(column: str, value: float) -> str:
    """One metric value in the same units the goal sentence uses."""
    scale, places, suffix = _UNITS.get(column, _DEFAULT_UNIT)
    shown = value * scale
    text = f"{shown:.{places}f}"
    # A whole number reads as spurious precision with a trailing ".0", but rounding
    # every count to an integer made the tooltip contradict the goal above it: a
    # "2.8 enemy wards cleared" goal was explained as "at least 3".
    if places and text.endswith("." + "0" * places):
        text = text[: -(places + 1)]
    return f"{text}{suffix}"


def _hits(frame: Any, column: str, target: float, comparator: str) -> int:
    series = pd.to_numeric(frame[column], errors="coerce").dropna()
    return series_hits(series, comparator, target)


def why_for(
    column: str,
    ctx: Any,
    *,
    target: float,
    comparator: str,
    need: int,
) -> str:
    """The metric's explanation, followed by this player's numbers on it.

    Degrades to the plain explanation when there is no usable history, so a thin
    sample never produces a sentence quoting a number that is not there.
    """
    reason = WHY_BY_COLUMN.get(column, "")
    frame = getattr(ctx, "matches_df", None)
    if frame is None or getattr(frame, "empty", True) or column not in frame.columns:
        return reason

    recent = recent_window(frame, WINDOW)
    series = pd.to_numeric(recent[column], errors="coerce").dropna()
    if series.empty:
        return reason

    typical = float(series.median())
    hits = _hits(recent, column, target, comparator)
    if comparator == "under":
        direction = "under"
    elif comparator == "at_most":
        direction = "at most"
    else:
        direction = "at least"
    parts = [
        f"Your numbers: you are at {format_value(column, typical)} in a typical game, "
        f"and {hits} of your last {len(series)} games already stay "
        f"{direction} {format_value(column, target)} — the goal asks for {need} of {WINDOW}."
    ]
    peer = (getattr(ctx, "peer_p75", None) or {}).get(column)
    if peer is not None:
        parts.append(
            f"The top quarter of players at your rank sit around "
            f"{format_value(column, float(peer))}."
        )
    evidence = " ".join(parts)
    return f"{reason}\n\n{evidence}" if reason else evidence
