"""Tests for objective setup analysis."""

from __future__ import annotations

from league_stats.analysis.objectives import extract_objectives, objective_summary, objectives_dataframe
from league_stats.analysis.timeline import build_context
from league_stats.core.models import MatchRecord, ObjectiveKind, ObjectiveRecord
from tests.fixtures import MY_PUUID, make_match, make_timeline


def test_objectives_extracted() -> None:
    """One dragon and one baron are extracted with correct ownership."""
    ctx = build_context(make_match(), make_timeline(), MY_PUUID)
    objectives = extract_objectives(ctx)
    kinds = {o.kind for o in objectives}
    assert kinds == {ObjectiveKind.DRAGON, ObjectiveKind.BARON}
    dragon = next(o for o in objectives if o.kind == ObjectiveKind.DRAGON)
    baron = next(o for o in objectives if o.kind == ObjectiveKind.BARON)
    assert dragon.taken_by_team is False
    assert baron.taken_by_team is True


def test_dragon_vision_setup() -> None:
    """Only the player's wards in the 2-minute window count, not teammates'."""
    ctx = build_context(make_match(), make_timeline(), MY_PUUID)
    dragon = next(
        o for o in extract_objectives(ctx) if o.kind == ObjectiveKind.DRAGON
    )
    assert dragon.wards_before == 1
    assert dragon.control_wards_before == 0


def _timeline_with_grubs(*, blue: int = 2, red: int = 1) -> dict:
    timeline = make_timeline()
    events = timeline["info"]["frames"][-1]["events"]
    base_ts = 480_000
    for index in range(blue):
        events.append(
            {
                "timestamp": base_ts + index * 3_000,
                "type": "ELITE_MONSTER_KILL",
                "killerTeamId": 100,
                "monsterType": "HORDE",
                "position": {"x": 5007, "y": 10471},
            }
        )
    for index in range(red):
        events.append(
            {
                "timestamp": base_ts + (blue + index) * 3_000,
                "type": "ELITE_MONSTER_KILL",
                "killerTeamId": 200,
                "monsterType": "HORDE",
                "position": {"x": 5007, "y": 10471},
            }
        )
    timeline["info"]["frames"][-1]["events"] = sorted(events, key=lambda e: e["timestamp"])
    return timeline


def test_grubs_collapsed_to_one_objective() -> None:
    """All Void grub kills become a single camp objective with a secured split."""
    ctx = build_context(make_match(), _timeline_with_grubs(blue=2, red=1), MY_PUUID)
    objectives = extract_objectives(ctx)
    grubs = [o for o in objectives if o.kind == ObjectiveKind.GRUBS]
    assert len(grubs) == 1
    camp = grubs[0]
    assert camp.secured_count == 2
    assert camp.objective_total == 3
    assert camp.taken_by_team is True
    assert camp.minute == 8.0


def test_grubs_taken_rate_uses_secured_share() -> None:
    """Summary taken_rate for grubs averages secured/total, not a boolean majority."""
    record = MatchRecord.model_construct(
        match_id="EUW1_grubs",
        win=True,
        objectives=[
            ObjectiveRecord(
                minute=8.0,
                kind=ObjectiveKind.GRUBS,
                taken_by_team=True,
                secured_count=2,
                objective_total=3,
            )
        ],
    )
    summary = objective_summary(objectives_dataframe([record]))
    assert summary["by_kind"]["grubs"]["taken_rate"] == 0.667
    assert summary["by_kind"]["grubs"]["count"] == 1
