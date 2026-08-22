"""Parity tests for league_stats.presentation.tones against report-tones.js.

Worked examples are taken from the Claude Design project's own demo data
(project f86e4399-0a80-4039-afd7-d141337da4ec, "Report Design System.dc.html")
so both the JS and Python sides are checked against the same known-good cases.
"""

from __future__ import annotations

import pytest

from league_stats.presentation.tones import (
    band_verdict,
    career_count,
    career_node,
    delta_label,
    delta_tone,
    focus_tone,
    p_value,
    priority_tone,
    verdict,
    verdict_tone,
)
from league_stats.pipeline.bundles import _overall_score_verdict, _score_verdict_sentence


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (74, ("Strength", "good")),
        (58, ("Solid", "solid")),
        (51, ("Steady", "flat")),
        (62, ("Solid", "solid")),
        (35, ("Watch", "warn")),
        (34, ("Focus", "bad")),
        (40, ("Watch", "warn")),  # boundary: exactly 40
        (45, ("Steady", "flat")),  # boundary: exactly 45
        (55, ("Solid", "solid")),  # boundary: exactly 55
        (65, ("Strength", "good")),  # boundary: exactly 65
    ],
)
def test_verdict_matches_design_system_demo_scores(score: float, expected: tuple[str, str]) -> None:
    assert verdict(score) == expected


def test_verdict_is_band_verdict() -> None:
    """tones.verdict is an alias of band_verdict -- RFC-001 Open Question #5."""
    for score in (0, 34, 35, 44.9, 45, 54.9, 55, 64.9, 65, 100):
        assert verdict(score) == band_verdict(score)


@pytest.mark.parametrize(
    "score",
    [0, 12, 34, 35, 39.9, 40, 44.9, 45, 51, 54.9, 55, 58, 64.9, 65, 74, 100],
)
def test_one_score_one_verdict_across_tones_and_bundles(score: float) -> None:
    """A single score must produce one verdict everywhere it's rendered.

    Regression test for RFC-001 Open Question #5: tones.verdict and
    bundles.py's _overall_score_verdict / _score_verdict_sentence must delegate
    to tones.band_verdict so every surface agrees on label and tone.
    """
    label, tone = band_verdict(score)

    _, overall_label = _overall_score_verdict(score)
    assert overall_label == label

    _, _, sentence_label = _score_verdict_sentence("Laning", score, is_biggest_gap=False)
    assert sentence_label == label


@pytest.mark.parametrize(
    ("matchup_verdict", "expected_tone"),
    [
        ("favorable", "good"),
        ("lean_favorable", "good"),
        ("even", "warn"),  # gold-toned in the matchup table, not neutral
        ("lean_unfavorable", "bad"),
        ("unfavorable", "bad"),
        ("thin_sample", "flat"),
    ],
)
def test_verdict_tone(matchup_verdict: str, expected_tone: str) -> None:
    assert verdict_tone(matchup_verdict) == expected_tone


def test_focus_tone_known_keys() -> None:
    assert focus_tone("snowball") == "good"
    assert focus_tone("survive") == "bad"
    assert focus_tone("convert") == "warn"
    assert focus_tone("standard") == "flat"
    assert focus_tone("unknown-key") == "flat"


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


def test_overall_score_color_for_solid() -> None:
    color, label = _overall_score_verdict(58)
    assert label == "Solid"
    assert color == "var(--tone-solid-fg)"


def test_score_verdict_sentence_steady_and_watch() -> None:
    text, pulse, label = _score_verdict_sentence("Vision", 50, is_biggest_gap=False)
    assert label == "Steady"
    assert pulse == "steady"
    assert "steady" in text

    text, pulse, label = _score_verdict_sentence("Vision", 38, is_biggest_gap=False)
    assert label == "Watch"
    assert pulse == "watch"
    assert "watching" in text


def test_refresh_score_verdicts_in_report_updates_baked_labels() -> None:
    from league_stats.pipeline.bundles import refresh_score_verdicts_in_report

    payload = {
        "score": 53.0,
        "score_verdict_label": "Solid",
        "score_color": "var(--color-text)",
        "score_components": [
            {"name": "Fight", "score": 53.0, "verdict": "Solid", "tone": "flat"},
        ],
        "negative_recommendations": [],
        "report_views": {
            "solo": {
                "windows": {
                    "all": {
                        "score": 58.0,
                        "score_verdict_label": "Solid",
                        "score_color": "var(--color-text)",
                        "score_components": [
                            {"name": "Economy", "score": 58.0, "verdict": "Solid", "tone": "flat"},
                        ],
                        "negative_recommendations": [],
                    }
                }
            }
        },
    }
    refresh_score_verdicts_in_report(payload)
    assert payload["score_verdict_label"] == "Steady"
    assert payload["score_components"][0]["verdict"] == "Steady"
    window = payload["report_views"]["solo"]["windows"]["all"]
    assert window["score_verdict_label"] == "Solid"
    assert window["score_color"] == "var(--tone-solid-fg)"
    assert window["score_components"][0]["verdict"] == "Solid"
