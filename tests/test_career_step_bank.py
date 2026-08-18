"""The Career step bank: a block is a category, its goals are that bank's top 3.

The pool used to hold exactly one track per score category, so the category the
ladder picked *determined* the goal -- 6 tracks, 18 goals, and 17 of those 18
ending in the words "in 15 of 20 games". Now a block is a category and its three
goals are the three steps that category's bank says this player most needs, so two
players weak at Survival get different blocks.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import pytest

from league_stats.analysis.career.engine import candidate_step_keys
from league_stats.analysis.career.models import GOALS_PER_BLOCK
from league_stats.analysis.career.steps import (
    BLOCK_CATEGORY_KEYS,
    CATEGORY_SURVIVAL,
    CATEGORY_UTILITY,
    STEP_BANK,
    rank_steps,
    steps_for_category,
)
from league_stats.analysis.career.tracks import (
    LEGACY_TRACK_KEYS,
    TRACKS_BY_KEY,
    TrackContext,
    build_rungs,
    selectable_track_keys,
)

HOUR = 3_600_000
PEERS = {"cspm": 7.5, "vspm": 1.25, "damage_share": 0.29, "ccpm": 12.0,
         "early_ganks": 2.0, "roams_pre15": 4.0}

# A player with nothing diagnosable: every "problem" column is clean.
HEALTHY: dict[str, float] = {
    "cspm": 7.0, "vspm": 1.1, "damage_share": 0.26, "deaths_pre20": 1.0,
    "deaths_pre14": 0.5, "deaths_before_neutral_objective": 0.0,
    "objectives_present_rate": 0.8, "control_wards": 2.0, "tf_participation": 0.8,
    "first_item_min": 9.0, "gd10": 400.0, "gd15": 700.0, "gd20": 900.0,
    "xpd10": 300.0, "greed_deaths": 0.0, "solo_deaths": 0.0,
    "outnumbered_deaths": 0.0, "shutdown_given": 0.0, "time_dead_s": 40.0,
    "gank_deaths_laning": 0.0, "under_enemy_tower_laning_deaths": 0.0,
    "pct_advantaged_fights": 0.7, "objective_trade_success_rate": 0.8,
    "unproductive_absence_rate": 0.05, "towers_taken": 3.0, "vspm10": 1.2,
    "wards_killed": 5.0, "avg_wards_before_objective": 2.5,
    "avg_unspent_gold": 200.0, "avg_gold_at_death": 300.0, "first_recall_min": 4.0,
    "gold10": 3800.0, "early_ganks": 2.0, "roams_pre15": 3.0, "ccpm": 14.0,
    "hpm": 300.0, "spm": 200.0, "kp15": 0.7, "tf_won_share": 0.7,
    "wards_placed": 14.0, "cs10": 75.0,
}


@dataclass(frozen=True)
class _Component:
    name: str
    score: float


def _components(**overrides: float) -> list[_Component]:
    base = {
        "Laning": 50.0, "Early game": 50.0, "Setup": 50.0, "Utility": 50.0,
        "Economy": 50.0, "Fight": 50.0, "Survival": 50.0, "Vision": 50.0,
        "Objectives": 50.0,
    }
    base.update(overrides)
    return [_Component(name, score) for name, score in base.items()]


def _matches(games: int = 25, **overrides: float) -> pd.DataFrame:
    row = {**HEALTHY, **overrides}
    frame = pd.DataFrame({col: [value] * games for col, value in row.items()})
    frame["game_creation_ms"] = [i * HOUR for i in range(games)]
    return frame


def _ctx(matches: pd.DataFrame | None = None, role: str = "MIDDLE") -> TrackContext:
    return TrackContext(
        matches_df=_matches() if matches is None else matches,
        objectives_df=pd.DataFrame({"present": [1] * 8}),
        role=role,
        peer_p75=PEERS,
    )


def _goal_texts(category: str, ctx: TrackContext) -> list[str]:
    rungs = build_rungs(TRACKS_BY_KEY[category], ctx)
    return [rung.text for rung in (rungs or ())]


# --- the bank --------------------------------------------------------------


def test_the_bank_is_deep_enough_to_vary() -> None:
    assert len(STEP_BANK) >= 35


def test_every_step_key_is_unique() -> None:
    keys = [step.key for step in STEP_BANK]
    assert len(keys) == len(set(keys))


def test_every_category_has_a_bank_worth_choosing_from() -> None:
    """Three goals picked from three steps is not a choice."""
    for category in BLOCK_CATEGORY_KEYS:
        pool = [step for step in STEP_BANK if step.category == category]
        assert len(pool) > GOALS_PER_BLOCK, f"{category} has only {len(pool)} steps"


@pytest.mark.parametrize("step", STEP_BANK, ids=lambda step: step.key)
def test_every_step_declares_a_specificity_and_a_known_category(step) -> None:
    assert 1 <= step.specificity <= 3
    assert step.category in BLOCK_CATEGORY_KEYS


@pytest.mark.parametrize("step", STEP_BANK, ids=lambda step: step.key)
def test_every_step_builds_a_rung_or_declines_cleanly(step) -> None:
    role = step.roles[0] if step.roles else "MIDDLE"
    rung = step.build(_ctx(role=role))
    if rung is None:
        return
    assert rung.text and rung.column and rung.need > 0


@pytest.mark.parametrize("step", STEP_BANK, ids=lambda step: step.key)
def test_severity_is_a_finite_number_for_every_step(step) -> None:
    role = step.roles[0] if step.roles else "MIDDLE"
    value = step.severity(_ctx(role=role))
    assert value == value and value >= 0.0


# --- blocks are categories filled from the bank ----------------------------


@pytest.mark.parametrize("category", BLOCK_CATEGORY_KEYS)
def test_every_category_block_fills_three_goals(category: str) -> None:
    role = "UTILITY" if category == CATEGORY_UTILITY else "MIDDLE"
    rungs = build_rungs(TRACKS_BY_KEY[category], _ctx(role=role))

    assert rungs is not None and len(rungs) == GOALS_PER_BLOCK


@pytest.mark.parametrize("category", BLOCK_CATEGORY_KEYS)
def test_a_block_never_states_the_same_column_twice(category: str) -> None:
    role = "UTILITY" if category == CATEGORY_UTILITY else "MIDDLE"
    rungs = build_rungs(TRACKS_BY_KEY[category], _ctx(role=role)) or ()
    columns = [rung.column for rung in rungs]

    assert len(columns) == len(set(columns))


def test_two_players_weak_at_survival_get_different_survival_goals() -> None:
    """The whole point of the bank."""
    greedy = _ctx(_matches(greed_deaths=2.0, shutdown_given=800.0))
    caught = _ctx(_matches(solo_deaths=2.0, deaths_before_neutral_objective=1.0))

    assert _goal_texts(CATEGORY_SURVIVAL, greedy) != _goal_texts(CATEGORY_SURVIVAL, caught)


def test_a_diagnosed_habit_makes_it_into_the_block() -> None:
    greedy = _ctx(_matches(greed_deaths=2.0))

    assert any("greed death" in text for text in _goal_texts(CATEGORY_SURVIVAL, greedy))


def test_a_clean_player_is_not_told_to_stop_doing_something_they_never_do() -> None:
    clean = _ctx(_matches(greed_deaths=0.0, solo_deaths=0.0))
    texts = _goal_texts(CATEGORY_SURVIVAL, clean)

    assert texts
    assert not any("greed death" in text for text in texts)


def test_the_worst_offence_is_ranked_ahead_of_a_milder_one() -> None:
    ctx = _ctx(_matches(greed_deaths=0.2, solo_deaths=2.0))
    ranked = rank_steps(steps_for_category(CATEGORY_SURVIVAL, "MIDDLE"), ctx)
    keys = [step.key for step in ranked]

    assert keys.index("no_solo_deaths") < keys.index("greed_discipline")


def test_specificity_outranks_severity() -> None:
    """A named habit beats a generic shortfall even when the shortfall is larger."""
    ctx = _ctx(_matches(greed_deaths=1.0, deaths_pre20=6.0))
    ranked = rank_steps(steps_for_category(CATEGORY_SURVIVAL, "MIDDLE"), ctx)

    assert ranked[0].specificity == 3


# --- role gating -----------------------------------------------------------


def test_a_jungler_is_never_offered_a_support_only_step() -> None:
    offered = {
        step.key
        for category in BLOCK_CATEGORY_KEYS
        for step in steps_for_category(category, "JUNGLE")
    }
    support_only = {
        step.key for step in STEP_BANK if step.roles and set(step.roles) == {"UTILITY"}
    }

    assert support_only
    assert not offered & support_only


def test_a_laner_is_never_offered_a_jungle_only_step() -> None:
    offered = {
        step.key
        for category in BLOCK_CATEGORY_KEYS
        for step in steps_for_category(category, "MIDDLE")
    }
    jungle_only = {
        step.key for step in STEP_BANK if step.roles and set(step.roles) == {"JUNGLE"}
    }

    assert jungle_only
    assert not offered & jungle_only


def test_the_utility_category_only_exists_for_supports() -> None:
    """UTILITY's Utility score could never be addressed before."""
    assert TRACKS_BY_KEY[CATEGORY_UTILITY].serves_role("UTILITY")
    assert not TRACKS_BY_KEY[CATEGORY_UTILITY].serves_role("MIDDLE")


def test_a_support_weak_at_utility_is_offered_that_block() -> None:
    order = candidate_step_keys(
        _components(Utility=15.0), _ctx(role="UTILITY"), set(), set()
    )

    assert order[0] == CATEGORY_UTILITY


def test_a_mid_laner_is_never_offered_the_utility_block() -> None:
    order = candidate_step_keys(_components(Utility=15.0), _ctx(role="MIDDLE"), set(), set())

    assert CATEGORY_UTILITY not in order


# --- block selection ------------------------------------------------------


def test_the_weakest_category_is_offered_first() -> None:
    order = candidate_step_keys(_components(Vision=12.0), _ctx(), set(), set())

    assert order[0] == "vision"


def test_only_categories_are_selectable_never_legacy_keys() -> None:
    order = candidate_step_keys(_components(), _ctx(), set(), set())

    assert not set(order) & LEGACY_TRACK_KEYS
    assert set(order) <= set(selectable_track_keys())


def test_a_taken_block_is_not_offered_again() -> None:
    order = candidate_step_keys(_components(Vision=12.0), _ctx(), {"vision"}, set())

    assert "vision" not in order


def test_a_retired_block_sorts_behind_fresh_ones() -> None:
    fresh = candidate_step_keys(_components(Vision=12.0), _ctx(), set(), set())
    recycled = candidate_step_keys(_components(Vision=12.0), _ctx(), set(), {"vision"})

    assert fresh[0] == "vision"
    assert recycled[0] != "vision"


def test_the_six_legacy_keys_still_resolve_for_display() -> None:
    """A ladder on disk stores its key; an unknown key gets its block purged."""
    for key in LEGACY_TRACK_KEYS:
        assert TRACKS_BY_KEY.get(key) is not None
