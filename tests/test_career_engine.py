"""Career ladder seeding, measurement, retirement and regeneration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pytest

from league_stats.analysis.career.engine import advance_career
from league_stats.analysis.career.models import BLOCK_SLOTS
from league_stats.analysis.career.tracks import TrackContext
from league_stats.infra.career_store import CareerStore, build_key

KEY = build_key("p", "Viktor", "MIDDLE")


@dataclass(frozen=True)
class _Component:
    name: str
    score: float


WEAK_LANING = [
    _Component("Laning", 10.0),
    _Component("Survival", 20.0),
    _Component("Economy", 30.0),
    _Component("Objectives", 80.0),
    _Component("Fight", 85.0),
    _Component("Vision", 90.0),
]


HOUR = 3_600_000


def _batch(games: int = 20, *, start: int = 0, cspm: float = 6.0, deaths: float = 3.0) -> pd.DataFrame:
    """One run of games, timestamped so later batches really are newer."""
    return pd.DataFrame(
        {
            "game_creation_ms": [(start + i) * HOUR for i in range(games)],
            "cspm": [cspm] * games,
            "deaths_pre20": [deaths] * games,
            "deaths_before_neutral_objective": [0.0] * games,
            "avg_unspent_gold": [1650.0] * games,
            "avg_unspent_gold_per_fight": [1420.0] * games,
            "avg_gold_at_death": [1180.0] * games,
            "damage_share": [0.20] * games,
            "vspm": [0.6] * games,
            "tf_participation": [0.62] * games,
            "control_wards": [0.0] * games,
            "objectives_present_rate": [0.4] * games,
        }
    )


def _matches(*batches: pd.DataFrame) -> pd.DataFrame:
    """A full history: every batch played so far, oldest first."""
    return pd.concat(batches, ignore_index=True) if batches else _batch()


def _ctx(matches: pd.DataFrame) -> TrackContext:
    return TrackContext(
        matches_df=matches,
        objectives_df=pd.DataFrame({"present": [1, 0, 0, 0, 1, 0]}),
        role="MIDDLE",
        peer_p75={"cspm": 7.5, "damage_share": 0.29},
    )


@pytest.fixture()
def store(tmp_path: Path):
    with CareerStore(tmp_path / "career.sqlite") as handle:
        yield handle


def test_first_run_seeds_a_full_ladder_weakest_first(store: CareerStore) -> None:
    snapshot = advance_career(store, KEY, _ctx(_batch()), WEAK_LANING)

    assert len(snapshot.blocks) == BLOCK_SLOTS
    assert [block.slot for block in snapshot.blocks] == list(range(BLOCK_SLOTS))
    assert [block.track_key for block in snapshot.blocks] == [
        "laning_income",
        "death_discipline",
    ]
    assert snapshot.pending_congrats == ""


def test_only_the_live_block_is_measured(store: CareerStore) -> None:
    snapshot = advance_career(store, KEY, _ctx(_batch()), WEAK_LANING)

    live, queued = snapshot.blocks
    assert live.display_states[0] == "In progress"
    assert queued.display_states == ["Locked"] * 3
    assert queued.hits == [0, 0, 0]


def test_every_goal_in_the_live_block_counts_in_parallel(store: CareerStore) -> None:
    # Three sequential windows would need 60 games to clear one block, so all
    # three goals are live and measured from the moment the block appears.
    snapshot = advance_career(store, KEY, _ctx(_batch()), WEAK_LANING)

    assert snapshot.blocks[0].display_states == ["In progress"] * 3


def test_a_fresh_block_starts_from_zero_on_existing_history(store: CareerStore) -> None:
    history = _batch(20, start=0, cspm=6.0)
    first = advance_career(store, KEY, _ctx(history), WEAK_LANING)

    assert first.blocks[0].hits == [0, 0, 0]
    assert first.pending_congrats == ""

    # Re-running on the same history changes nothing: no games are newer.
    again = advance_career(store, KEY, _ctx(history), WEAK_LANING)
    assert again.blocks[0].hits == [0, 0, 0]
    assert again.pending_congrats == ""


def test_a_promoted_block_does_not_inherit_the_previous_blocks_games(
    store: CareerStore,
) -> None:
    # The run that clears block 1 is full of games that would also satisfy
    # block 2 outright. Block 2 must start counting from its promotion, or
    # clearing one block would cascade straight through the next.
    history = _batch(20, start=0, cspm=6.0, deaths=3.0)
    seeded = advance_career(store, KEY, _ctx(history), WEAK_LANING)
    assert seeded.blocks[1].track_key == "death_discipline"

    # 20 games with high CS and zero early deaths: clears laning_income, and
    # would clear every death_discipline rung too if they counted.
    cleared = _matches(history, _batch(20, start=20, cspm=9.0, deaths=0.0))
    snapshot = advance_career(store, KEY, _ctx(cleared), WEAK_LANING)

    assert snapshot.pending_congrats == "laning_income"
    assert snapshot.blocks[0].track_key == "death_discipline"
    assert snapshot.blocks[0].hits == [0, 0, 0]
    assert snapshot.blocks[0].display_states == ["In progress"] * 3


def test_a_block_clears_on_games_played_after_it_appeared(store: CareerStore) -> None:
    history = _batch(20, start=0, cspm=6.0)
    advance_career(store, KEY, _ctx(history), WEAK_LANING)

    played_since = _matches(history, _batch(20, start=20, cspm=9.0))
    snapshot = advance_career(store, KEY, _ctx(played_since), WEAK_LANING)

    assert snapshot.pending_congrats == "laning_income"


def test_clearing_the_live_block_retires_shifts_and_regenerates(store: CareerStore) -> None:
    history = _batch(20, start=0, cspm=6.0)
    advance_career(store, KEY, _ctx(history), WEAK_LANING)

    # 20 fresh games well above every laning_income rung clear the whole block.
    cleared_ctx = _ctx(_matches(history, _batch(20, start=20, cspm=9.0)))
    snapshot = advance_career(store, KEY, cleared_ctx, WEAK_LANING)

    assert snapshot.pending_congrats == "laning_income"
    assert len(snapshot.blocks) == BLOCK_SLOTS
    # The queued block shifted left and became live; a fresh one filled its place.
    assert snapshot.blocks[0].track_key == "death_discipline"
    assert snapshot.blocks[1].track_key not in {"laning_income", "death_discipline"}
    assert store.used_track_keys(KEY) == {"laning_income"}


def test_the_congrats_banner_is_shown_only_once(store: CareerStore) -> None:
    history = _batch(20, start=0, cspm=6.0)
    cleared = _matches(history, _batch(20, start=20, cspm=9.0))
    advance_career(store, KEY, _ctx(history), WEAK_LANING)
    first = advance_career(store, KEY, _ctx(cleared), WEAK_LANING)
    second = advance_career(store, KEY, _ctx(cleared), WEAK_LANING)

    assert first.pending_congrats == "laning_income"
    assert second.pending_congrats == ""


def test_rung_targets_are_frozen_across_runs(store: CareerStore) -> None:
    history = _batch(20, start=0, cspm=6.0)
    first = advance_career(store, KEY, _ctx(history), WEAK_LANING)
    frozen = [goal.rung.target for goal in first.blocks[0].goals]

    improved = _ctx(_matches(history, _batch(10, start=20, cspm=7.0)))
    second = advance_career(store, KEY, improved, WEAK_LANING)

    assert [goal.rung.target for goal in second.blocks[0].goals] == frozen


def test_a_cleared_goal_drifting_below_the_hold_bar_is_revoked(store: CareerStore) -> None:
    # Rungs freeze at 6.5 / 7.0 / 7.5, so 6.6 clears goal 1 only, leaving the
    # other two counting in parallel and the block still live.
    history = _batch(20, start=0, cspm=6.0)
    advance_career(store, KEY, _ctx(history), WEAK_LANING)

    good = _matches(history, _batch(20, start=20, cspm=6.6))
    cleared = advance_career(store, KEY, _ctx(good), WEAK_LANING)
    assert cleared.blocks[0].display_states == ["Cleared", "In progress", "In progress"]

    slump = _matches(good, _batch(20, start=40, cspm=5.0))
    dropped = advance_career(store, KEY, _ctx(slump), WEAK_LANING)
    assert dropped.blocks[0].track_key == "laning_income"
    assert dropped.blocks[0].display_states[0] == "Revoked"


def _healthy_ctx() -> TrackContext:
    healthy = pd.DataFrame(
        {
            "game_creation_ms": [i * HOUR for i in range(20)],
            "cspm": [8.0 + i * 0.1 for i in range(20)],
            "vspm": [1.0 + i * 0.02 for i in range(20)],
            "deaths_pre20": [3.0] * 20,
            "deaths_before_neutral_objective": [0.0] * 20,
            "avg_unspent_gold": [400.0] * 20,
            "avg_unspent_gold_per_fight": [300.0] * 20,
            "avg_gold_at_death": [200.0] * 20,
            "damage_share": [0.40 + i * 0.002 for i in range(20)],
            "tf_participation": [0.9] * 20,
            "control_wards": [2.0] * 20,
            "objectives_present_rate": [0.9] * 20,
        }
    )
    return TrackContext(
        matches_df=healthy,
        objectives_df=pd.DataFrame({"present": [1, 1, 1, 1]}),
        role="MIDDLE",
        peer_p75={"cspm": 7.5, "damage_share": 0.29},
    )


def test_a_healthy_player_still_gets_a_full_ladder(store: CareerStore) -> None:
    snapshot = advance_career(store, KEY, _healthy_ctx(), WEAK_LANING)

    assert len(snapshot.blocks) == BLOCK_SLOTS
    assert [block.slot for block in snapshot.blocks] == list(range(BLOCK_SLOTS))


def test_a_full_ladder_without_any_peer_percentiles(store: CareerStore) -> None:
    ctx = TrackContext(
        matches_df=_healthy_ctx().matches_df,
        objectives_df=_healthy_ctx().objectives_df,
        role="MIDDLE",
        peer_p75={},
    )
    snapshot = advance_career(store, KEY, ctx, WEAK_LANING)

    assert len(snapshot.blocks) == BLOCK_SLOTS


def test_significant_tracks_are_handed_out_first(store: CareerStore) -> None:
    # deaths_pre20 is 3.0 (>= the early-deaths signal) while every peer-driven
    # metric sits above peer p75, so only death_discipline is a real finding.
    snapshot = advance_career(store, KEY, _healthy_ctx(), WEAK_LANING)
    assert snapshot.blocks[0].track_key == "death_discipline"


def test_an_empty_match_table_yields_an_empty_ladder(store: CareerStore) -> None:
    ctx = TrackContext(
        matches_df=pd.DataFrame(),
        objectives_df=pd.DataFrame(),
        role="MIDDLE",
        peer_p75={},
    )
    snapshot = advance_career(store, KEY, ctx, WEAK_LANING)

    assert snapshot.blocks == []
    assert snapshot.pending_congrats == ""


def test_ladders_do_not_leak_between_builds(store: CareerStore) -> None:
    other = build_key("p", "Aatrox", "TOP")
    advance_career(store, KEY, _ctx(_batch()), WEAK_LANING)

    assert store.load_goals(other) == []
