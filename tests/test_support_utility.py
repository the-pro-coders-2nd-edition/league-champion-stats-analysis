"""Tests for support utility composite scoring."""

from __future__ import annotations

import pandas as pd

from league_stats_runner.analysis.game_review.score import compute_game_score
from league_stats_runner.analysis.improvement import support_utility_impact
from league_stats_peers.analysis.peer.comparison import build_comparisons
from league_stats_runner.analysis.support import utility_summary
from league_stats_runner.ingest.parser import ItemCatalog, MatchParser
from tests.fixtures import FAKE_ITEMS, MY_PUUID, make_match, make_timeline

_GOLD = {
    "ccpm": 1.9,
    "damage_share": 0.08,
    "damage_taken_share": 0.20,
    "healing": 7500,
    "shielding": 3500,
}


def _parse_support_row(**participant_overrides) -> dict:
    match = make_match()
    me = match["info"]["participants"][0]
    me["teamPosition"] = "UTILITY"
    me["championName"] = "Thresh"
    me.update(participant_overrides)
    record = MatchParser(ItemCatalog(FAKE_ITEMS)).parse(match, make_timeline(), MY_PUUID)
    return record.to_row()


def test_healing_counts_allies_only() -> None:
    row = _parse_support_row(totalHeal=5000, totalHealsOnTeammates=1200)
    assert row["healing"] == 1200


def test_support_utility_omits_low_shielding_noise() -> None:
    row = _parse_support_row(
        totalHealsOnTeammates=6000,
        totalDamageShieldedOnTeammates=400,
        timeCCingOthers=50,
    )
    df = pd.DataFrame([row])
    _, value = support_utility_impact(df, _GOLD)
    assert "shield/min" not in value
    assert "heal/min" in value


def test_support_utility_omits_incidental_heal_and_shield() -> None:
    """Thresh/Pyke-level heal/shield must not drag the Utility composite."""
    row = _parse_support_row(
        totalHealsOnTeammates=800,
        totalDamageShieldedOnTeammates=900,
        timeCCingOthers=50,
    )
    df = pd.DataFrame([row])
    _, value = support_utility_impact(df, _GOLD)
    assert "heal/min" not in value
    assert "shield/min" not in value
    assert "CC/min" in value


def test_utility_summary_hides_low_heal_shield_cards() -> None:
    row = _parse_support_row(
        totalHealsOnTeammates=500,
        totalDamageShieldedOnTeammates=400,
    )
    summary = utility_summary(pd.DataFrame([row]))
    assert summary["avg_hpm"] is None
    assert summary["avg_spm"] is None


def test_game_score_skips_noise_heal_shield_ingredients() -> None:
    row = _parse_support_row(
        totalHealsOnTeammates=600,
        totalDamageShieldedOnTeammates=500,
        timeCCingOthers=50,
    )
    baseline = {
        "hpm": float(row["hpm"]),
        "spm": float(row["spm"]),
        "ccpm": float(row["ccpm"]),
        "damage_share": float(row["damage_share"]),
        "damage_taken_share": float(row.get("damage_taken_share") or 0.2),
    }
    score = compute_game_score(row, baseline, role="UTILITY")
    utility = next(dim for dim in score.dimensions if dim.name == "Utility")
    columns = {item.column for item in utility.ingredients}
    assert "hpm" not in columns
    assert "spm" not in columns


def test_peer_comparisons_omit_noise_heal_shield() -> None:
    comparisons = build_comparisons(
        {"healing": 900.0, "shielding": 700.0, "ccpm": 2.0, "vspm": 1.5},
        {"healing": 7500.0, "shielding": 3500.0, "ccpm": 1.9, "vspm": 1.8},
        role="UTILITY",
    )
    keys = {comp.metric for comp in comparisons}
    assert "healing" not in keys
    assert "shielding" not in keys
    assert "ccpm" in keys


def test_support_utility_includes_damage_taken_share() -> None:
    match = make_match()
    me = match["info"]["participants"][0]
    me["teamPosition"] = "UTILITY"
    me["championName"] = "Leona"
    me["totalHealsOnTeammates"] = 0
    me["totalDamageShieldedOnTeammates"] = 0
    me["totalDamageTaken"] = 28000
    me["timeCCingOthers"] = 50
    for ally in match["info"]["participants"][1:5]:
        ally["totalDamageTaken"] = 8000
    record = MatchParser(ItemCatalog(FAKE_ITEMS)).parse(match, make_timeline(), MY_PUUID)
    df = pd.DataFrame([record.to_row()])
    _, value = support_utility_impact(df, _GOLD)
    assert "dmg taken" in value
    assert float(df["damage_taken_share"].iloc[0]) > 0.30
