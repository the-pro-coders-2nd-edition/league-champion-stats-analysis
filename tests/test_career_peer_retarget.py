"""Blocks frozen before peer percentiles landed, and dropping a block by hand.

Stage A of the web pipeline renders every report with ``peer_comparison=None``
(``worker.py:262``), so a ladder seeded then steps toward the player's own p75
and its track order never saw the peer significance signals. Stage B re-renders
with peers, but ``_fill_empty_slots`` returns early once every slot is taken.
These tests pin the retarget that closes that window, and the manual drop.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pytest

from league_stats.analysis.career.engine import advance_career, drop_block
from league_stats.analysis.career.models import BLOCK_SLOTS
from league_stats.analysis.career.tracks import TrackContext
from league_stats.infra.career_store import CareerStore, build_key

KEY = build_key("p", "Thresh", "UTILITY")
HOUR = 3_600_000

# Peers are well ahead on cspm and vspm; the player is flat, so their own p75
# equals their p50 and every peer-driven track loses its ceiling without peers.
# Deliberately just under the +20% stretch cap on this player's medians: peer p75
# only moves a target when it sits *below* p50 x (1 + MAX_STEP_STRETCH), otherwise
# the cap decides and a peer-seeded rung is numerically identical to a blind one.
PEERS = {"cspm": 6.3, "vspm": 0.76, "damage_share": 0.26}


@dataclass(frozen=True)
class _Component:
    name: str
    score: float


# Laning is the weakest category, Objectives one of the strongest.
WEAK_LANING = [
    _Component("Laning", 30.0),
    _Component("Vision", 35.0),
    _Component("Objectives", 70.0),
    _Component("Fight", 75.0),
    _Component("Economy", 75.0),
    _Component("Survival", 80.0),
]


def _matches(games: int = 20, *, start: int = 0) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "game_creation_ms": [(start + i) * HOUR for i in range(games)],
            "cspm": [6.0] * games,
            "vspm": [0.72] * games,
            "damage_share": [0.25] * games,
            "deaths_pre20": [1.0] * games,
            "deaths_before_neutral_objective": [0.0] * games,
            "tf_participation": [0.6] * games,
            "control_wards": [1.0] * games,
            "first_item_min": [12.0] * games,
            "deaths_pre14": [0.5] * games,
            "greed_deaths": [0.0] * games,
            "solo_deaths": [0.0] * games,
            "outnumbered_deaths": [0.0] * games,
            "shutdown_given": [100.0] * games,
            "time_dead_s": [50.0] * games,
            "gd10": [200.0] * games,
            "gd15": [300.0] * games,
            "gd20": [400.0] * games,
            "xpd10": [150.0] * games,
            "cs10": [65.0] * games,
            "objectives_present_rate": [0.5] * games,
            "pct_advantaged_fights": [0.5] * games,
            "kp15": [0.55] * games,
            "tf_won_share": [0.5] * games,
            "ccpm": [7.0] * games,
            "vspm10": [0.7] * games,
            "wards_killed": [2.0] * games,
            "wards_placed": [10.0] * games,
            "avg_wards_before_objective": [1.2] * games,
            "objective_trade_success_rate": [0.5] * games,
            "unproductive_absence_rate": [0.15] * games,
            "towers_taken": [1.5] * games,
            "first_recall_min": [5.0] * games,
            "avg_unspent_gold": [900.0] * games,
            "avg_gold_at_death": [800.0] * games,
            "under_enemy_tower_laning_deaths": [0.0] * games,
            "gank_deaths_laning": [0.0] * games,
        }
    )


def _ctx(matches: pd.DataFrame, peer_p75: dict[str, float]) -> TrackContext:
    return TrackContext(
        matches_df=matches,
        objectives_df=pd.DataFrame({"present": [1, 1, 0, 0, 0, 0]}),
        role="UTILITY",
        peer_p75=peer_p75,
    )


@pytest.fixture()
def store(tmp_path: Path):
    with CareerStore(tmp_path / "career.sqlite") as opened:
        yield opened


def _ladder(store: CareerStore) -> list[tuple[int, str, float]]:
    return [
        (goal.slot, goal.track_key, goal.rung.target)
        for goal in sorted(store.load_goals(KEY), key=lambda g: (g.slot, g.goal_index))
    ]


def test_stage_a_ladder_is_retargeted_once_peers_land(store: CareerStore) -> None:
    matches = _matches()
    advance_career(store, KEY, _ctx(matches, {}), WEAK_LANING)
    seeded_blind = _ladder(store)

    advance_career(store, KEY, _ctx(matches, PEERS), WEAK_LANING)

    assert _ladder(store) != seeded_blind
    live = [row for row in _ladder(store) if row[0] == 0]
    assert {row[1] for row in live} == {"laning"}
    # cspm p50 is 6.0 and the peer p75 of 6.3 sits under the +20% stretch cap of
    # 7.2, so the peer number is what a retargeted rung actually lands on.
    assert 6.3 in [row[2] for row in live]


def test_retargeted_ladder_matches_one_seeded_with_peers_from_the_start(
    store: CareerStore, tmp_path: Path
) -> None:
    matches = _matches()
    advance_career(store, KEY, _ctx(matches, {}), WEAK_LANING)
    advance_career(store, KEY, _ctx(matches, PEERS), WEAK_LANING)

    with CareerStore(tmp_path / "fresh.sqlite") as fresh:
        advance_career(fresh, KEY, _ctx(matches, PEERS), WEAK_LANING)
        assert _ladder(store) == _ladder(fresh)


def test_retarget_leaves_a_block_the_player_has_already_started(store: CareerStore) -> None:
    """Progress is the line: a started block keeps its frozen targets."""
    advance_career(store, KEY, _ctx(_matches(), {}), WEAK_LANING)
    started = _ladder(store)
    first = [goal for goal in store.load_goals(KEY) if goal.slot == 0][0].rung

    # Twenty more games that bank progress on whichever goal went live.
    later = _matches(games=40)
    hit = first.target * 2 if first.comparator == "at_least" else 0.0
    later.loc[20:, first.column] = hit
    advance_career(store, KEY, _ctx(later, PEERS), WEAK_LANING)

    live_before = [row for row in started if row[0] == 0]
    live_after = [row for row in _ladder(store) if row[0] == 0]
    assert live_after == live_before


def test_peer_seeded_ladder_is_not_retargeted_twice(store: CareerStore) -> None:
    matches = _matches()
    advance_career(store, KEY, _ctx(matches, PEERS), WEAK_LANING)
    first = _ladder(store)

    advance_career(store, KEY, _ctx(matches, PEERS), WEAK_LANING)

    assert _ladder(store) == first


def _retire_the_live_block(store: CareerStore) -> pd.DataFrame:
    """Seed with peers, then clear the live block on a peer-blind refresh.

    A block's goals are three different metrics chosen by its category bank, so
    the follow-up games have to satisfy every one of them rather than nudging a
    named column and hoping the block picked it.
    """
    advance_career(store, KEY, _ctx(_matches(), PEERS), WEAK_LANING)
    live = [goal for goal in store.load_goals(KEY) if goal.slot == 0]
    cleared = _matches(games=40)
    for goal in live:
        rung = goal.rung
        cleared.loc[20:, rung.column] = (
            rung.target * 2 + 1 if rung.comparator == "at_least" else 0.0
        )
    advance_career(store, KEY, _ctx(cleared, {}), WEAK_LANING)
    return cleared


def test_replacement_block_seeded_after_a_blind_retire_is_flagged_provisional(
    store: CareerStore,
) -> None:
    _retire_the_live_block(store)

    replacement = max(goal.slot for goal in store.load_goals(KEY))
    assert not any(
        goal.peer_seeded for goal in store.load_goals(KEY) if goal.slot == replacement
    )


def test_replacement_block_is_retargeted_on_the_next_run_with_peers(
    store: CareerStore,
) -> None:
    cleared = _retire_the_live_block(store)

    advance_career(store, KEY, _ctx(cleared, PEERS), WEAK_LANING)

    assert all(goal.peer_seeded for goal in store.load_goals(KEY))


def test_drop_block_shifts_the_queue_left_and_generates_a_replacement(
    store: CareerStore,
) -> None:
    matches = _matches()
    advance_career(store, KEY, _ctx(matches, PEERS), WEAK_LANING)
    before = _ladder(store)
    queued_track = next(row[1] for row in before if row[0] == 1)

    snapshot = drop_block(store, KEY, 0, _ctx(matches, PEERS), WEAK_LANING)

    after = _ladder(store)
    assert next(row[1] for row in after if row[0] == 0) == queued_track
    assert len({row[0] for row in after}) == BLOCK_SLOTS
    assert len(snapshot.blocks) == BLOCK_SLOTS


def test_dropped_track_stays_eligible_and_can_come_straight_back(store: CareerStore) -> None:
    """A drop is not a retire: the track is not recorded as used."""
    matches = _matches()
    advance_career(store, KEY, _ctx(matches, PEERS), WEAK_LANING)
    dropped = next(row[1] for row in _ladder(store) if row[0] == 0)

    drop_block(store, KEY, 0, _ctx(matches, PEERS), WEAK_LANING)

    assert dropped not in store.used_track_keys(KEY)
    assert dropped in {row[1] for row in _ladder(store)}


def test_drop_block_shows_no_block_complete_banner(store: CareerStore) -> None:
    matches = _matches()
    advance_career(store, KEY, _ctx(matches, PEERS), WEAK_LANING)

    snapshot = drop_block(store, KEY, 0, _ctx(matches, PEERS), WEAK_LANING)

    assert snapshot.pending_congrats == ""
    assert store.peek_pending_congrats(KEY) == ""


def test_dropping_a_queued_slot_leaves_the_live_block_alone(store: CareerStore) -> None:
    matches = _matches()
    advance_career(store, KEY, _ctx(matches, PEERS), WEAK_LANING)
    live_before = [row for row in _ladder(store) if row[0] == 0]

    drop_block(store, KEY, 1, _ctx(matches, PEERS), WEAK_LANING)

    assert [row for row in _ladder(store) if row[0] == 0] == live_before


def test_dropping_an_empty_slot_is_a_no_op(store: CareerStore) -> None:
    matches = _matches()
    advance_career(store, KEY, _ctx(matches, PEERS), WEAK_LANING)
    before = _ladder(store)

    drop_block(store, KEY, BLOCK_SLOTS + 5, _ctx(matches, PEERS), WEAK_LANING)

    assert _ladder(store) == before


def test_goals_written_without_peers_are_flagged_unseeded(store: CareerStore) -> None:
    advance_career(store, KEY, _ctx(_matches(), {}), WEAK_LANING)

    assert all(not goal.peer_seeded for goal in store.load_goals(KEY))


def test_since_ms_migration_keeps_older_rows_unseeded(tmp_path: Path) -> None:
    """A ladder on disk from before the column existed must not read as seeded."""
    path = tmp_path / "career.sqlite"
    with CareerStore(path) as first:
        advance_career(first, KEY, _ctx(_matches(), PEERS), WEAK_LANING)
    import sqlite3

    with sqlite3.connect(path) as raw:
        raw.execute("ALTER TABLE career_goals DROP COLUMN peer_seeded")

    with CareerStore(path) as reopened:
        assert all(not goal.peer_seeded for goal in reopened.load_goals(KEY))


def test_requested_drop_is_performed_on_the_next_run(store: CareerStore) -> None:
    """The HTTP route cannot see match data, so it queues the drop for the run."""
    matches = _matches()
    advance_career(store, KEY, _ctx(matches, PEERS), WEAK_LANING)
    live = next(row[1] for row in _ladder(store) if row[0] == 0)
    queued = next(row[1] for row in _ladder(store) if row[0] == 1)

    store.request_drop(KEY, 0)
    advance_career(store, KEY, _ctx(matches, PEERS), WEAK_LANING)

    assert next(row[1] for row in _ladder(store) if row[0] == 0) == queued
    assert live not in store.used_track_keys(KEY)


def test_requested_drop_is_consumed_and_does_not_repeat(store: CareerStore) -> None:
    matches = _matches()
    advance_career(store, KEY, _ctx(matches, PEERS), WEAK_LANING)
    store.request_drop(KEY, 0)
    advance_career(store, KEY, _ctx(matches, PEERS), WEAK_LANING)
    after_drop = _ladder(store)

    advance_career(store, KEY, _ctx(matches, PEERS), WEAK_LANING)

    assert store.peek_pending_drop(KEY) is None
    assert _ladder(store) == after_drop


def test_promoted_block_restarts_its_window_after_a_drop(store: CareerStore) -> None:
    """A promoted block must not inherit credit from games already banked."""
    matches = _matches(games=30)
    advance_career(store, KEY, _ctx(matches, PEERS), WEAK_LANING)
    newest = int(matches["game_creation_ms"].max())

    drop_block(store, KEY, 0, _ctx(matches, PEERS), WEAK_LANING)

    promoted = [goal for goal in store.load_goals(KEY) if goal.slot == 0]
    assert promoted and all(goal.since_ms == newest for goal in promoted)


def test_no_pending_drop_by_default(store: CareerStore) -> None:
    advance_career(store, KEY, _ctx(_matches(), PEERS), WEAK_LANING)

    assert store.peek_pending_drop(KEY) is None


def test_career_view_exposes_the_slot_index_for_each_block(store: CareerStore) -> None:
    """The drop button posts a slot, so the payload has to carry one."""
    from league_stats.presentation.career import build_career_view

    snapshot = advance_career(store, KEY, _ctx(_matches(), PEERS), WEAK_LANING)
    view = build_career_view(snapshot)

    assert [block["slot"] for block in view["blocks"]] == list(range(BLOCK_SLOTS))


def test_block_on_a_retired_track_is_replaced(store: CareerStore) -> None:
    """A track removed from TRACK_SPECS leaves a block nothing can regenerate."""
    matches = _matches()
    advance_career(store, KEY, _ctx(matches, PEERS), WEAK_LANING)
    # Simulate a track that was dropped from the pool between releases, the way
    # preview-cache/career.sqlite still holds `economy_discipline` goals.
    store.write_slot(
        KEY,
        0,
        "economy_discipline",
        [goal.rung for goal in store.load_goals(KEY) if goal.slot == 0],
        ["In progress"] * 3,
        since_ms=0,
        peer_seeded=True,
    )

    advance_career(store, KEY, _ctx(matches, PEERS), WEAK_LANING)

    tracks = {goal.track_key for goal in store.load_goals(KEY)}
    assert "economy_discipline" not in tracks
    assert len({goal.slot for goal in store.load_goals(KEY)}) == BLOCK_SLOTS


def test_retired_track_is_not_recorded_as_used_when_purged(store: CareerStore) -> None:
    matches = _matches()
    advance_career(store, KEY, _ctx(matches, PEERS), WEAK_LANING)
    store.write_slot(
        KEY, 1, "economy_discipline",
        [goal.rung for goal in store.load_goals(KEY) if goal.slot == 1],
        ["In progress"] * 3, since_ms=0, peer_seeded=True,
    )

    advance_career(store, KEY, _ctx(matches, PEERS), WEAK_LANING)

    assert "economy_discipline" not in store.used_track_keys(KEY)


def test_purging_a_retired_track_raises_no_congrats_banner(store: CareerStore) -> None:
    matches = _matches()
    advance_career(store, KEY, _ctx(matches, PEERS), WEAK_LANING)
    store.write_slot(
        KEY, 0, "economy_discipline",
        [goal.rung for goal in store.load_goals(KEY) if goal.slot == 0],
        ["In progress"] * 3, since_ms=0, peer_seeded=True,
    )

    snapshot = advance_career(store, KEY, _ctx(matches, PEERS), WEAK_LANING)

    assert snapshot.pending_congrats == ""


def test_purging_a_live_orphan_keeps_the_queued_block_queued(store: CareerStore) -> None:
    """The freed slot is refilled by current ranking, not by promoting the queue.

    Unlike a retire or a manual drop, an orphaned block was never a legitimate
    goal, so there is nothing to reward with a promotion. Refilling slot 0 from
    the live track ranking puts the most relevant track in front of the player.
    """
    matches = _matches()
    advance_career(store, KEY, _ctx(matches, PEERS), WEAK_LANING)
    queued = next(
        goal.track_key for goal in store.load_goals(KEY) if goal.slot == 1
    )
    store.write_slot(
        KEY, 0, "economy_discipline",
        [goal.rung for goal in store.load_goals(KEY) if goal.slot == 0],
        ["In progress"] * 3, since_ms=0, peer_seeded=True,
    )

    advance_career(store, KEY, _ctx(matches, PEERS), WEAK_LANING)

    still_queued = {goal.track_key for goal in store.load_goals(KEY) if goal.slot == 1}
    live = {goal.track_key for goal in store.load_goals(KEY) if goal.slot == 0}
    assert still_queued == {queued}
    assert live and live != {queued}


def test_requested_drop_survives_the_two_stage_regenerate(store: CareerStore) -> None:
    """The drop lands in stage A (peer-blind); stage B retargets the replacement.

    A regenerate job runs advance_career twice: worker.py:262 without peers, then
    worker.py:312 with them. The drop must be performed exactly once, and the
    block generated to replace it must not keep stage A's own-p75 rungs.
    """
    matches = _matches()
    advance_career(store, KEY, _ctx(matches, PEERS), WEAK_LANING)
    live = next(row[1] for row in _ladder(store) if row[0] == 0)
    queued = next(row[1] for row in _ladder(store) if row[0] == 1)

    store.request_drop(KEY, 0)
    advance_career(store, KEY, _ctx(matches, {}), WEAK_LANING)      # stage A
    advance_career(store, KEY, _ctx(matches, PEERS), WEAK_LANING)   # stage B

    ladder = _ladder(store)
    assert next(row[1] for row in ladder if row[0] == 0) == queued
    assert len({row[0] for row in ladder}) == BLOCK_SLOTS
    assert all(goal.peer_seeded for goal in store.load_goals(KEY))
    assert live not in store.used_track_keys(KEY)
    assert store.peek_pending_drop(KEY) is None
