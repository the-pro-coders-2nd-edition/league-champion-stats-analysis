"""A goal's "why does this exist" text has to survive the career database.

``Rung.why`` was added with a default of ``""`` and wired through to a
``MetricTooltip`` gated on ``{#if why}``, but ``career_goals`` had no column for
it. Every goal the report renders comes back from ``CareerStore.load_goals``, so
the value was always the default and the tooltip never rendered for anyone, on any
report, regardless of regeneration.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from league_stats.analysis.career.engine import advance_career
from league_stats.analysis.career.models import CLEAR_BAR, Rung
from league_stats.analysis.career.tracks import TrackContext
from league_stats.infra.career_store import CareerStore, build_key
from league_stats.presentation.career import build_career_view

KEY = build_key("p", "Aatrox", "TOP")
HOUR = 3_600_000

ROW = {
    "cspm": 6.0, "vspm": 1.0, "damage_share": 0.24, "deaths_pre20": 3.0,
    "deaths_pre14": 1.0, "deaths_before_neutral_objective": 0.4,
    "objectives_present_rate": 0.6, "control_wards": 1.5, "tf_participation": 0.7,
    "first_item_min": 11.0, "gd10": -100.0, "gd15": -150.0, "gd20": -200.0,
    "xpd10": -80.0, "greed_deaths": 0.6, "solo_deaths": 0.8,
    "outnumbered_deaths": 0.7, "shutdown_given": 300.0, "time_dead_s": 90.0,
    "gank_deaths_laning": 0.5, "under_enemy_tower_laning_deaths": 0.4,
    "pct_advantaged_fights": 0.45, "objective_trade_success_rate": 0.45,
    "unproductive_absence_rate": 0.2, "towers_taken": 2.0, "vspm10": 0.8,
    "wards_killed": 3.0, "avg_wards_before_objective": 1.2,
    "avg_unspent_gold": 700.0, "avg_gold_at_death": 700.0, "first_recall_min": 5.5,
    "cs10": 66.0, "wards_placed": 12.0, "kp15": 0.55, "tf_won_share": 0.5,
    "ccpm": 8.0, "hpm": 220.0,
}


class _Component:
    def __init__(self, name: str, score: float) -> None:
        self.name, self.score = name, score


COMPONENTS = [
    _Component("Survival", 10.0), _Component("Laning", 80.0),
    _Component("Vision", 70.0), _Component("Objectives", 75.0),
    _Component("Fight", 78.0), _Component("Economy", 79.0),
]


def _ctx() -> TrackContext:
    frame = pd.DataFrame({col: [val] * 30 for col, val in ROW.items()})
    frame["game_creation_ms"] = [i * HOUR for i in range(30)]
    return TrackContext(
        matches_df=frame,
        objectives_df=pd.DataFrame({"present": [1] * 8}),
        role="MIDDLE",
        peer_p75={"cspm": 7.5, "deaths_pre20": 3.0},
    )


@pytest.fixture()
def store(tmp_path: Path):
    with CareerStore(tmp_path / "career.sqlite") as opened:
        yield opened


def test_a_why_survives_a_save_and_reload(store: CareerStore) -> None:
    """The assertion that would have caught the missing column."""
    rungs = (
        Rung(text="goal one", column="cspm", comparator="at_least", target=7.0,
             need=CLEAR_BAR, why="Because your CS is behind."),
    )
    store.write_slot(KEY, 0, "laning", rungs, ["In progress"])

    reloaded = store.load_goals(KEY)

    assert reloaded[0].rung.why == "Because your CS is behind."


def test_a_goal_written_without_a_why_reloads_as_empty(store: CareerStore) -> None:
    rungs = (
        Rung(text="goal one", column="cspm", comparator="at_least", target=7.0,
             need=CLEAR_BAR),
    )
    store.write_slot(KEY, 0, "laning", rungs, ["In progress"])

    assert store.load_goals(KEY)[0].rung.why == ""


def test_a_real_ladder_reaches_the_view_with_its_why_text(store: CareerStore) -> None:
    """End to end: the bank writes a why, the store keeps it, the view exposes it."""
    snapshot = advance_career(store, KEY, _ctx(), COMPONENTS)
    view = build_career_view(snapshot)

    whys = [goal["why"] for goal in view["blocks"][0]["goals"]]
    assert whys and all(whys), f"live block goals have no why text: {whys}"


def test_the_overview_widget_carries_the_why_too(store: CareerStore) -> None:
    snapshot = advance_career(store, KEY, _ctx(), COMPONENTS)
    view = build_career_view(snapshot)

    assert all(item["why"] for item in view["widget"])


def test_a_ladder_written_before_the_column_existed_still_loads(tmp_path: Path) -> None:
    """Older rows have no why; they must read as empty, not raise."""
    path = tmp_path / "career.sqlite"
    with CareerStore(path) as first:
        advance_career(first, KEY, _ctx(), COMPONENTS)

    with sqlite3.connect(path) as raw:
        raw.execute("ALTER TABLE career_goals DROP COLUMN why")

    with CareerStore(path) as reopened:
        goals = reopened.load_goals(KEY)
        assert goals
        assert all(goal.rung.why == "" for goal in goals)
