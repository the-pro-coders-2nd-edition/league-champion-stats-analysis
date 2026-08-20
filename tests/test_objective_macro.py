"""Tests for split-push aware objective macro detection."""

from __future__ import annotations

from league_stats_runner.analysis.buildings import (
    classify_structure_position,
    extract_buildings,
    lane_tower_fallen_before,
    split_push_allowed,
)
from league_stats_runner.analysis.objective_macro import enrich_objectives, objective_aggregate_rates
from league_stats_runner.analysis.objectives import extract_objectives, extract_objectives_enriched
from league_stats_runner.analysis.timeline import build_context
from league_stats_common.core.models import BuildingRecord, ObjectiveKind, ObjectiveRecord, Position
from tests.fixtures import MY_PUUID, make_match, make_timeline


def _ev(ts: int, **kwargs: object) -> dict:
    return {"timestamp": ts, **kwargs}


def _timeline_with_split_trade() -> dict:
    timeline = make_timeline()
    events = list(timeline["info"]["frames"][-1]["events"])
    # Bot tower for red (enemy) destroyed while player is absent at dragon.
    events.append(
        _ev(
            795_000,
            type="BUILDING_KILL",
            teamId=200,
            buildingType="TOWER_BUILDING",
            killerId=1,
            position={"x": 8955, "y": 8510},
        )
    )
    timeline["info"]["frames"][-1]["events"] = sorted(events, key=lambda e: e["timestamp"])
    return timeline


def test_classify_bot_tower_position() -> None:
    btype, lane, tier = classify_structure_position(
        Position(x=8955, y=8510), building_type="TOWER_BUILDING"
    )
    assert btype == "tower"
    assert lane in {"mid", "bot", "top"}
    assert tier in {"outer", "inner", "inhib"}


def test_enriched_objectives_have_macro_fields() -> None:
    ctx = build_context(make_match(), make_timeline(), MY_PUUID)
    objectives, buildings = extract_objectives_enriched(ctx, summoners=["Flash", "Teleport"])
    assert buildings
    assert objectives
    assert all(hasattr(obj, "macro_role") for obj in objectives)
    assert all(hasattr(obj, "justified_absence") for obj in objectives)


def test_split_trade_labels_missed_dragon_with_tower() -> None:
    ctx = build_context(make_match(), _timeline_with_split_trade(), MY_PUUID)
    objectives = enrich_objectives(
        ctx,
        extract_objectives(ctx),
        extract_buildings(ctx),
        summoners=["Flash", "Teleport"],
    )
    dragon = next(obj for obj in objectives if obj.kind == ObjectiveKind.DRAGON)
    assert dragon.taken_by_team is False
    if dragon.macro_role == "split_pushing":
        assert dragon.trade_outcome in {"traded_for", "lost", "even", "none"}


def test_objective_aggregate_rates_account_split_defend() -> None:
    objectives = [
        ObjectiveRecord(
            minute=10.0,
            kind=ObjectiveKind.DRAGON,
            taken_by_team=True,
            present=True,
            justified_absence=True,
            macro_role="present",
        ),
        ObjectiveRecord(
            minute=20.0,
            kind=ObjectiveKind.BARON,
            taken_by_team=False,
            present=False,
            justified_absence=True,
            macro_role="split_pushing",
            sidelane_pressure=True,
            trade_outcome="traded_for",
            trade_value_delta=2.0,
        ),
        ObjectiveRecord(
            minute=25.0,
            kind=ObjectiveKind.DRAGON,
            taken_by_team=False,
            present=False,
            justified_absence=False,
            macro_role="absent",
        ),
    ]
    rates = objective_aggregate_rates(objectives)
    assert rates["objectives_accounted_for_rate"] == 0.667
    assert rates["unproductive_absence_rate"] == 0.333
    assert rates["objectives_split_push_rate"] == 0.333
    assert rates["objective_trade_success_rate"] == 1.0


def test_objective_trade_success_ignores_epic_wins_without_structures() -> None:
    """Securing dragon with no tower swing is not a sidelane trade."""
    objectives = [
        ObjectiveRecord(
            minute=10.0,
            kind=ObjectiveKind.DRAGON,
            taken_by_team=True,
            present=True,
            macro_role="present",
            trade_outcome="won",
            trade_gain=["dragon"],
            trade_value_delta=4.0,
        ),
        ObjectiveRecord(
            minute=20.0,
            kind=ObjectiveKind.BARON,
            taken_by_team=False,
            present=False,
            macro_role="split_pushing",
            sidelane_pressure=True,
            trade_outcome="traded_for",
            trade_gain=["bot_t1"],
            trade_loss=["baron"],
            trade_value_delta=2.0,
        ),
    ]
    rates = objective_aggregate_rates(objectives)
    assert rates["objective_trade_success_rate"] == 1.0


def test_split_push_blocked_before_laning_without_tower_fall() -> None:
    pos = Position(x=8955, y=8510)
    assert split_push_allowed(10.0, pos, [], 600_000) is False


def test_split_push_allowed_after_laning_phase() -> None:
    pos = Position(x=8955, y=8510)
    assert split_push_allowed(15.0, pos, [], 900_000) is True


def test_split_push_allowed_early_when_lane_tower_fallen() -> None:
    # Red bot outer turret platform.
    pos = Position(x=10481, y=13650)
    buildings = [
        BuildingRecord(
            minute=8.0,
            timestamp_ms=480_000,
            building_type="tower",
            lane="bot",
            tier="outer",
            taken_by_team=True,
            destroyed_team_id=200,
            position=pos,
            structure_id="bot_t1",
        )
    ]
    assert lane_tower_fallen_before(buildings, "bot", 780_000) is True
    assert split_push_allowed(13.0, pos, buildings, 780_000) is True


def test_unproductive_absence_not_penalized_when_accounted() -> None:
    from league_stats_runner.analysis.game_review.score import compute_game_score

    baseline = {
        "objectives_present_rate": 0.6,
        "objectives_accounted_for_rate": 0.6,
        "unproductive_absence_rate": 0.15,
        "deaths_before_neutral_objective": 0.5,
    }
    split_game = compute_game_score(
        {
            "objectives_present_rate": 0.33,
            "objectives_accounted_for_rate": 0.67,
            "unproductive_absence_rate": 0.0,
            "deaths_before_neutral_objective": 0,
            "duration_min": 30,
        },
        baseline,
        role="TOP",
    )
    idle_game = compute_game_score(
        {
            "objectives_present_rate": 0.33,
            "objectives_accounted_for_rate": 0.33,
            "unproductive_absence_rate": 0.67,
            "deaths_before_neutral_objective": 0,
            "duration_min": 30,
        },
        baseline,
        role="TOP",
    )
    split_obj = next(d for d in split_game.dimensions if d.name == "Objectives").score
    idle_obj = next(d for d in idle_game.dimensions if d.name == "Objectives").score
    assert split_obj > idle_obj


def test_split_push_summary_aggregates_matches_df() -> None:
    import pandas as pd

    from league_stats_runner.analysis.objective_macro import split_push_summary

    frame = pd.DataFrame(
        [
            {
                "objectives_present_rate": 0.5,
                "objectives_split_push_rate": 0.2,
                "objectives_defend_split_rate": 0.1,
                "unproductive_absence_rate": 0.2,
                "structure_tower_damage": 4000,
                "towers_taken": 1,
            },
            {
                "objectives_present_rate": 0.7,
                "objectives_split_push_rate": 0.0,
                "objectives_defend_split_rate": 0.3,
                "unproductive_absence_rate": 0.0,
                "structure_tower_damage": 6000,
                "towers_taken": 2,
            },
        ]
    )
    summary = split_push_summary(frame)
    assert summary["avg_objectives_split_push_rate"] == 0.1
    assert summary["avg_split_push_balance"] == -0.1
    assert summary["avg_structure_tower_damage"] == 5000.0
    assert summary["avg_towers_taken"] == 1.5


def test_top_role_profile_includes_split_push_metrics() -> None:
    from league_stats_common.core.role_metrics import role_profile

    profile = role_profile("TOP")
    assert profile.objectives
    assert any(spec.label == "Split pushing" for spec in profile.objectives)
    peer_keys = {key for key, _, _ in profile.peer_metrics}
    assert "objectives_split_push_rate" in peer_keys
    assert "structure_tower_damage" in peer_keys
    objective_score = next(spec for spec in profile.score_components if spec.name == "Objectives")
    score_columns = {metric.column for metric in objective_score.metrics}
    assert "objectives_split_push_rate" in score_columns
