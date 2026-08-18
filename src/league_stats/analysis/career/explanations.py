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

from typing import Final

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
        "Share of objective trades (giving up one objective or structure to take "
        "another) that actually came out ahead. Trading is fine -- trading down isn't."
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
        "Healing or shielding delivered to allies per minute. This is the support "
        "stat line that keeps your team's damage dealers alive through a fight."
    ),
    "roams_pre15": (
        "Roams to another lane detected before 15 minutes. Early roams snowball lanes "
        "other than your own, which is often more valuable than farming in a lane "
        "that's already even."
    ),
}
