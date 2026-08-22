"""Core Career mode value types and bars."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

GoalState = Literal["Locked", "In progress", "At risk", "Cleared", "Revoked"]
Comparator = Literal["at_least", "under", "at_most"]

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
BLOCK_SLOTS: Final[int] = 2

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
    why: str = ""


@dataclass(frozen=True)
class StoredGoal:
    """A persisted goal: where it sits, what it asks for, and its state.

    ``since_ms`` is the game-creation timestamp the block was generated at.
    Only games newer than that count toward it, so a block never inherits
    credit from games played before it existed.

    ``peer_seeded`` is False for a block frozen before peer percentiles existed.
    Its rungs stepped toward the player's own p75 instead of peer p75, so it is
    provisional until a run with peers either rebuilds it or the player starts it.
    """

    slot: int
    goal_index: int
    track_key: str
    rung: Rung
    state: str
    since_ms: int = 0
    peer_seeded: bool = False
