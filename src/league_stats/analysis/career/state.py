"""Goal state transitions and the revocation lock cascade."""

from __future__ import annotations

from typing import Sequence

from league_stats.analysis.career.models import SATISFIED, hold_bar


def transition(old_state: str, hit: int, need: int) -> str:
    """Advance one goal's state from its stored state and this window's hits.

    ``Locked`` is never returned: locking is a display-time cascade, so a goal's
    own progress keeps accruing even while an earlier goal in its block is open.
    """
    if hit >= need:
        return "Cleared"
    if old_state in {"Cleared", "At risk", "Revoked"}:
        return "At risk" if hit >= hold_bar(need) else "Revoked"
    return "In progress"


def apply_lock_overlay(states: Sequence[str]) -> list[str]:
    """Display states for one block: everything after an open goal reads Locked."""
    displayed: list[str] = []
    blocked = False
    for state in states:
        if blocked:
            displayed.append("Locked")
            continue
        displayed.append(state)
        if state not in SATISFIED:
            blocked = True
    return displayed


def block_is_complete(states: Sequence[str]) -> bool:
    """Whether every goal in a block currently counts toward it."""
    return bool(states) and all(state in SATISFIED for state in states)
