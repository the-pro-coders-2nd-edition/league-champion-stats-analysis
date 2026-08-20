"""The lightweight "welcome back" summary computed from a single match doc."""

from __future__ import annotations

from league_stats.web.welcome_back import compute_welcome_back_summary
from tests.fixtures import MY_PUUID, make_player_match


def test_computes_win_kda_cs_per_min_and_damage_share() -> None:
    match = make_player_match("EUW1_1234", duration_s=1200)

    summary = compute_welcome_back_summary(match, MY_PUUID)

    # Hand-verified against tests/fixtures.py's make_participant():
    # kills=7, deaths=2, assists=5, totalMinionsKilled=180,
    # neutralMinionsKilled=8, duration=1200s (20 min), teamId=100 (win).
    assert summary["match_id"] == "EUW1_1234"
    assert summary["champion"] == "Viktor"
    assert summary["win"] is True
    assert summary["kills"] == 7
    assert summary["deaths"] == 2
    assert summary["assists"] == 5
    assert summary["kda"] == 6.0  # (7 + 5) / 2
    assert summary["cs"] == 188  # 180 + 8
    assert summary["cs_per_min"] == 9.4  # 188 / 20
    assert summary["damage_to_champions"] == 24000
    # Team damage: 24000 (me) + 4 * 12000 (allies) = 72000.
    assert summary["damage_share"] == 24000 / 72000


def test_a_loss_is_reported_as_such() -> None:
    match = make_player_match("EUW1_5678", duration_s=1500)
    match["info"]["participants"][0]["win"] = False

    summary = compute_welcome_back_summary(match, MY_PUUID)

    assert summary["win"] is False


def test_zero_deaths_does_not_divide_by_zero() -> None:
    match = make_player_match("EUW1_9012")
    match["info"]["participants"][0]["deaths"] = 0

    summary = compute_welcome_back_summary(match, MY_PUUID)

    assert summary["deaths"] == 0
    assert summary["kda"] == 12.0  # (7 + 5) / max(1, 0)
