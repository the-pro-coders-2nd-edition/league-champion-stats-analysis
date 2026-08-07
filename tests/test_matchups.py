"""Tests for matchup aggregation and pattern-based advice."""

from __future__ import annotations

import pandas as pd

from league_stats.analysis.matchups import matchup_advice, matchup_recommendation, matchups_dataframe
from league_stats.pipeline.view_models import annotate_matchup_rows, matchup_row_display


def _row(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "opponent": "Syndra",
        "games": 5,
        "wins": 2,
        "winrate": 0.4,
        "avg_gd10": -50.0,
        "avg_gd15": -40.0,
        "avg_xpd10": -20.0,
        "avg_csd10": -2.0,
        "avg_dpm": 600.0,
        "avg_deaths": 4.0,
        "avg_deaths_pre14": 0.6,
        "avg_kills": 2.0,
    }
    base.update(overrides)
    return base


def test_matchups_dataframe_aggregates_by_opponent() -> None:
    matches = pd.DataFrame(
        [
            {
                "opponent": "Ahri",
                "win": 1,
                "gd10": 200,
                "gd15": 300,
                "xpd10": 100,
                "csd10": 5,
                "dpm": 700,
                "deaths": 2,
                "deaths_pre14": 0,
                "kills": 4,
            },
            {
                "opponent": "Ahri",
                "win": 0,
                "gd10": -100,
                "gd15": -50,
                "xpd10": -80,
                "csd10": -3,
                "dpm": 500,
                "deaths": 5,
                "deaths_pre14": 1,
                "kills": 1,
            },
            {
                "opponent": "Zed",
                "win": 1,
                "gd10": 50,
                "gd15": 80,
                "xpd10": 20,
                "csd10": 1,
                "dpm": 650,
                "deaths": 3,
                "deaths_pre14": 0,
                "kills": 3,
            },
        ]
    )
    frame = matchups_dataframe(matches)
    assert list(frame["opponent"]) == ["Ahri", "Zed"]
    ahri = frame.iloc[0]
    assert ahri["games"] == 2
    assert ahri["wins"] == 1
    assert ahri["winrate"] == 0.5


def test_thin_sample_is_soft_read() -> None:
    advice = matchup_advice(_row(games=1, winrate=1.0, wins=1))
    assert advice["verdict"] == "thin_sample"
    assert advice["focus"] == "Sample"
    assert "soft" in advice["recommendation"].lower()


def test_early_deaths_outrank_generic_lane_loss() -> None:
    advice = matchup_advice(
        _row(avg_gd10=-350, avg_deaths_pre14=2.0, winrate=0.3, games=6),
        role="MIDDLE",
    )
    assert advice["focus"] == "Survive"
    assert advice["focus_key"] == "survive"
    assert "deaths" in advice["recommendation"].lower() or "all-in" in advice["recommendation"].lower()


def test_win_lane_lose_game_suggests_convert() -> None:
    advice = matchup_advice(_row(avg_gd10=320, winrate=0.33, games=6, wins=2))
    assert advice["focus"] == "Convert"
    assert "lead" in advice["recommendation"].lower() or "tower" in advice["recommendation"].lower()


def test_lose_lane_win_game_suggests_scale() -> None:
    advice = matchup_advice(_row(avg_gd10=-280, winrate=0.67, games=6, wins=4))
    assert advice["focus"] == "Scale"
    assert "spike" in advice["recommendation"].lower() or "lane" in advice["recommendation"].lower()


def test_recovering_lane_suggests_stabilize() -> None:
    advice = matchup_advice(
        _row(avg_gd10=-220, avg_gd15=80, avg_deaths_pre14=0.4, winrate=0.5, games=4)
    )
    assert advice["focus"] == "Stabilize"


def test_falling_off_suggests_protect_lead() -> None:
    advice = matchup_advice(
        _row(avg_gd10=220, avg_gd15=-80, avg_deaths_pre14=0.4, winrate=0.5, games=4)
    )
    assert advice["focus"] == "Protect lead"


def test_strong_mid_matchup_uses_roam_copy() -> None:
    advice = matchup_advice(
        _row(
            avg_gd10=300,
            avg_gd15=420,
            winrate=0.7,
            games=5,
            wins=4,
            avg_deaths_pre14=0.2,
        ),
        role="MIDDLE",
    )
    assert advice["verdict"] == "favorable"
    assert advice["focus"] == "Snowball"
    assert "roam" in advice["recommendation"].lower()


def test_matchup_recommendation_returns_string() -> None:
    text = matchup_recommendation(_row(avg_deaths_pre14=2.1, games=4))
    assert isinstance(text, str)
    assert text


def test_matchup_row_display_adds_colors_and_verdict() -> None:
    display = matchup_row_display(_row(winrate=0.7, games=5, wins=4, avg_gd10=200))
    assert display["verdict_label"]
    assert "winrate_color" not in display
    assert display["gd10_color"].startswith("#")
    rows = annotate_matchup_rows([_row()], role="TOP")
    assert rows[0]["recommendation"]
