"""Tests for shared skill-order helpers."""

from __future__ import annotations

from league_stats_runner.core.skills import (
    build_skill_sequence_from_events,
    dedupe_skill_level_up_events,
    skill_display_max_level,
)

SKILL_LETTERS = {1: "Q", 2: "W", 3: "E", 4: "R"}


def _skill_event(timestamp: int, skill_slot: int) -> dict[str, int]:
    return {"timestamp": timestamp, "skillSlot": skill_slot, "participantId": 1}


def test_dedupe_skill_level_up_events_drops_identical_copies() -> None:
    events = [
        _skill_event(1000, 1),
        _skill_event(1000, 1),
        _skill_event(2000, 2),
    ]
    assert dedupe_skill_level_up_events(events) == [_skill_event(2000, 2)]


def test_build_skill_sequence_from_events_caps_invalid_extra_points() -> None:
    events = [_skill_event(1000 + index * 1000, 1) for index in range(10)]
    sequence = build_skill_sequence_from_events(events, SKILL_LETTERS)
    assert sequence == ["Q", "Q", "Q", "Q", "Q"]
    assert len(sequence) <= 18


def test_skill_display_max_level_uses_champ_level() -> None:
    assert skill_display_max_level(15, ["Q", "Q", "Q"]) == 15
    assert skill_display_max_level(0, ["Q", "W", "E"]) == 3
    assert skill_display_max_level(22, ["Q"]) == 18
