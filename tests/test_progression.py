"""Tests for Form Tracker progression analysis."""

from __future__ import annotations

import pandas as pd

import pytest

from league_stats_peers.analysis.peer.comparison import _verdict as _peer_verdict
from league_stats_runner.analysis.progression.diff import _progression_verdict, build_progression_comparison
from league_stats_runner.analysis.progression.form_score import compute_form_score, trend_from_score
from league_stats_runner.analysis.progression.metrics import progression_metrics_for_role
from league_stats_runner.analysis.progression.slicing import (
    slice_baseline_exclusive,
    slice_baseline_inclusive,
    slice_recent,
)
from league_stats_runner.analysis.progression.stats import welch_test, winrate_significant
from league_stats_common.core.config import AppConfig
from league_stats_common.core.models import MatchRecord, MetricDelta
from tests.fixtures import FAKE_ITEMS, MY_PUUID, make_match, make_timeline
from league_stats_runner.ingest.parser import ItemCatalog, MatchParser


def _make_config(**overrides: object) -> AppConfig:
    base = {
        "riot_id": "Test",
        "tagline": "EUW",
        "api_key": "RGAPI-test-key-1234567890",
        "champion": "Viktor",
        "role": "MIDDLE",
    }
    base.update(overrides)
    return AppConfig(**base)


def _parse_records(count: int, *, win: bool = True) -> list[MatchRecord]:
    parser = MatchParser(ItemCatalog(FAKE_ITEMS))
    records: list[MatchRecord] = []
    for index in range(count):
        match = make_match()
        match["metadata"]["matchId"] = f"EUW1_{10000 + index}"
        match["info"]["gameCreation"] = 1_700_000_000_000 - index * 86_400_000
        me = match["info"]["participants"][0]
        me["win"] = win if index % 2 == 0 else not win
        me["deaths"] = 2 + (index % 3)
        timeline = make_timeline()
        records.append(parser.parse(match, timeline, MY_PUUID))
    return records


def test_slice_baseline_exclusive_no_overlap() -> None:
    records = _parse_records(100)
    recent = slice_recent(records, 20)
    baseline = slice_baseline_exclusive(records, 20, 80)
    assert len(recent) == 20
    assert len(baseline) == 80
    recent_ids = {record.match_id for record in recent}
    baseline_ids = {record.match_id for record in baseline}
    assert recent_ids.isdisjoint(baseline_ids)


def test_slice_baseline_inclusive_overlaps() -> None:
    records = _parse_records(50)
    recent = slice_recent(records, 10)
    baseline = slice_baseline_inclusive(records, 30)
    assert len(recent) == 10
    assert len(baseline) == 30
    assert recent[0].match_id == baseline[0].match_id


def test_winrate_significance_known_proportions() -> None:
    significant, p_value, effect = winrate_significant(15, 20, 5, 20)
    assert p_value is not None
    assert effect is not None
    assert effect > 0
    assert significant or p_value < 0.2


def test_welch_test_small_sample_returns_none() -> None:
    p_value, cohen_d = welch_test(pd.Series([1.0, 2.0]), pd.Series([3.0, 4.0]))
    assert p_value is None
    assert cohen_d is None


def test_form_score_deaths_down_is_positive() -> None:
    deltas = [
        MetricDelta(
            metric="deaths",
            label="Deaths/game",
            section="overview",
            recent=2.0,
            baseline=4.0,
            delta=-2.0,
            delta_pct=-50.0,
            direction="lower",
            verdict="improved",
            significant=True,
            recent_n=20,
            baseline_n=80,
        )
    ]
    score = compute_form_score(deltas, role="MIDDLE")
    assert score > 0
    assert trend_from_score(score) in {"improving", "stable"}


def test_insufficient_sample_returns_insufficient_confidence() -> None:
    config = _make_config()
    recent = _parse_records(3)
    baseline = _parse_records(10)
    comparison = build_progression_comparison(
        config,
        recent,
        baseline,
        preset_key="20_80",
    )
    assert comparison is not None
    assert comparison.snapshot.confidence == "insufficient"
    assert not comparison.deltas


def test_progression_metrics_role_aware() -> None:
    jungle = {spec.metric for spec in progression_metrics_for_role("JUNGLE")}
    support = {spec.metric for spec in progression_metrics_for_role("UTILITY")}
    assert "kill_participation" in jungle
    assert "solo_death_rate" in support or "greed_death_rate" in support


def test_progression_comparison_schema_roundtrip() -> None:
    config = _make_config()
    recent = _parse_records(20)
    baseline = _parse_records(80)
    comparison = build_progression_comparison(
        config,
        recent,
        baseline,
        preset_key="20_80",
    )
    assert comparison is not None
    payload = comparison.model_dump()
    from league_stats_common.core.models import ProgressionComparison

    restored = ProgressionComparison.model_validate(payload)
    assert restored.preset_key == "20_80"
    assert restored.snapshot.recent_games == 20


def test_form_stories_fold_death_rate_into_deaths() -> None:
    from league_stats_runner.analysis.progression.stories import build_form_stories
    from league_stats_common.core.models import Recommendation, RecommendationTone

    deaths = MetricDelta(
        metric="deaths",
        label="Deaths/game",
        section="overview",
        recent=6.0,
        baseline=4.0,
        delta=2.0,
        delta_pct=50.0,
        direction="lower",
        verdict="regressed",
        significant=True,
        effect_size=0.9,
        recent_n=20,
        baseline_n=80,
    )
    greed = MetricDelta(
        metric="greed_death_rate",
        label="Greed death rate",
        section="deaths",
        recent=0.35,
        baseline=0.18,
        delta=0.17,
        delta_pct=94.0,
        direction="lower",
        verdict="regressed",
        significant=True,
        effect_size=0.7,
        recent_n=20,
        baseline_n=80,
    )
    vision = MetricDelta(
        metric="vspm",
        label="Vision/min",
        section="vision",
        recent=1.4,
        baseline=1.0,
        delta=0.4,
        delta_pct=40.0,
        direction="higher",
        verdict="improved",
        significant=True,
        effect_size=0.6,
        recent_n=20,
        baseline_n=80,
    )
    recs = [
        Recommendation(
            category="Form",
            title="Deaths creeping up",
            detail="Tighten reset timing after plates and kills.",
            evidence="Deaths rose.",
            tone=RecommendationTone.NEGATIVE,
            priority=3.0,
        ),
        Recommendation(
            category="Form",
            title="Vision trending up",
            detail="Keep buying control wards.",
            evidence="VS/min rose.",
            tone=RecommendationTone.POSITIVE,
            priority=2.0,
        ),
    ]
    stories = build_form_stories(
        [deaths, greed, vision],
        behavioral_shifts=["Greed deaths rose from 18% to 35% (+17 pp)"],
        recommendations=recs,
        limit=3,
    )
    assert len(stories) == 2
    assert stories[0].metric == "deaths"
    assert stories[0].tone == "fix"
    assert stories[0].habit is not None
    assert "greed" in stories[0].habit.lower()
    assert "reset" in stories[0].action.lower()
    assert stories[1].metric == "vspm"
    assert stories[1].tone == "keep"
    assert all(story.metric != "greed_death_rate" for story in stories)


def test_form_stories_action_does_not_restate_driver() -> None:
    from league_stats_runner.analysis.progression.stories import build_form_stories
    from league_stats_common.core.models import Recommendation, RecommendationTone

    delta = MetricDelta(
        metric="deaths",
        label="Deaths/game",
        section="overview",
        recent=6.5,
        baseline=4.2,
        delta=2.3,
        delta_pct=55.0,
        direction="lower",
        verdict="regressed",
        significant=True,
        effect_size=1.1,
        recent_n=20,
        baseline_n=80,
    )
    stories = build_form_stories(
        [delta],
        recommendations=[
            Recommendation(
                category="Form",
                title="Deaths creeping up",
                detail="Tighten reset timing after plates and kills.",
                evidence="Deaths/game rose from 4.2 to 6.5.",
                tone=RecommendationTone.NEGATIVE,
                priority=4.0,
            )
        ],
    )
    assert len(stories) == 1
    assert "4.2" in stories[0].driver
    assert "6.5" in stories[0].driver
    assert "4.2" not in stories[0].action
    assert "6.5" not in stories[0].action


@pytest.mark.parametrize("metric", ["gd10", "cs10"])
@pytest.mark.parametrize("delta", [2, -2, 5, -15, 25, -29.9])
def test_gd10_cs10_noise_gate_agrees_below_threshold(metric: str, delta: float) -> None:
    """Same metric, same delta magnitude, must be judged noise consistently.

    Regression test: progression/diff.py used to suppress gd10/cs10 noise below
    abs(delta) < 3 while peer/comparison.py used < 30 -- a 10x gap on the same
    metric pair. Baseline/peer of 0 forces the generic magnitude threshold to
    0.05, isolating the gd10/cs10-specific noise gate being compared here.
    """
    assert _progression_verdict(delta, "higher", metric, 0.0) == "inline"
    assert _peer_verdict(delta, "higher", metric, 0.0) == "inline"


@pytest.mark.parametrize("metric", ["gd10", "cs10"])
@pytest.mark.parametrize("delta", [30, -30, 35, -50])
def test_gd10_cs10_noise_gate_agrees_at_and_above_threshold(metric: str, delta: float) -> None:
    assert _progression_verdict(delta, "higher", metric, 0.0) != "inline"
    assert _peer_verdict(delta, "higher", metric, 0.0) != "inline"
