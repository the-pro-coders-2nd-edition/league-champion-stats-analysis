"""Career snapshot → template props."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pytest

from league_stats.analysis.career.engine import CareerSnapshot, advance_career
from league_stats.analysis.career.models import BLOCK_SLOTS
from league_stats.analysis.career.tracks import TrackContext
from league_stats.infra.career_store import CareerStore, build_key
from league_stats.presentation.career import (
    _track_name,
    CAREER_RULES,
    build_career_view,
    empty_career_view,
    state_class,
)

KEY = build_key("p", "Viktor", "MIDDLE")


@dataclass(frozen=True)
class _Component:
    name: str
    score: float


COMPONENTS = [
    _Component("Laning", 10.0),
    _Component("Survival", 20.0),
    _Component("Economy", 30.0),
    _Component("Objectives", 80.0),
    _Component("Fight", 85.0),
    _Component("Vision", 90.0),
]


HOUR = 3_600_000


def _batch(games: int = 20, *, start: int = 0, cspm: float = 6.0) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "game_creation_ms": [(start + i) * HOUR for i in range(games)],
            "cspm": [cspm] * games,
            "deaths_pre20": [3.0] * games,
            "deaths_before_neutral_objective": [0.0] * games,
            "avg_unspent_gold": [1650.0] * games,
            "avg_unspent_gold_per_fight": [1420.0] * games,
            "avg_gold_at_death": [1180.0] * games,
            "damage_share": [0.20] * games,
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
            "first_item_min": [10.0] * games,
            "pct_advantaged_fights": [0.55] * games,
            "kp15": [0.6] * games,
            "tf_won_share": [0.55] * games,
            "ccpm": [8.0] * games,
            "vspm": [0.8] * games,
            "vspm10": [0.9] * games,
            "wards_killed": [3.0] * games,
            "wards_placed": [11.0] * games,
            "avg_wards_before_objective": [1.5] * games,
            "objective_trade_success_rate": [0.6] * games,
            "unproductive_absence_rate": [0.1] * games,
            "towers_taken": [2.0] * games,
            "first_recall_min": [5.0] * games,
            "under_enemy_tower_laning_deaths": [0.0] * games,
            "gank_deaths_laning": [0.0] * games,
        }
    )


def _ctx(matches: pd.DataFrame | None = None) -> TrackContext:
    return TrackContext(
        matches_df=_batch() if matches is None else matches,
        objectives_df=pd.DataFrame({"present": [1, 0, 0, 0, 1, 0]}),
        role="MIDDLE",
        peer_p75={"cspm": 7.5, "damage_share": 0.29},
    )


@pytest.fixture()
def view(tmp_path: Path) -> dict:
    with CareerStore(tmp_path / "career.sqlite") as store:
        snapshot = advance_career(store, KEY, _ctx(), COMPONENTS)
    return build_career_view(snapshot)


def test_state_class_slugifies() -> None:
    assert state_class("At risk") == "at-risk"
    assert state_class("In progress") == "in-progress"
    assert state_class("Cleared") == "cleared"


def test_empty_view_still_carries_rules_and_legend() -> None:
    empty = empty_career_view()
    assert empty["has_career"] is False
    assert empty["blocks"] == []
    assert empty["widget"] == []
    assert empty["congrats"] is None
    assert len(empty["rules"]) == len(CAREER_RULES)
    assert [row["name"] for row in empty["legend"]] == [
        "Locked",
        "In progress",
        "At risk",
        "Cleared",
        "Revoked",
    ]


def test_empty_view_is_not_shared_state() -> None:
    first = empty_career_view()
    first["blocks"].append("mutated")
    assert empty_career_view()["blocks"] == []


def test_view_has_one_live_block_and_the_rest_locked(view: dict) -> None:
    assert view["has_career"] is True
    assert len(view["blocks"]) == BLOCK_SLOTS
    assert [block["is_active"] for block in view["blocks"]] == [True] + [False] * (BLOCK_SLOTS - 1)
    assert view["blocks"][0]["opacity"] == "1"
    assert view["blocks"][1]["opacity"] == "0.6"
    assert view["blocks"][0]["position"] == "Block 1"
    assert view["blocks"][-1]["position"] == f"Block {BLOCK_SLOTS}"


def test_live_block_carries_nodes_and_locked_blocks_carry_steps(view: dict) -> None:
    live, second = view["blocks"][0], view["blocks"][1]
    assert len(live["goals"]) == 3
    assert live["steps"] == []
    assert second["goals"] == []
    assert len(second["steps"]) == 3
    assert all(isinstance(step, str) and step for step in second["steps"])


def test_live_block_state_label_and_tone(view: dict) -> None:
    live = view["blocks"][0]
    assert live["state_label"] == "Live · 0 of 3 cleared"
    assert live["tone"] == "warn"
    assert live["unlock"] == ""


def test_locked_blocks_explain_their_unlock(view: dict) -> None:
    assert view["blocks"][1]["unlock"] == "Opens when Lane and early game is complete."
    assert view["blocks"][1]["state_label"] == "Locked"


def test_node_props_are_template_ready(view: dict) -> None:
    first, second, third = view["blocks"][0]["goals"]
    assert first["state"] == "In progress"
    assert first["state_class"] == "in-progress"
    assert first["tone"] == "warn"
    assert first["pct"] == 0
    assert first["mark"] == ""
    assert first["count"] == "0 of 20"
    assert first["need"] == 15
    assert first["hold"] == 11
    assert first["last"] is False
    # All three goals in the live block count in parallel; none is locked behind
    # the one above it.
    assert second["state"] == "In progress"
    assert second["count"] == "0 of 20"
    assert third["state"] == "In progress"
    assert third["last"] is True


def test_widget_notes_name_the_live_track(view: dict) -> None:
    assert len(view["widget"]) == 3
    assert view["widget"][0]["note"] == "Lane and early game · 0 of 20"
    assert view["widget"][1]["note"] == "Lane and early game · 0 of 20"


def test_cleared_goals_get_a_check_mark(tmp_path: Path) -> None:
    history = _batch(20, start=0, cspm=6.0)
    with CareerStore(tmp_path / "career.sqlite") as store:
        advance_career(store, KEY, _ctx(history), COMPONENTS)
        # A block's goals are three different metrics from its category bank, so
        # satisfy the first one specifically rather than nudging a named column.
        first = [goal for goal in store.load_goals(KEY) if goal.slot == 0][0].rung
        improved = pd.concat([history, _batch(20, start=20)], ignore_index=True)
        improved.loc[20:, first.column] = (
            first.target * 2 + 1 if first.comparator == "at_least" else 0.0
        )
        snapshot = advance_career(store, KEY, _ctx(improved), COMPONENTS)
    view = build_career_view(snapshot)

    first = view["blocks"][0]["goals"][0]
    assert first["state"] == "Cleared"
    assert first["mark"] == "✓"
    assert first["tone"] == "good"
    assert first["pct"] == 100
    # Some goals in a category block can already be satisfied by the baseline
    # games, so count what actually cleared rather than assuming exactly one.
    cleared_count = sum(
        1 for goal in view["blocks"][0]["goals"] if goal["state"] in {"Cleared", "At risk"}
    )
    assert view["blocks"][0]["state_label"] == f"Live · {cleared_count} of 3 cleared"


def test_congrats_banner_names_the_retired_and_next_block(tmp_path: Path) -> None:
    history = _batch(20, start=0, cspm=6.0)
    with CareerStore(tmp_path / "career.sqlite") as store:
        advance_career(store, KEY, _ctx(history), COMPONENTS)
        live = [goal for goal in store.load_goals(KEY) if goal.slot == 0]
        queued_name = _track_name(
            [goal for goal in store.load_goals(KEY) if goal.slot == 1][0].track_key
        )
        cleared = pd.concat([history, _batch(20, start=20)], ignore_index=True)
        for goal in live:
            rung = goal.rung
            cleared.loc[20:, rung.column] = (
                rung.target * 2 + 1 if rung.comparator == "at_least" else 0.0
            )
        snapshot = advance_career(store, KEY, _ctx(cleared), COMPONENTS)
    view = build_career_view(snapshot)

    assert view["congrats"] is not None
    assert view["congrats"]["title"] == "Lane and early game complete"
    assert f"{queued_name} moves left" in view["congrats"]["body"]


def test_no_blocks_falls_back_to_the_empty_view() -> None:
    assert build_career_view(CareerSnapshot())["has_career"] is False
