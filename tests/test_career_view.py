"""Career snapshot → template props."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pytest

from league_stats.analysis.career.engine import CareerSnapshot, advance_career
from league_stats.analysis.career.tracks import TrackContext
from league_stats.infra.career_store import CareerStore, build_key
from league_stats.presentation.career import (
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


def _matches(cspm: float = 6.0) -> pd.DataFrame:
    games = 20
    return pd.DataFrame(
        {
            "game_creation_ms": list(range(games)),
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
        }
    )


def _ctx(cspm: float = 6.0) -> TrackContext:
    return TrackContext(
        matches_df=_matches(cspm),
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


def test_view_has_one_live_block_and_two_locked(view: dict) -> None:
    assert view["has_career"] is True
    assert [block["is_active"] for block in view["blocks"]] == [True, False, False]
    assert view["blocks"][0]["opacity"] == "1"
    assert view["blocks"][1]["opacity"] == "0.6"
    assert view["blocks"][0]["position"] == "Block 1"
    assert view["blocks"][2]["position"] == "Block 3"


def test_live_block_carries_nodes_and_locked_blocks_carry_steps(view: dict) -> None:
    live, second, _ = view["blocks"]
    assert len(live["items"]) == 3
    assert live["steps"] == []
    assert second["items"] == []
    assert len(second["steps"]) == 3
    assert all(isinstance(step, str) and step for step in second["steps"])


def test_live_block_state_label_and_tone(view: dict) -> None:
    live = view["blocks"][0]
    assert live["state_label"] == "Live · 0 of 3 cleared"
    assert live["tone"] == "warn"
    assert live["unlock"] == ""


def test_locked_blocks_explain_their_unlock(view: dict) -> None:
    assert view["blocks"][1]["unlock"] == "Opens when Laning income is complete."
    assert view["blocks"][2]["unlock"] == "Opens when Death discipline is complete."
    assert view["blocks"][1]["state_label"] == "Locked"


def test_node_props_are_template_ready(view: dict) -> None:
    first, second, third = view["blocks"][0]["items"]
    assert first["state"] == "In progress"
    assert first["state_class"] == "in-progress"
    assert first["tone"] == "warn"
    assert first["pct"] == 0
    assert first["mark"] == ""
    assert first["count"] == "0 of 20"
    assert first["need"] == 15
    assert first["hold"] == 11
    assert first["last"] is False
    assert second["state"] == "Locked"
    assert second["count"] == "blocked"
    assert third["last"] is True


def test_widget_notes_name_the_live_track(view: dict) -> None:
    assert len(view["widget"]) == 3
    assert view["widget"][0]["note"] == "Laning income · 0 of 20"
    assert view["widget"][1]["note"] == "Laning income · blocked"


def test_cleared_goals_get_a_check_mark(tmp_path: Path) -> None:
    with CareerStore(tmp_path / "career.sqlite") as store:
        advance_career(store, KEY, _ctx(6.0), COMPONENTS)
        snapshot = advance_career(store, KEY, _ctx(6.6), COMPONENTS)
    view = build_career_view(snapshot)

    first = view["blocks"][0]["items"][0]
    assert first["state"] == "Cleared"
    assert first["mark"] == "✓"
    assert first["tone"] == "good"
    assert first["pct"] == 100
    assert view["blocks"][0]["state_label"] == "Live · 1 of 3 cleared"


def test_congrats_banner_names_the_retired_and_next_block(tmp_path: Path) -> None:
    with CareerStore(tmp_path / "career.sqlite") as store:
        advance_career(store, KEY, _ctx(6.0), COMPONENTS)
        snapshot = advance_career(store, KEY, _ctx(9.0), COMPONENTS)
    view = build_career_view(snapshot)

    assert view["congrats"] is not None
    assert view["congrats"]["title"] == "Laning income complete"
    assert "Death discipline moves left" in view["congrats"]["body"]


def test_no_blocks_falls_back_to_the_empty_view() -> None:
    assert build_career_view(CareerSnapshot())["has_career"] is False
