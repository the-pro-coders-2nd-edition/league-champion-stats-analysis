"""A goal's "why" has to argue from this player's numbers, and reach locked blocks.

Two gaps in the first version:

* ``WHY_BY_COLUMN`` is keyed only by column, so every player reading a wards goal
  got the same sentence about why vision matters. It never said what *they* do
  today, so it read as advice rather than as evidence that this goal is worth
  training.
* ``_locked_block`` flattened its goals to ``[rung.text]``, dropping ``why``
  entirely, so the tooltip only ever appeared on the live block.
"""

from __future__ import annotations

import pandas as pd
import pytest

from league_stats.analysis.career.engine import CareerBlockState, CareerSnapshot
from league_stats.analysis.career.explanations import WHY_BY_COLUMN, format_value, why_for
from league_stats.analysis.career.models import CLEAR_BAR, Rung, StoredGoal
from league_stats.analysis.career.tracks import TrackContext
from league_stats.presentation.career import build_career_view

HOUR = 3_600_000


def _ctx(values: list[float], column: str = "wards_placed", peers: dict | None = None):
    frame = pd.DataFrame(
        {column: values, "game_creation_ms": [i * HOUR for i in range(len(values))]}
    )
    return TrackContext(
        matches_df=frame,
        objectives_df=pd.DataFrame({"present": [1]}),
        role="MIDDLE",
        peer_p75=peers or {},
    )


# --- the evidence sentence -------------------------------------------------


def test_the_why_quotes_the_players_own_current_level() -> None:
    ctx = _ctx([7.0] * 30)

    why = why_for("wards_placed", ctx, target=9.0, comparator="at_least", need=CLEAR_BAR)

    assert "7" in why


def test_the_why_says_how_many_recent_games_already_clear_the_target() -> None:
    """Half clearing it reads very differently from none clearing it."""
    ctx = _ctx([12.0] * 10 + [4.0] * 10)

    why = why_for("wards_placed", ctx, target=9.0, comparator="at_least", need=CLEAR_BAR)

    assert "10 of" in why
    assert str(CLEAR_BAR) in why


def test_the_why_still_explains_what_the_metric_is() -> None:
    """The evidence is added to the reason, it does not replace it."""
    ctx = _ctx([7.0] * 30)

    why = why_for("cspm", ctx, target=7.5, comparator="at_least", need=CLEAR_BAR)

    assert WHY_BY_COLUMN["cspm"].split(".")[0] in why


def test_the_why_cites_the_peer_number_when_one_exists() -> None:
    ctx = _ctx([1.0] * 30, column="vspm", peers={"vspm": 1.4})

    why = why_for("vspm", ctx, target=1.15, comparator="at_least", need=CLEAR_BAR)

    assert "1.4" in why


def test_the_why_omits_peers_when_the_metric_has_no_peer_data() -> None:
    ctx = _ctx([7.0] * 30)

    why = why_for("wards_placed", ctx, target=9.0, comparator="at_least", need=CLEAR_BAR)

    assert "at your rank" not in why


def test_a_lower_is_better_goal_reads_as_staying_under() -> None:
    ctx = _ctx([300.0] * 30, column="avg_unspent_gold")

    why = why_for(
        "avg_unspent_gold", ctx, target=240.0, comparator="under", need=CLEAR_BAR
    )

    assert "under" in why.lower()


def test_an_unknown_column_still_produces_evidence() -> None:
    ctx = _ctx([5.0] * 30, column="mystery")

    why = why_for("mystery", ctx, target=6.0, comparator="at_least", need=CLEAR_BAR)

    assert "5" in why


def test_no_history_degrades_to_the_plain_reason() -> None:
    ctx = _ctx([], column="cspm")

    why = why_for("cspm", ctx, target=7.0, comparator="at_least", need=CLEAR_BAR)

    assert why == WHY_BY_COLUMN["cspm"]


@pytest.mark.parametrize(
    ("column", "value", "expected"),
    [
        ("damage_share", 0.246, "25%"),
        ("objectives_present_rate", 0.6, "60%"),
        ("avg_unspent_gold", 712.4, "712g"),
        ("time_dead_s", 91.6, "92s"),
        ("cspm", 6.53, "6.5"),
        ("wards_placed", 7.0, "7"),
        ("first_item_min", 10.44, "10.4"),
    ],
)
def test_values_are_formatted_in_the_metric_s_own_units(
    column: str, value: float, expected: str
) -> None:
    assert format_value(column, value) == expected


# --- locked blocks ---------------------------------------------------------


def _goal(slot: int, text: str, why: str) -> StoredGoal:
    return StoredGoal(
        slot=slot,
        goal_index=0,
        track_key="vision",
        rung=Rung(text=text, column="wards_placed", comparator="at_least",
                  target=9.0, need=CLEAR_BAR, why=why),
        state="In progress",
    )


def _snapshot() -> CareerSnapshot:
    live = CareerBlockState(
        slot=0, track_key="survival", goals=[_goal(0, "live goal", "live why")],
        hits=[0], display_states=["In progress"],
    )
    locked = CareerBlockState(
        slot=1, track_key="vision", goals=[_goal(1, "locked goal", "locked why")],
        hits=[0], display_states=["Locked"],
    )
    return CareerSnapshot(blocks=[live, locked])


def test_a_locked_block_carries_its_goals_why_text() -> None:
    view = build_career_view(_snapshot())

    steps = view["blocks"][1]["steps"]
    assert steps[0]["why"] == "locked why"


def test_a_locked_block_still_carries_its_goal_text() -> None:
    view = build_career_view(_snapshot())

    assert view["blocks"][1]["steps"][0]["text"] == "locked goal"


def test_the_live_block_keeps_its_why_too() -> None:
    view = build_career_view(_snapshot())

    assert view["blocks"][0]["goals"][0]["why"] == "live why"


def test_the_target_in_the_tooltip_matches_the_number_in_the_goal() -> None:
    """A "2.8 wards cleared" goal explained as "at least 3" contradicts itself."""
    from league_stats.analysis.career.steps import STEP_BANK
    from league_stats.analysis.career.tracks import TrackContext

    row = {
        "cspm": 6.2, "vspm": 0.92, "damage_share": 0.23, "deaths_pre20": 2.0,
        "deaths_pre14": 1.0, "deaths_before_neutral_objective": 0.3,
        "objectives_present_rate": 0.55, "control_wards": 1.0,
        "tf_participation": 0.66, "first_item_min": 11.2, "gd10": -140.0,
        "gd15": -220.0, "gd20": -260.0, "xpd10": -90.0, "greed_deaths": 0.5,
        "solo_deaths": 0.9, "outnumbered_deaths": 0.6, "shutdown_given": 320.0,
        "time_dead_s": 95.0, "gank_deaths_laning": 0.4,
        "under_enemy_tower_laning_deaths": 0.3, "pct_advantaged_fights": 0.44,
        "objective_trade_success_rate": 0.47, "unproductive_absence_rate": 0.22,
        "towers_taken": 1.8, "vspm10": 0.74, "wards_killed": 2.4,
        "avg_wards_before_objective": 1.1, "avg_unspent_gold": 760.0,
        "avg_gold_at_death": 690.0, "first_recall_min": 5.6, "cs10": 64.0,
        "wards_placed": 7.0, "kp15": 0.52, "tf_won_share": 0.48, "ccpm": 7.4,
        "hpm": 210.0, "gold10": 3400.0, "early_ganks": 1.2, "roams_pre15": 2.0,
    }
    frame = pd.DataFrame({col: [val] * 40 for col, val in row.items()})
    frame["game_creation_ms"] = [i * HOUR for i in range(40)]
    ctx = TrackContext(
        matches_df=frame, objectives_df=pd.DataFrame({"present": [1] * 8}),
        role="MIDDLE", peer_p75={},
    )

    mismatched, checked = [], 0
    for step in STEP_BANK:
        if step.roles:
            continue
        rung = step.build(ctx)
        if rung is None or not rung.why:
            continue
        # Fixed-line and zero-tolerance goals state their line in words -- "Even or
        # ahead in gold at 10 min", "No greed death" -- so there is no number in the
        # sentence for the tooltip to contradict.
        if rung.target in (0.0, 1.0):
            continue
        checked += 1
        shown = format_value(rung.column, rung.target)
        if shown not in rung.text and shown.rstrip("%gs XP") not in rung.text:
            mismatched.append((step.key, shown, rung.text))
    assert checked >= 8, f"only {checked} numeric goals checked; the test lost its teeth"
    assert not mismatched, f"tooltip target disagrees with the goal text: {mismatched}"
