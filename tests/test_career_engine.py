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


# Values that clear any "at least" rung and any "under" rung the bank can build,
# and values that fail both. A block's goals now depend on which steps the bank
# ranked highest, so these tests state the outcome they want rather than nudging
# one metric and hoping it is the one the block picked.
def _apply_outcome(frame: pd.DataFrame, outcome: str) -> pd.DataFrame:
    override = {"clears": _CLEARS, "fails": _FAILS}.get(outcome)
    if override is None:
        return frame
    for column, value in override.items():
        if column in frame.columns:
            frame[column] = value
    return frame


_CLEARS: dict[str, float] = {
    "cspm": 20.0, "cs10": 200.0, "gd10": 5000.0, "gd15": 5000.0, "gd20": 5000.0,
    "xpd10": 5000.0, "damage_share": 0.9, "vspm": 5.0, "vspm10": 5.0,
    "wards_killed": 40.0, "wards_placed": 60.0, "avg_wards_before_objective": 10.0,
    "control_wards": 20.0, "objectives_present_rate": 1.0, "tf_participation": 1.0,
    "objective_trade_success_rate": 1.0, "towers_taken": 20.0, "kp15": 1.0,
    "tf_won_share": 1.0, "pct_advantaged_fights": 1.0, "ccpm": 60.0, "hpm": 3000.0,
    "early_ganks": 20.0, "roams_pre15": 20.0, "gold10": 20000.0,
    "deaths_pre20": 0.0, "deaths_pre14": 0.0, "greed_deaths": 0.0,
    "solo_deaths": 0.0, "outnumbered_deaths": 0.0, "shutdown_given": 0.0,
    "time_dead_s": 0.0, "deaths_before_neutral_objective": 0.0,
    "first_item_min": 1.0, "avg_unspent_gold": 0.0, "avg_gold_at_death": 0.0,
    "unproductive_absence_rate": 0.0, "first_recall_min": 1.0,
    "under_enemy_tower_laning_deaths": 0.0, "gank_deaths_laning": 0.0,
}
_FAILS: dict[str, float] = {
    "cspm": 0.5, "cs10": 5.0, "gd10": -5000.0, "gd15": -5000.0, "gd20": -5000.0,
    "xpd10": -5000.0, "damage_share": 0.01, "vspm": 0.01, "vspm10": 0.01,
    "wards_killed": 0.0, "wards_placed": 0.0, "avg_wards_before_objective": 0.0,
    "control_wards": 0.0, "objectives_present_rate": 0.0, "tf_participation": 0.0,
    "objective_trade_success_rate": 0.0, "towers_taken": 0.0, "kp15": 0.0,
    "tf_won_share": 0.0, "pct_advantaged_fights": 0.0, "ccpm": 0.0, "hpm": 0.0,
    "early_ganks": 0.0, "roams_pre15": 0.0, "gold10": 0.0,
    "deaths_pre20": 30.0, "deaths_pre14": 30.0, "greed_deaths": 30.0,
    "solo_deaths": 30.0, "outnumbered_deaths": 30.0, "shutdown_given": 9000.0,
    "time_dead_s": 9000.0, "deaths_before_neutral_objective": 30.0,
    "first_item_min": 90.0, "avg_unspent_gold": 90000.0, "avg_gold_at_death": 90000.0,
    "unproductive_absence_rate": 1.0, "first_recall_min": 90.0,
    "under_enemy_tower_laning_deaths": 30.0, "gank_deaths_laning": 30.0,
}


def _batch(
    games: int = 20,
    *,
    start: int = 0,
    cspm: float = 6.0,
    deaths: float = 3.0,
    outcome: str = "",
) -> pd.DataFrame:
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
            "deaths_pre14": [1.0] * games,
            "greed_deaths": [0.0] * games,
            "solo_deaths": [0.0] * games,
            "outnumbered_deaths": [0.0] * games,
            "shutdown_given": [150.0] * games,
            "time_dead_s": [60.0] * games,
            "gd10": [250.0] * games,
            "gd15": [400.0] * games,
            "gd20": [500.0] * games,
            "xpd10": [200.0] * games,
            "cs10": [70.0] * games,
            "gold10": [3600.0] * games,
            "first_item_min": [10.0] * games,
            "pct_advantaged_fights": [0.55] * games,
            "kp15": [0.6] * games,
            "tf_won_share": [0.55] * games,
            "ccpm": [8.0] * games,
            "vspm10": [0.9] * games,
            "wards_killed": [3.0] * games,
            "wards_placed": [11.0] * games,
            "avg_wards_before_objective": [1.5] * games,
            "objective_trade_success_rate": [0.6] * games,
            "unproductive_absence_rate": [0.1] * games,
            "towers_taken": [2.0] * games,
            "first_recall_min": [5.0] * games,
            "early_ganks": [1.5] * games,
            "roams_pre15": [2.0] * games,
            "hpm": [200.0] * games,
            "under_enemy_tower_laning_deaths": [0.0] * games,
            "gank_deaths_laning": [0.0] * games,
        }
    ).pipe(_apply_outcome, outcome)


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
        "laning",
        "survival",
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
    assert seeded.blocks[1].track_key == "survival"

    # 20 games with high CS and zero early deaths: clears laning_income, and
    # would clear every death_discipline rung too if they counted.
    cleared = _matches(history, _batch(20, start=20, outcome="clears"))
    snapshot = advance_career(store, KEY, _ctx(cleared), WEAK_LANING)

    assert snapshot.pending_congrats == "laning"
    assert snapshot.blocks[0].track_key == "survival"
    assert snapshot.blocks[0].hits == [0, 0, 0]
    assert snapshot.blocks[0].display_states == ["In progress"] * 3


def test_a_block_clears_on_games_played_after_it_appeared(store: CareerStore) -> None:
    history = _batch(20, start=0, cspm=6.0)
    advance_career(store, KEY, _ctx(history), WEAK_LANING)

    played_since = _matches(history, _batch(20, start=20, outcome="clears"))
    snapshot = advance_career(store, KEY, _ctx(played_since), WEAK_LANING)

    assert snapshot.pending_congrats == "laning"


def test_clearing_the_live_block_retires_shifts_and_regenerates(store: CareerStore) -> None:
    history = _batch(20, start=0, cspm=6.0)
    advance_career(store, KEY, _ctx(history), WEAK_LANING)

    # 20 fresh games well above every laning rung clear the whole block.
    cleared_ctx = _ctx(_matches(history, _batch(20, start=20, outcome="clears")))
    snapshot = advance_career(store, KEY, cleared_ctx, WEAK_LANING)

    assert snapshot.pending_congrats == "laning"
    assert len(snapshot.blocks) == BLOCK_SLOTS
    # The queued block shifted left and became live; a fresh one filled its place.
    assert snapshot.blocks[0].track_key == "survival"
    assert snapshot.blocks[1].track_key not in {"laning", "survival"}
    assert store.used_track_keys(KEY) == {"laning"}


def test_the_congrats_banner_survives_until_acknowledged(store: CareerStore) -> None:
    # Under group watch a background rebuild the reader never opens must not
    # swallow the milestone, so the flag persists until it is explicitly cleared.
    history = _batch(20, start=0, cspm=6.0)
    cleared = _matches(history, _batch(20, start=20, outcome="clears"))
    advance_career(store, KEY, _ctx(history), WEAK_LANING)
    first = advance_career(store, KEY, _ctx(cleared), WEAK_LANING)
    second = advance_career(store, KEY, _ctx(cleared), WEAK_LANING)

    assert first.pending_congrats == "laning"
    assert second.pending_congrats == "laning"

    store.clear_pending_congrats(KEY)
    third = advance_career(store, KEY, _ctx(cleared), WEAK_LANING)
    assert third.pending_congrats == ""


def test_rung_targets_are_frozen_across_runs(store: CareerStore) -> None:
    history = _batch(20, start=0, cspm=6.0)
    first = advance_career(store, KEY, _ctx(history), WEAK_LANING)
    frozen = [goal.rung.target for goal in first.blocks[0].goals]

    improved = _ctx(_matches(history, _batch(10, start=20, outcome="clears")))
    second = advance_career(store, KEY, improved, WEAK_LANING)

    assert [goal.rung.target for goal in second.blocks[0].goals] == frozen


def test_a_cleared_goal_drifting_below_the_hold_bar_is_revoked(store: CareerStore) -> None:
    """Clear one goal only, so the block stays live, then let it drift.

    A block's goals are three different metrics drawn from its category bank, so
    the batch has to satisfy the first goal's column and fail the rest -- clearing
    all three would retire the block and the drift would land on its replacement.
    """
    history = _batch(20, start=0)
    advance_career(store, KEY, _ctx(history), WEAK_LANING)
    live = [goal for goal in store.load_goals(KEY) if goal.slot == 0]
    first = live[0].rung

    one_goal = _batch(20, start=20, outcome="fails")
    one_goal[first.column] = _CLEARS[first.column]
    good = _matches(history, one_goal)
    cleared = advance_career(store, KEY, _ctx(good), WEAK_LANING)
    assert cleared.blocks[0].display_states[0] == "Cleared"
    assert "In progress" in cleared.blocks[0].display_states[1:]

    slump = _matches(good, _batch(20, start=40, outcome="fails"))
    dropped = advance_career(store, KEY, _ctx(slump), WEAK_LANING)
    assert dropped.blocks[0].track_key == "laning"
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


def test_the_weakest_category_becomes_the_live_block(store: CareerStore) -> None:
    """Blocks are categories, ordered by improvement-score weakness.

    Which *goals* land inside the block is the step bank's job, and a diagnosed
    habit outranking a stretch goal is covered in tests/test_career_step_bank.py.
    """
    weakest = min(WEAK_LANING, key=lambda comp: comp.score).name
    snapshot = advance_career(store, KEY, _healthy_ctx(), WEAK_LANING)

    assert weakest == "Laning"
    assert snapshot.blocks[0].track_key == "laning"


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
