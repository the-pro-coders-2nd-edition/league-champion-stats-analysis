"""Tests for gradient metric color helpers."""

from __future__ import annotations

from league_stats.presentation.metric_colors import (
    BLUE_HEX,
    LOSS_HEX,
    NEUTRAL_HEX,
    TEAL_HEX,
    WIN_HEX,
    color_winrate,
    interpolate_metric_color,
    normalize_deaths_for_duration,
    score_deaths_per_game,
    score_lane_diff,
    score_winrate,
)


def test_interpolate_metric_color_endpoints() -> None:
    assert interpolate_metric_color(-1.0) == LOSS_HEX
    assert interpolate_metric_color(1.0) == BLUE_HEX


def test_interpolate_metric_color_midpoint_is_neutral() -> None:
    assert interpolate_metric_color(0.0) == NEUTRAL_HEX


def test_interpolate_metric_color_goes_green_then_teal_then_blue() -> None:
    """Slightly positive scores should already read green (like score bars)."""
    from league_stats.presentation.metric_colors import _POS_GREEN, _POS_TEAL

    assert interpolate_metric_color(_POS_GREEN) == WIN_HEX
    assert interpolate_metric_color(_POS_TEAL) == TEAL_HEX
    # Mild positive is already much closer to green than gold.
    assert _channel_closer_to(interpolate_metric_color(0.2), WIN_HEX, NEUTRAL_HEX)
    assert _channel_closer_to(interpolate_metric_color(0.85), BLUE_HEX, WIN_HEX)


def _hex_channels(hex_color: str) -> tuple[int, int, int]:
    value = hex_color.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _channel_closer_to(color: str, target: str, other: str) -> bool:
    cr, cg, cb = _hex_channels(color)
    tr, tg, tb = _hex_channels(target)
    or_, og, ob = _hex_channels(other)
    dist_target = (cr - tr) ** 2 + (cg - tg) ** 2 + (cb - tb) ** 2
    dist_other = (cr - or_) ** 2 + (cg - og) ** 2 + (cb - ob) ** 2
    return dist_target < dist_other


def test_score_winrate_is_centered_on_fifty_percent() -> None:
    assert score_winrate(50.0) == 0.0
    assert score_winrate(70.0) == 1.0
    assert score_winrate(30.0) == -1.0


def test_score_lane_diff_scales_signed_gold() -> None:
    assert score_lane_diff(300.0) == 1.0
    assert score_lane_diff(-300.0) == -1.0


def test_score_lane_diff_uses_tighter_cs_span() -> None:
    from league_stats.presentation.metric_colors import CS_DIFF_SPAN, score_form_delta

    assert score_lane_diff(CS_DIFF_SPAN, span=CS_DIFF_SPAN) == 1.0
    assert score_form_delta("csd10", 7.5) == 0.5
    assert score_form_delta("gd10", 150.0) == 0.5


def test_score_deaths_prefers_fewer_deaths() -> None:
    assert score_deaths_per_game(3.0) == 1.0
    assert score_deaths_per_game(6.0) == -1.0


def test_score_deaths_scales_with_game_length() -> None:
    short_game = score_deaths_per_game(4.0, duration_min=20.0)
    long_game = score_deaths_per_game(4.0, duration_min=40.0)
    assert short_game < long_game
    assert normalize_deaths_for_duration(4.0, 20.0) == 6.0


def test_color_winrate_graduates_near_fifty() -> None:
    assert color_winrate(0.5) == interpolate_metric_color(0.0)
    assert color_winrate(0.53) != color_winrate(0.47)
