"""CareerStore persistence round-trips."""

from __future__ import annotations

import mongomock

from league_stats_runner.analysis.career.models import Rung
from league_stats_common.infra.career_store import CareerStore, build_key


def _rungs(prefix: str) -> list[Rung]:
    return [
        Rung(text=f"{prefix} {i}", column="cspm", comparator="at_least", target=6.0 + i, need=15)
        for i in range(3)
    ]


def _store() -> CareerStore:
    return CareerStore(mongomock.MongoClient(), db_name="career")


def test_build_key_normalises_role() -> None:
    assert build_key("hugros_euw", "Viktor", "middle") == "hugros_euw|Viktor|MIDDLE"


def test_goal_id_is_a_real_objectid() -> None:
    from bson import ObjectId

    key = build_key("p", "Viktor", "MIDDLE")
    with _store() as store:
        store.write_slot(key, 0, "map_presence", _rungs("a"), ["In progress"] * 3)
        doc = store._goals.find_one({"build_key": key, "slot": 0, "goal_index": 0})
    assert isinstance(doc["_id"], ObjectId)


def test_used_track_id_is_a_real_objectid() -> None:
    from bson import ObjectId

    key = build_key("p", "Viktor", "MIDDLE")
    with _store() as store:
        store.record_used_track(key, "map_presence")
        doc = store._used_tracks.find_one({"build_key": key, "track_key": "map_presence"})
    assert isinstance(doc["_id"], ObjectId)


def test_career_flag_id_is_a_real_objectid() -> None:
    from bson import ObjectId

    key = build_key("p", "Viktor", "MIDDLE")
    with _store() as store:
        store.set_pending_congrats(key, "map_presence")
        doc = store._flags.find_one({"build_key": key})
    assert isinstance(doc["_id"], ObjectId)


def test_write_and_load_slot_round_trip() -> None:
    key = build_key("p", "Viktor", "MIDDLE")
    with _store() as store:
        store.write_slot(key, 0, "laning_income", _rungs("a"), ["In progress"] * 3)
        goals = store.load_goals(key)

    assert [g.goal_index for g in goals] == [0, 1, 2]
    assert {g.track_key for g in goals} == {"laning_income"}
    assert goals[1].rung.text == "a 1"
    assert goals[1].rung.target == 7.0
    assert goals[1].rung.comparator == "at_least"
    assert goals[1].rung.need == 15


def test_at_most_comparator_survives_a_round_trip() -> None:
    key = build_key("p", "Viktor", "MIDDLE")
    rungs = [
        Rung(
            text="1 or fewer", column="solo_deaths", comparator="at_most",
            target=1.0, need=15,
        )
    ]
    with _store() as store:
        store.write_slot(key, 0, "survival", rungs, ["In progress"])
        goals = store.load_goals(key)

    assert goals[0].rung.comparator == "at_most"
    assert goals[0].rung.target == 1.0


def test_goals_are_ordered_by_slot_then_index() -> None:
    key = build_key("p", "Viktor", "MIDDLE")
    with _store() as store:
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


def test_save_goal_states() -> None:
    key = build_key("p", "Viktor", "MIDDLE")
    with _store() as store:
        store.write_slot(key, 0, "laning_income", _rungs("a"), ["In progress"] * 3)
        store.save_goal_states(key, {(0, 0): "Cleared", (0, 1): "At risk"})
        states = [g.state for g in store.load_goals(key)]

    assert states == ["Cleared", "At risk", "In progress"]


def test_move_slot_overwrites_the_destination() -> None:
    key = build_key("p", "Viktor", "MIDDLE")
    with _store() as store:
        store.write_slot(key, 0, "laning_income", _rungs("a"), ["Cleared"] * 3)
        store.write_slot(key, 1, "map_presence", _rungs("b"), ["In progress"] * 3)
        store.move_slot(key, 1, 0)
        goals = store.load_goals(key)

    assert {g.slot for g in goals} == {0}
    assert {g.track_key for g in goals} == {"map_presence"}


def test_used_tracks_and_pending_congrats() -> None:
    key = build_key("p", "Viktor", "MIDDLE")
    with _store() as store:
        assert store.used_track_keys(key) == set()
        store.record_used_track(key, "laning_income")
        assert store.used_track_keys(key) == {"laning_income"}

        assert store.peek_pending_congrats(key) == ""
        store.set_pending_congrats(key, "laning_income")

        # Peeking must not consume: a background watch rebuild would otherwise
        # swallow the milestone before anyone saw it.
        assert store.peek_pending_congrats(key) == "laning_income"
        assert store.peek_pending_congrats(key) == "laning_income"

        store.clear_pending_congrats(key)
        assert store.peek_pending_congrats(key) == ""


def test_record_used_track_is_idempotent_within_the_same_second() -> None:
    """SQL's `INSERT OR IGNORE` keyed on `(build_key, track_key, cleared_at)`:
    two clears of the same track landing in the same second (`_now()` has
    second precision) must not create two rows."""
    key = build_key("p", "Viktor", "MIDDLE")
    with _store() as store:
        store.record_used_track(key, "laning_income")
        store.record_used_track(key, "laning_income")

        assert store.row_counts()["career_used_tracks"] == 1
        assert store.used_track_keys(key) == {"laning_income"}


def test_ladders_are_isolated_per_build() -> None:
    mid = build_key("p", "Viktor", "MIDDLE")
    top = build_key("p", "Aatrox", "TOP")
    with _store() as store:
        store.write_slot(mid, 0, "laning_income", _rungs("a"), ["In progress"] * 3)
        assert store.load_goals(top) == []


def test_since_ms_round_trips_and_defaults_to_zero() -> None:
    key = build_key("p", "Viktor", "MIDDLE")
    with _store() as store:
        store.write_slot(key, 0, "laning_income", _rungs("a"), ["In progress"] * 3)
        assert {g.since_ms for g in store.load_goals(key)} == {0}

        store.write_slot(
            key, 1, "map_presence", _rungs("b"), ["In progress"] * 3, since_ms=1234
        )
        queued = [g for g in store.load_goals(key) if g.slot == 1]
        assert {g.since_ms for g in queued} == {1234}


def test_move_slot_can_restamp_the_start_line() -> None:
    key = build_key("p", "Viktor", "MIDDLE")
    with _store() as store:
        store.write_slot(
            key, 1, "map_presence", _rungs("b"), ["In progress"] * 3, since_ms=100
        )
        store.move_slot(key, 1, 0, since_ms=500)
        promoted = store.load_goals(key)

        assert {g.slot for g in promoted} == {0}
        assert {g.since_ms for g in promoted} == {500}


def test_a_goal_document_missing_since_ms_defaults_to_zero_on_load() -> None:
    """Mongo has no `ALTER TABLE`; a document written before `since_ms`
    existed (or by a client that omitted it) must default to 0 on read, the
    same as the old SQL schema migration's `DEFAULT 0` did for pre-existing
    rows.
    """
    key = build_key("p", "Viktor", "MIDDLE")
    with _store() as store:
        store._goals.insert_one(
            {
                "_id": "k\x1f0\x1f0",
                "build_key": key,
                "slot": 0,
                "goal_index": 0,
                "track_key": "laning_income",
                "text": "old",
                "column_name": "cspm",
                "comparator": "at_least",
                "target": 7.0,
                "need": 15,
                "state": "Cleared",
                # since_ms, peer_seeded, why deliberately absent.
            }
        )
        goals = store.load_goals(key)

    assert len(goals) == 1
    assert goals[0].since_ms == 0
    assert goals[0].peer_seeded is False
    assert goals[0].rung.why == ""
    assert goals[0].state == "Cleared"


def test_clear_all_removes_every_ladder() -> None:
    key = build_key("p", "Viktor", "MIDDLE")
    with _store() as store:
        store.write_slot(key, 0, "laning_income", _rungs("a"), ["In progress"] * 3)
        store.record_used_track(key, "laning_income")
        store.set_pending_congrats(key, "laning_income")

        counts = store.clear_all()

        assert counts["career_goals"] == 3
        assert counts["career_used_tracks"] == 1
        assert counts["career_flags"] == 1
        assert store.load_goals(key) == []
        assert store.used_track_keys(key) == set()
        assert store.peek_pending_congrats(key) == ""
