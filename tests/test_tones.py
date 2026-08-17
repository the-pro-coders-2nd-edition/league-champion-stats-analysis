"""Parity tests for league_stats.presentation.tones against report-tones.js.

Worked examples are taken from the Claude Design project's own demo data
(project f86e4399-0a80-4039-afd7-d141337da4ec, "Report Design System.dc.html")
so both the JS and Python sides are checked against the same known-good cases.
"""

from __future__ import annotations

import pytest

from league_stats.presentation.tones import (
    career_count,
    career_node,
    delta_label,
    delta_tone,
    p_value,
    priority_tone,
    verdict,
)


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (74, ("Strength", "good")),  # demoScores: Laning
        (58, ("Solid", "flat")),  # demoScores: Economy
        (51, ("Solid", "flat")),  # demoScores: Fights
        (62, ("Solid", "flat")),  # demoScores: Survival
        (35, ("Focus", "bad")),  # demoScores: Vision
        (34, ("Focus", "bad")),  # demoScores: Objectives
        (40, ("Watch", "warn")),  # boundary: exactly 40
        (45, ("Solid", "flat")),  # boundary: exactly 45
        (70, ("Strength", "good")),  # boundary: exactly 70
    ],
)
def test_verdict_matches_design_system_demo_scores(score: float, expected: tuple[str, str]) -> None:
    assert verdict(score) == expected


def test_delta_tone_higher_is_better() -> None:
    assert delta_tone(1037, 1) == "good"  # Gold diff @10 demo row: +1037 vs your avg
    assert delta_tone(-10, 1) == "bad"


def test_delta_tone_lower_is_better() -> None:
    """MetricCard 'Deaths / game': delta=-6, polarity=-1 -- lower deaths is an improvement."""
    assert delta_tone(-6, -1) == "good"


def test_delta_tone_flat_cases() -> None:
    assert delta_tone(None, 1) == "flat"
    assert delta_tone(0, 1) == "flat"


def test_delta_tone_warn_band() -> None:
    """Between 0 (exclusive) and -8 (exclusive) in the unfavorable direction is warn, not bad."""
    assert delta_tone(-5, 1) == "warn"
    assert delta_tone(-8.1, 1) == "bad"


def test_delta_label_formats() -> None:
    assert delta_label(1037, 1, "your avg") == "▲ 1037% vs your avg"
    assert delta_label(None) == "no peer baseline"
    assert delta_label(0) == "— 0%"


def test_p_value_formats() -> None:
    assert p_value(0.0001) == "p < 0.001"
    assert p_value(0.001) == "p = 0.001"  # not strictly less than 0.001
    assert p_value(0.05) == "p = 0.050"
    assert p_value(None) == "descriptive"


def test_priority_tone() -> None:
    assert priority_tone("High", "work") == "bad"
    assert priority_tone("High", "keep") == "good"
    assert priority_tone("Medium", "work") == "warn"
    assert priority_tone("Low", "work") == "flat"


def test_career_count() -> None:
    assert career_count("Locked", 0) == "blocked"
    assert career_count("Cleared", 17) == "17 of 20"  # design doc CareerNode demo


def test_career_node_states() -> None:
    assert career_node("Cleared", 17, 15) == {"tone": "good", "pct": 100}
    assert career_node("At risk", 12, 15) == {"tone": "warn", "pct": 80}
    assert career_node("Revoked", 8, 15) == {"tone": "bad", "pct": 53}
    assert career_node("Locked", 0, 15) == {"tone": "flat", "pct": 0}
