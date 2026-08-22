"""Shared skill-order helpers for ingest and game review."""

from __future__ import annotations

from collections import Counter
from typing import Any, Final

ABILITY_SLOTS: Final[tuple[str, ...]] = ("Q", "W", "E", "R")
MAX_LEVEL: Final[int] = 18
SKILL_SLOT_MAX: Final[dict[str, int]] = {"Q": 5, "W": 5, "E": 5, "R": 3}


def dedupe_skill_level_up_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop duplicated skill-ups from the Match-V5 timeline duplication bug.

    When identical ``SKILL_LEVEL_UP`` events share participant, slot, and timestamp,
  all copies are removed (Riot workaround for patch 15.17+ ghost events).
    """
    counts = Counter(
        (int(event.get("timestamp", 0)), int(event.get("skillSlot", 0))) for event in events
    )
    deduped = [
        event
        for event in events
        if counts[(int(event.get("timestamp", 0)), int(event.get("skillSlot", 0)))] == 1
    ]
    deduped.sort(key=lambda event: int(event.get("timestamp", 0)))
    return deduped


def build_skill_sequence_from_events(
    events: list[dict[str, Any]],
    slot_letters: dict[int, str],
) -> list[str]:
    """Build a validated skill letter sequence from timeline skill-up events."""
    deduped = dedupe_skill_level_up_events(events)
    sequence: list[str] = []
    points = {slot: 0 for slot in ABILITY_SLOTS}
    for event in deduped:
        letter = slot_letters.get(int(event.get("skillSlot", 0)), "?")
        if letter not in SKILL_SLOT_MAX:
            continue
        if points[letter] >= SKILL_SLOT_MAX[letter]:
            continue
        if len(sequence) >= MAX_LEVEL:
            break
        points[letter] += 1
        sequence.append(letter)
    return sequence


def build_skill_levels_by_level(sequence: list[str]) -> list[dict[str, int]]:
    """Map each player level (1–18) to cumulative Q/W/E/R ranks after that level's skill-up."""
    counts = {slot: 0 for slot in ABILITY_SLOTS}
    rows: list[dict[str, int]] = []
    for level in range(1, MAX_LEVEL + 1):
        letter = sequence[level - 1] if level - 1 < len(sequence) else None
        if letter in counts:
            counts[letter] += 1
        rows.append(dict(counts))
    return rows


def skill_display_max_level(champ_level: int, sequence: list[str]) -> int:
    """Levels to show in the skill progression grid (never above 18)."""
    if champ_level > 0:
        return min(MAX_LEVEL, champ_level)
    if sequence:
        return min(MAX_LEVEL, len(sequence))
    return 0


__all__ = [
    "ABILITY_SLOTS",
    "MAX_LEVEL",
    "SKILL_SLOT_MAX",
    "build_skill_levels_by_level",
    "build_skill_sequence_from_events",
    "dedupe_skill_level_up_events",
    "skill_display_max_level",
]
