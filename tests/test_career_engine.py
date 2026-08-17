"""Career ladder seeding, measurement, retirement and regeneration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pytest

from league_stats.analysis.career.engine import advance_career
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


def _matches(games: int = 20, *, cspm: float = 6.0, deaths: float = 3.0) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "game_creation_ms": list(range(games)),
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


def test_first_run_seeds_three_blocks_weakest_first(store: CareerStore) -> None:
    snapshot = advance_career(store, KEY, _ctx(_matches()), WEAK_LANING)

    assert [block.slot for block in snapshot.blocks] == [0, 1, 2]
    assert [block.track_key for block in snapshot.blocks] == [
        "laning_income",
        "death_discipline",
        "economy_discipline",
    ]
    assert snapshot.pending_congrats == ""


def test_only_the_live_block_is_measured(store: CareerStore) -> None:
    snapshot = advance_career(store, KEY, _ctx(_matches()), WEAK_LANING)

    live, second, third = snapshot.blocks
    assert live.display_states[0] == "In progress"
    assert second.display_states == ["Locked"] * 3
    assert third.display_states == ["Locked"] * 3
    assert second.hits == [0, 0, 0]


def test_later_goals_stay_locked_until_the_first_one_clears(store: CareerStore) -> None:
    snapshot = advance_career(store, KEY, _ctx(_matches()), WEAK_LANING)

    assert snapshot.blocks[0].display_states == ["In progress", "Locked", "Locked"]


def test_clearing_the_live_block_retires_shifts_and_regenerates(store: CareerStore) -> None:
    ctx = _ctx(_matches())
    advance_career(store, KEY, ctx, WEAK_LANING)

    # Every laning_income rung asks for CS/min above 6.5; 8.0 clears all three.
    cleared_ctx = _ctx(_matches(cspm=8.0))
    snapshot = advance_career(store, KEY, cleared_ctx, WEAK_LANING)

    assert snapshot.pending_congrats == "laning_income"
    assert [block.track_key for block in snapshot.blocks][:2] == [
        "death_discipline",
        "economy_discipline",
    ]
    assert len(snapshot.blocks) == 3
    assert snapshot.blocks[2].track_key not in {"death_discipline", "economy_discipline"}
    assert store.used_track_keys(KEY) == {"laning_income"}


def test_the_congrats_banner_is_shown_only_once(store: CareerStore) -> None:
    advance_career(store, KEY, _ctx(_matches()), WEAK_LANING)
    first = advance_career(store, KEY, _ctx(_matches(cspm=8.0)), WEAK_LANING)
    second = advance_career(store, KEY, _ctx(_matches(cspm=8.0)), WEAK_LANING)

    assert first.pending_congrats == "laning_income"
    assert second.pending_congrats == ""


def test_rung_targets_are_frozen_across_runs(store: CareerStore) -> None:
    first = advance_career(store, KEY, _ctx(_matches(cspm=6.0)), WEAK_LANING)
    frozen = [goal.rung.target for goal in first.blocks[0].goals]

    improved = _ctx(_matches(cspm=7.0))
    second = advance_career(store, KEY, improved, WEAK_LANING)

    assert [goal.rung.target for goal in second.blocks[0].goals] == frozen


def test_a_cleared_goal_drifting_below_the_hold_bar_is_revoked(store: CareerStore) -> None:
    # Rungs freeze at 6.5 / 7.0 / 7.5, so 6.6 clears goal 1 only and the block lives on.
    advance_career(store, KEY, _ctx(_matches(cspm=6.0)), WEAK_LANING)
    cleared = advance_career(store, KEY, _ctx(_matches(cspm=6.6)), WEAK_LANING)
    assert cleared.blocks[0].display_states == ["Cleared", "In progress", "Locked"]

    dropped = advance_career(store, KEY, _ctx(_matches(cspm=5.0)), WEAK_LANING)
    assert dropped.blocks[0].track_key == "laning_income"
    assert dropped.blocks[0].display_states == ["Revoked", "Locked", "Locked"]


def test_no_eligible_tracks_yields_an_empty_ladder(store: CareerStore) -> None:
    healthy = pd.DataFrame(
        {
            "game_creation_ms": list(range(20)),
            "cspm": [9.0] * 20,
            "deaths_pre20": [1.0] * 20,
            "deaths_before_neutral_objective": [0.0] * 20,
            "avg_unspent_gold": [400.0] * 20,
            "avg_unspent_gold_per_fight": [300.0] * 20,
            "avg_gold_at_death": [200.0] * 20,
            "damage_share": [0.40] * 20,
            "tf_participation": [0.9] * 20,
            "control_wards": [2.0] * 20,
            "objectives_present_rate": [0.9] * 20,
        }
    )
    ctx = TrackContext(
        matches_df=healthy,
        objectives_df=pd.DataFrame({"present": [1, 1, 1, 1]}),
        role="MIDDLE",
        peer_p75={"cspm": 7.5, "damage_share": 0.29},
    )
    snapshot = advance_career(store, KEY, ctx, WEAK_LANING)

    assert snapshot.blocks == []
    assert snapshot.pending_congrats == ""


def test_ladders_do_not_leak_between_builds(store: CareerStore) -> None:
    other = build_key("p", "Aatrox", "TOP")
    advance_career(store, KEY, _ctx(_matches()), WEAK_LANING)

    assert store.load_goals(other) == []
