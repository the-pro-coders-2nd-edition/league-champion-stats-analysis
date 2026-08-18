"""Tests for objective / structure trade summaries."""

from __future__ import annotations

from league_stats.analysis.trades import evaluate_objective_trade
from league_stats.core.models import BuildingRecord, ObjectiveKind, ObjectiveRecord, Position


def _building(
    *,
    minute: float,
    structure_id: str,
    lane: str,
    tier: str,
    taken_by_team: bool,
    destroyed_team_id: int,
) -> BuildingRecord:
    tier_map = {"t1": "outer", "t2": "inner"}
    return BuildingRecord(
        minute=minute,
        timestamp_ms=int(minute * 60_000),
        building_type="tower",
        lane=lane,  # type: ignore[arg-type]
        tier=tier_map.get(tier, tier),  # type: ignore[arg-type]
        taken_by_team=taken_by_team,
        destroyed_team_id=destroyed_team_id,
        position=Position(x=1000, y=1000),
        structure_id=structure_id,
    )


def _objective(*, minute: float, kind: ObjectiveKind, taken: bool) -> ObjectiveRecord:
    return ObjectiveRecord(
        kind=kind,
        minute=minute,
        taken_by_team=taken,
        present=False,
        arrived_early=False,
        arrived_late=False,
        dead_before=False,
        wards_before=0,
    )


class _Ctx:
    blue_side = True


def test_format_trade_asset_labels() -> None:
    from league_stats.analysis.trades import format_trade_asset_labels

    assert format_trade_asset_labels(["mid_t2", "baron"], gained=True) == [
        "their Mid T2",
        "Baron",
    ]
    assert format_trade_asset_labels(["bot_t1", "baron"], gained=False) == [
        "our Bot T1",
        "Baron",
    ]


def test_trade_summary_marks_enemy_structures_when_objective_lost() -> None:
    """Split trade: enemy towers taken while Baron is lost."""
    objective = _objective(minute=20.0, kind=ObjectiveKind.BARON, taken=False)
    buildings = [
        _building(
            minute=20.0,
            structure_id="mid_t2",
            lane="mid",
            tier="t2",
            taken_by_team=True,
            destroyed_team_id=200,
        ),
        _building(
            minute=20.1,
            structure_id="top_inhib",
            lane="top",
            tier="inhib",
            taken_by_team=True,
            destroyed_team_id=200,
        ),
    ]
    result = evaluate_objective_trade(
        _Ctx(),  # type: ignore[arg-type]
        objective,
        buildings,
        macro_role="split_pushing",
        defending_lane=None,
    )
    assert result["trade_outcome"] == "traded_for"
    assert result["trade_summary"] == "Gained their Mid T2 + their Top Inhib — Lost Baron"


def test_trade_summary_marks_our_structures_when_objective_secured_with_losses() -> None:
    objective = _objective(minute=18.0, kind=ObjectiveKind.DRAGON, taken=True)
    buildings = [
        _building(
            minute=18.0,
            structure_id="bot_t1",
            lane="bot",
            tier="t1",
            taken_by_team=False,
            destroyed_team_id=100,
        ),
    ]
    result = evaluate_objective_trade(
        _Ctx(),  # type: ignore[arg-type]
        objective,
        buildings,
        macro_role="present",
        defending_lane=None,
    )
    assert result["trade_outcome"] == "traded_away"
    assert result["trade_summary"] == "Secured Dragon — lost our Bot T1"


def test_secured_objective_with_enemy_towers_is_not_a_trade() -> None:
    objective = _objective(minute=18.0, kind=ObjectiveKind.DRAGON, taken=True)
    buildings = [
        _building(
            minute=18.0,
            structure_id="mid_t2",
            lane="mid",
            tier="t2",
            taken_by_team=True,
            destroyed_team_id=200,
        ),
    ]
    result = evaluate_objective_trade(
        _Ctx(),  # type: ignore[arg-type]
        objective,
        buildings,
        macro_role="present",
        defending_lane=None,
    )
    assert result["trade_outcome"] == "won"
    assert result["trade_summary"] == "Secured Dragon — gained their Mid T2"


def test_trade_summary_covers_lost_objective_and_our_structures() -> None:
    objective = _objective(minute=22.0, kind=ObjectiveKind.BARON, taken=False)
    buildings = [
        _building(
            minute=22.0,
            structure_id="mid_t2",
            lane="mid",
            tier="t2",
            taken_by_team=False,
            destroyed_team_id=100,
        ),
    ]
    result = evaluate_objective_trade(
        _Ctx(),  # type: ignore[arg-type]
        objective,
        buildings,
        macro_role="absent",
        defending_lane=None,
    )
    assert result["trade_outcome"] == "lost"
    assert result["trade_summary"] == "Lost Baron — lost our Mid T2"
