"""Goal state transitions."""

from __future__ import annotations

from typing import Sequence

from league_stats.analysis.career.models import SATISFIED, hold_bar


def transition(old_state: str, hit: int, need: int) -> str:
    """Advance one goal's state from its stored state and this window's hits.

    ``Locked`` is never returned: it is a display-time label for blocks that are
    not live yet. Every goal inside the live block is measured in parallel.
    """
    if hit >= need:
        return "Cleared"
    if old_state in {"Cleared", "At risk", "Revoked"}:
        return "At risk" if hit >= hold_bar(need) else "Revoked"
    return "In progress"


def block_is_complete(states: Sequence[str]) -> bool:
    """Whether every goal in a block currently counts toward it."""
    return bool(states) and all(state in SATISFIED for state in states)
