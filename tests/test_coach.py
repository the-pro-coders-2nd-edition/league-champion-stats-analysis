"""Tests for the coach engine's rule evaluation and ranking."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from league_stats_runner.analysis.coach.engine import (
    CoachEngine,
    VISIBLE_RECOMMENDATIONS,
    recommendations_markdown,
)
from league_stats_runner.analysis.statistics import StatisticsEngine
from tests.test_statistics import _synthetic_matches


@pytest.fixture()
def coach(tmp_path: Path) -> CoachEngine:
    """A coach over synthetic data with a strong early-deaths signal."""
    matches = _synthetic_matches()
    matches["opponent"] = (["Syndra"] * 20 + ["Orianna"] * 20 + ["Akali"] * 20)
    # Keep a clear win-rate split for non-matchup rules that use these rows.
    matches.loc[matches["opponent"] == "Akali", "win"] = 0
    matches.loc[matches["opponent"] == "Orianna", "win"] = 1
    matches["gd10"] = np.where(matches["win"] == 1, 650, -450)
    matches["kill_participation"] = np.where(matches["win"] == 1, 0.78, 0.32)
    matches["deaths_before_neutral_objective"] = np.where(matches["win"] == 0, 2, 0)
    matches["fights_disadvantaged"] = np.where(matches["win"] == 0, 3, 0)
    matches["avg_gold_at_death"] = np.where(matches["win"] == 0, 1600, 450)
    matches["avg_unspent_gold_per_fight"] = np.where(matches["win"] == 0, 1800, 650)
    matches["grouped_share"] = np.where(matches["win"] == 0, 0.72, 0.38)
    matches["solo_share"] = np.where(matches["win"] == 1, 0.55, 0.18)
    matches["dist_jungle"] = np.where(matches["win"] == 1, 2200, 6200)
    matches["outnumbered_deaths"] = np.where(matches["win"] == 0, 3, 0)
    matches["greed_deaths"] = np.where(matches["win"] == 0, 3, 0)
    matches["shutdown_given"] = np.where(matches["win"] == 0, 400, 50)
    matches["tf_participation"] = np.where(matches["win"] == 1, 0.8, 0.4)
    matches.loc[matches["gd15"] < 0, "gd15"] = 900
    matches.loc[matches["win"] == 0, "gd15"] = 900
    deaths = pd.DataFrame(
        {
            "match_id": ["M0"] * 6,
            "win": [0, 0, 0, 0, 1, 0],
            "minute": [23.0, 25.0, 12.0, 30.0, 18.0, 27.0],
            "side_lane_push": [True, True, False, True, False, True],
            "alone": [True, True, False, True, False, True],
            "team_wards_recent": [0, 1, 2, 0, 1, 0],
            "zone": ["bot", "top", "mid", "bot", "mid", "bot"],
            "shutdown_given": [300, 0, 0, 450, 0, 250],
        }
    )
    objectives = pd.DataFrame(
        {
            "match_id": ["M0"] * 14,
            "win": [0] * 8 + [1] * 6,
            "kind": ["dragon"] * 8 + ["baron"] * 6,
            "present": [False] * 10 + [True] * 4,
            "dead_before": [True] * 5 + [False] * 9,
        }
    )
    stats = StatisticsEngine(matches, tmp_path)
    return CoachEngine(
        matches_df=matches,
        deaths_df=deaths,
        objectives_df=objectives,
        stats_engine=stats,
        role="MIDDLE",
    )


def test_recommendations_generated_and_sorted(coach: CoachEngine) -> None:
    """Recommendations exist and are sorted by descending priority."""
    recommendations = coach.generate()
    assert recommendations
    priorities = [r.priority for r in recommendations]
    assert priorities == sorted(priorities, reverse=True)


def test_matchup_rules_disabled(coach: CoachEngine) -> None:
    """Matchup tips stay in the matchup table, not the coaching section."""
    titles = " | ".join(r.title for r in coach.generate())
    assert "Orianna" not in titles
    assert "Akali" not in titles
    assert "strongest matchup" not in titles.lower()
    assert "struggle most against" not in titles.lower()


def test_new_rules_fire(coach: CoachEngine) -> None:
    """New coaching rules surface on the synthetic dataset."""
    recommendations = coach.generate()
    titles = " | ".join(r.title for r in recommendations)
    assert "Gold leads at 10 line up with your wins" in titles
    assert "Greed deaths show up in your losses" in titles
    assert "Shutdown bounties show up in your losses" in titles
    assert "Early leads aren't converting cleanly" in titles
    assert "Teamfight participation has room to grow" in titles
    assert "Death timers before objectives hurt setups" in titles
    assert "Pre-objective deaths hurt your setups" in titles
    assert all(r.action for r in recommendations)


def test_merged_objective_death_rule_not_dragon_only(coach: CoachEngine) -> None:
    """Pre-objective deaths cover dragon, elder, and baron together."""
    rec = next(r for r in coach.generate() if "Pre-objective deaths" in r.title)
    assert "dragon, elder, or baron" in rec.detail


def test_tip_titles_avoid_absolute_claims(coach: CoachEngine) -> None:
    """Titles stay comparative — no 'biggest leak' style absolutes."""
    joined = " | ".join(r.title.lower() for r in coach.generate())
    for banned in ("biggest leak", "costing you games", "throwing", "hurting your win rate"):
        assert banned not in joined


def test_new_metric_rules_fire(coach: CoachEngine) -> None:
    """Coach rules for newer death, fight, and positioning metrics surface."""
    titles = " | ".join(r.title for r in coach.generate())
    assert "Deaths with banked gold show up in losses" in titles
    assert "Fights start with gold still unspent" in titles
    assert "Disadvantaged fights show up in your losses" in titles
    assert "Over-grouping lines up with losses" in titles
    assert "Solo farm time lines up with your wins" in titles
    assert "Closer play with your jungle lines up with wins" in titles
    assert "Outnumbered deaths show up in your losses" in titles


def test_markdown_rendering(coach: CoachEngine) -> None:
    """The Markdown export lists every recommendation with evidence."""
    recommendations = coach.generate()
    markdown = recommendations_markdown(recommendations)
    assert markdown.startswith("# Viktor Mid Coaching Recommendations")
    assert "Evidence:" in markdown


def test_visible_recommendation_limit_constant() -> None:
    """The report shows three recommendations before expanding."""
    assert VISIBLE_RECOMMENDATIONS == 3


def test_empty_data_yields_no_recommendations(tmp_path: Path) -> None:
    """A near-empty dataset produces no recommendations."""
    empty = pd.DataFrame({"match_id": ["M0"], "win": [1]})
    stats = StatisticsEngine(empty, tmp_path)
    coach = CoachEngine(empty, pd.DataFrame(), pd.DataFrame(), stats)
    assert coach.generate() == []


def _support_low_cc_matches(n: int = 20) -> pd.DataFrame:
    """Support games with CC well below the Gold role norm (~1.9)."""
    return pd.DataFrame(
        {
            "match_id": [f"M{i}" for i in range(n)],
            "win": [i % 2 for i in range(n)],
            "ccpm": [0.9] * n,
            "damage_share": [0.08] * n,
            "vspm": [1.9] * n,
            "kill_participation": [0.65] * n,
            "deaths_pre20": [1] * n,
            "control_wards": [2] * n,
        }
    )


def test_low_cc_fallback_uses_role_benchmark_not_peers(tmp_path: Path) -> None:
    """Without peers, the CC tip cites the Gold role average — not 'peers'."""
    matches = _support_low_cc_matches()
    stats = StatisticsEngine(matches, tmp_path, role="UTILITY")
    coach = CoachEngine(
        matches, pd.DataFrame(), pd.DataFrame(), stats, role="UTILITY", build_label="Thresh support"
    )
    rec = next(r for r in coach.generate() if "Crowd control" in r.title)
    assert "role norms" in rec.title
    assert "Gold support average" in rec.detail
    assert "for peers" not in rec.detail
    assert "1.90" in rec.detail


def test_low_cc_defers_to_peer_comparison(tmp_path: Path) -> None:
    """Once peer CC exists, coach skips the static-norm tip (peer tip owns it)."""
    from league_stats_common.core.models import MetricComparison, PeerComparisonResult

    matches = _support_low_cc_matches()
    stats = StatisticsEngine(matches, tmp_path, role="UTILITY")
    peer = PeerComparisonResult(
        rank_label="Gold II",
        tier="GOLD",
        build_label="Thresh support",
        source="test",
        peer_games=40,
        peer_players=12,
        comparisons=[
            MetricComparison(
                metric="ccpm",
                label="CC/min",
                yours=0.9,
                peer_avg=2.3,
                delta=-1.4,
                delta_pct=-60.9,
                direction="higher",
                verdict="below",
            )
        ],
    )
    coach = CoachEngine(
        matches,
        pd.DataFrame(),
        pd.DataFrame(),
        stats,
        role="UTILITY",
        build_label="Thresh support",
        peer_comparison=peer,
    )
    titles = [r.title for r in coach.generate()]
    assert not any("Crowd control trails role norms" in title for title in titles)


def test_unproductive_sidelane_fires_for_top_only(tmp_path: Path) -> None:
    """Win-leaky objective-trade splits are gone; TOP gets absence-conversion instead."""
    n = 20
    matches = pd.DataFrame(
        {
            "match_id": [f"M{i}" for i in range(n)],
            "win": [i % 2 for i in range(n)],
            "unproductive_absence_rate": [0.45] * n,
            "objective_trade_success_rate": [0.9 if i % 2 else 0.1 for i in range(n)],
        }
    )
    stats = StatisticsEngine(matches, tmp_path, role="TOP")
    top = CoachEngine(matches, pd.DataFrame(), pd.DataFrame(), stats, role="TOP")
    mid = CoachEngine(matches, pd.DataFrame(), pd.DataFrame(), stats, role="MIDDLE")
    top_titles = [r.title for r in top.generate()]
    mid_titles = [r.title for r in mid.generate()]
    assert "Sidelane absences aren't converting" in top_titles
    assert "Sidelane trades correlate with your wins" not in top_titles
    assert "Sidelane absences aren't converting" not in mid_titles
    assert "Sidelane trades correlate with your wins" not in mid_titles
