"""CareerStore persistence round-trips."""

from __future__ import annotations

from pathlib import Path

from league_stats.analysis.career.models import Rung
from league_stats.infra.career_store import CareerStore, build_key


def _rungs(prefix: str) -> list[Rung]:
    return [
        Rung(text=f"{prefix} {i}", column="cspm", comparator="at_least", target=6.0 + i, need=15)
        for i in range(3)
    ]


def _store(tmp_path: Path) -> CareerStore:
    return CareerStore(tmp_path / "nested" / "career.sqlite")


def test_build_key_normalises_role() -> None:
    assert build_key("hugros_euw", "Viktor", "middle") == "hugros_euw|Viktor|MIDDLE"


def test_write_and_load_slot_round_trip(tmp_path: Path) -> None:
    key = build_key("p", "Viktor", "MIDDLE")
    with _store(tmp_path) as store:
        store.write_slot(key, 0, "laning_income", _rungs("a"), ["In progress"] * 3)
        goals = store.load_goals(key)

    assert [g.goal_index for g in goals] == [0, 1, 2]
    assert {g.track_key for g in goals} == {"laning_income"}
    assert goals[1].rung.text == "a 1"
    assert goals[1].rung.target == 7.0
    assert goals[1].rung.comparator == "at_least"
    assert goals[1].rung.need == 15


def test_goals_are_ordered_by_slot_then_index(tmp_path: Path) -> None:
    key = build_key("p", "Viktor", "MIDDLE")
    with _store(tmp_path) as store:
        store.write_slot(key, 2, "map_presence", _rungs("c"), ["In progress"] * 3)
        store.write_slot(key, 0, "laning_income", _rungs("a"), ["In progress"] * 3)
        goals = store.load_goals(key)

    assert [(g.slot, g.goal_index) for g in goals] == [
        (0, 0),
        (0, 1),
        (0, 2),
        (2, 0),
        (2, 1),
        (2, 2),
    ]


def test_save_goal_states(tmp_path: Path) -> None:
    key = build_key("p", "Viktor", "MIDDLE")
    with _store(tmp_path) as store:
        store.write_slot(key, 0, "laning_income", _rungs("a"), ["In progress"] * 3)
        store.save_goal_states(key, {(0, 0): "Cleared", (0, 1): "At risk"})
        states = [g.state for g in store.load_goals(key)]

    assert states == ["Cleared", "At risk", "In progress"]


def test_move_slot_overwrites_the_destination(tmp_path: Path) -> None:
    key = build_key("p", "Viktor", "MIDDLE")
    with _store(tmp_path) as store:
        store.write_slot(key, 0, "laning_income", _rungs("a"), ["Cleared"] * 3)
        store.write_slot(key, 1, "map_presence", _rungs("b"), ["In progress"] * 3)
        store.move_slot(key, 1, 0)
        goals = store.load_goals(key)

    assert {g.slot for g in goals} == {0}
    assert {g.track_key for g in goals} == {"map_presence"}


def test_used_tracks_and_pending_congrats(tmp_path: Path) -> None:
    key = build_key("p", "Viktor", "MIDDLE")
    with _store(tmp_path) as store:
        assert store.used_track_keys(key) == set()
        store.record_used_track(key, "laning_income")
        assert store.used_track_keys(key) == {"laning_income"}

        assert store.take_pending_congrats(key) == ""
        store.set_pending_congrats(key, "laning_income")
        assert store.take_pending_congrats(key) == "laning_income"
        assert store.take_pending_congrats(key) == ""


def test_ladders_are_isolated_per_build(tmp_path: Path) -> None:
    mid = build_key("p", "Viktor", "MIDDLE")
    top = build_key("p", "Aatrox", "TOP")
    with _store(tmp_path) as store:
        store.write_slot(mid, 0, "laning_income", _rungs("a"), ["In progress"] * 3)
        assert store.load_goals(top) == []
