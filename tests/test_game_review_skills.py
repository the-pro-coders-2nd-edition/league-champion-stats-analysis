"""Tests for game review skill progression helpers."""

from __future__ import annotations

from league_stats_runner.core.skills import build_skill_levels_by_level


def test_build_skill_levels_by_level_tracks_cumulative_ranks() -> None:
    sequence = ["Q", "E", "W", "Q", "Q", "Q", "Q", "R"]
    rows = build_skill_levels_by_level(sequence)
    assert len(rows) == 18
    assert rows[0] == {"Q": 1, "W": 0, "E": 0, "R": 0}
    assert rows[1] == {"Q": 1, "W": 0, "E": 1, "R": 0}
    assert rows[2] == {"Q": 1, "W": 1, "E": 1, "R": 0}
    assert rows[6] == {"Q": 5, "W": 1, "E": 1, "R": 0}
    assert rows[7] == {"Q": 5, "W": 1, "E": 1, "R": 1}
    assert rows[17] == rows[7]
