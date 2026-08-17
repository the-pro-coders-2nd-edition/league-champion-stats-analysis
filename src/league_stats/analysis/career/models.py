"""Core Career mode value types and bars."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

GoalState = Literal["Locked", "In progress", "At risk", "Cleared", "Revoked"]
Comparator = Literal["at_least", "under"]

GOAL_STATES: Final[tuple[str, ...]] = (
    "Locked",
    "In progress",
    "At risk",
    "Cleared",
    "Revoked",
)

WINDOW: Final[int] = 20
CLEAR_BAR: Final[int] = 15
SETUP_CLEAR_BAR: Final[int] = 12
HOLD_RATIO: Final[float] = 0.75
GOALS_PER_BLOCK: Final[int] = 3
BLOCK_SLOTS: Final[int] = 3

# A goal counts toward its block while it is Cleared or drifting but still above
# the hold bar; anything else stops the block and locks the goals after it.
SATISFIED: Final[frozenset[str]] = frozenset({"Cleared", "At risk"})


def hold_bar(need: int) -> int:
    """Games needed to keep a cleared goal, 75% of its clear bar."""
    return max(1, round(need * HOLD_RATIO))


@dataclass(frozen=True)
class Rung:
    """One frozen goal definition: a target on a column over the window."""

    text: str
    column: str
    comparator: Comparator
    target: float
    need: int


@dataclass(frozen=True)
class StoredGoal:
    """A persisted goal: where it sits, what it asks for, and its state."""

    slot: int
    goal_index: int
    track_key: str
    rung: Rung
    state: str
