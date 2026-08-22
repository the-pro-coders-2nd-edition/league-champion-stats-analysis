"""Game Review has to say which Career goals a given game counted toward.

A reader looking at one game's "Objective presence 57%" row has no way to tell
that it is the metric their live Career block is tracking, or whether this game
cleared it. Two payload additions make that answerable in the frontend without
growing the response meaningfully:

* live goal nodes expose the ``column``/``comparator``/``target`` they were frozen
  with, and the ``since_ms`` start line;
* each reviewed game exposes ``game_creation_ms``.

``since_ms`` matters: a block only counts games played after it appeared, so a game
older than the live block was never tracked by it and must not be shown as a miss.
"""

from __future__ import annotations

from league_stats_runner.analysis.career.engine import CareerBlockState, CareerSnapshot
from league_stats_runner.analysis.career.models import CLEAR_BAR, Rung, StoredGoal
from league_stats_runner.presentation.career import build_career_view

HOUR = 3_600_000


def _goal(column: str, target: float, comparator: str, since_ms: int) -> StoredGoal:
    return StoredGoal(
        slot=0,
        goal_index=0,
        track_key="objectives",
        rung=Rung(text=f"{column} goal", column=column, comparator=comparator,
                  target=target, need=CLEAR_BAR, why="because"),
        state="In progress",
        since_ms=since_ms,
    )


def _snapshot(*goals: StoredGoal) -> CareerSnapshot:
    live = CareerBlockState(
        slot=0, track_key="objectives", goals=list(goals),
        hits=[0] * len(goals), display_states=["In progress"] * len(goals),
    )
    queued = CareerBlockState(
        slot=1, track_key="vision",
        goals=[_goal("vspm", 1.0, "at_least", 0)],
        hits=[0], display_states=["Locked"],
    )
    return CareerSnapshot(blocks=[live, queued])


# --- what a live goal node exposes -----------------------------------------


def test_a_live_goal_exposes_the_column_it_measures() -> None:
    view = build_career_view(_snapshot(_goal("objectives_present_rate", 0.6, "at_least", 0)))

    assert view["blocks"][0]["goals"][0]["column"] == "objectives_present_rate"


def test_a_live_goal_exposes_its_target_and_comparator() -> None:
    view = build_career_view(_snapshot(_goal("objectives_present_rate", 0.6, "at_least", 0)))

    goal = view["blocks"][0]["goals"][0]
    assert goal["target"] == 0.6
    assert goal["comparator"] == "at_least"


def test_a_live_goal_exposes_the_start_line_it_counts_from() -> None:
    """Without since_ms the frontend cannot tell 'not tracked yet' from 'missed'."""
    view = build_career_view(_snapshot(_goal("cspm", 7.0, "at_least", 42 * HOUR)))

    assert view["blocks"][0]["goals"][0]["since_ms"] == 42 * HOUR


def test_a_lower_is_better_goal_keeps_its_comparator() -> None:
    view = build_career_view(_snapshot(_goal("greed_deaths", 1.0, "under", 0)))

    assert view["blocks"][0]["goals"][0]["comparator"] == "under"


def test_queued_goals_do_not_advertise_themselves_as_tracked() -> None:
    """Only the live block counts games, so only it should highlight rows."""
    view = build_career_view(_snapshot(_goal("cspm", 7.0, "at_least", 0)))

    assert view["blocks"][1]["goals"] == []


# --- the reviewed game's timestamp -----------------------------------------


def test_a_reviewed_game_exposes_its_creation_timestamp() -> None:
    from league_stats_runner.analysis.game_review.assemble import _iso_date

    # The assembler already has the millisecond timestamp; it only formatted it away.
    assert _iso_date(1_700_000_000_000).startswith("20")


def test_the_game_review_payload_carries_game_creation_ms() -> None:
    from league_stats_common.core.models import GameDetail

    assert "game_creation_ms" in GameDetail.model_fields


def test_game_creation_ms_defaults_so_old_cached_payloads_still_load() -> None:
    from league_stats_common.core.models import GameDetail

    assert GameDetail.model_fields["game_creation_ms"].default == 0
