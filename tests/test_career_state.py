"""Career goal state machine."""

from __future__ import annotations

from league_stats_runner.analysis.career.models import CLEAR_BAR, SETUP_CLEAR_BAR, hold_bar
from league_stats_runner.analysis.career.state import block_is_complete, transition


def test_hold_bar_is_three_quarters_of_the_clear_bar() -> None:
    assert hold_bar(CLEAR_BAR) == 11
    assert hold_bar(SETUP_CLEAR_BAR) == 9
    assert hold_bar(1) == 1


def test_never_cleared_goal_stays_in_progress() -> None:
    assert transition("In progress", 0, 15) == "In progress"
    assert transition("In progress", 14, 15) == "In progress"


def test_reaching_the_clear_bar_clears() -> None:
    assert transition("In progress", 15, 15) == "Cleared"
    assert transition("Revoked", 17, 15) == "Cleared"


def test_cleared_goal_above_hold_bar_is_at_risk() -> None:
    assert transition("Cleared", 12, 15) == "At risk"
    assert transition("At risk", 11, 15) == "At risk"


def test_cleared_goal_below_hold_bar_is_revoked() -> None:
    assert transition("Cleared", 10, 15) == "Revoked"
    assert transition("At risk", 8, 15) == "Revoked"
    assert transition("Revoked", 8, 15) == "Revoked"


def test_transition_never_returns_locked() -> None:
    for old in ("Locked", "In progress", "At risk", "Cleared", "Revoked"):
        for hit in range(0, 21):
            assert transition(old, hit, 15) != "Locked"


def test_block_is_complete() -> None:
    assert block_is_complete(["Cleared", "Cleared", "At risk"])
    assert not block_is_complete(["Cleared", "Cleared", "In progress"])
    assert not block_is_complete([])
