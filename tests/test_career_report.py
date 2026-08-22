"""Career mode surfaced through the pipeline's report.json payload."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from league_stats_runner.analysis.career.models import BLOCK_SLOTS
from league_stats_peers.analysis.peer import build_comparisons
from league_stats_common.core.models import MatchRecord, PeerComparisonResult, RankedEntry
from league_stats_runner.pipeline.orchestrator import run_analysis
from tests.fixtures import FAKE_ITEMS, MY_PUUID, make_match, make_timeline
from tests.test_reports import _config
from league_stats_runner.ingest.parser import ItemCatalog, MatchParser

PEER_METRICS = {
    "win": 0.5,
    "kda": 2.4,
    "dpm": 640.0,
    "cspm": 7.0,
    "deaths": 5.0,
    "vspm": 1.0,
    "control_wards": 2.0,
    "kill_participation": 0.6,
    "damage_share": 0.2,
}


def _records(count: int = 25) -> list[MatchRecord]:
    base = MatchParser(ItemCatalog(FAKE_ITEMS)).parse(make_match(), make_timeline(), MY_PUUID)
    records = [
        base.model_copy(
            deep=True,
            update={
                "match_id": f"EUW1_{index}",
                "win": index % 2 == 0,
                "game_creation_ms": 1_700_000_000_000 + index * 3_600_000,
            },
        )
        for index in range(count)
    ]
    return sorted(records, key=lambda record: record.game_creation_ms, reverse=True)


def _peer_with_percentiles(records: list[MatchRecord]) -> PeerComparisonResult:
    user_avgs = pd.DataFrame([r.to_row() for r in records]).mean(numeric_only=True).to_dict()
    p75 = {key: value * 1.4 for key, value in PEER_METRICS.items()}
    return PeerComparisonResult(
        rank_label="GOLD II",
        tier="GOLD",
        source="test benchmark",
        peer_games=0,
        peer_players=0,
        comparisons=build_comparisons(user_avgs, PEER_METRICS, peer_p75=p75),
    )


def _career_payload(tmp_path: Path) -> dict:
    records = _records()
    config = _config(tmp_path)
    run_analysis(
        config,
        records,
        peer_comparison=_peer_with_percentiles(records),
        ranked=RankedEntry(tier="GOLD", rank="II", league_points=45, wins=80, losses=75),
    )
    payload = json.loads((config.report_dir / "report.json").read_text(encoding="utf-8"))
    return _all_ranked_career(payload)


def _all_ranked_career(payload: dict) -> dict:
    """The ladder as the reader sees it. Every queue view carries the same one."""
    windows = payload["report_views"]["all"]["windows"]
    return windows[next(iter(windows))]["career"]


def test_report_json_has_a_career_block(tmp_path: Path) -> None:
    career = _career_payload(tmp_path)
    assert career["has_career"] is True
    assert len(career["blocks"]) == BLOCK_SLOTS


def test_career_rules_and_legend_are_present(tmp_path: Path) -> None:
    career = _career_payload(tmp_path)
    # Derived from the constants rather than the literal copy: the wording of this
    # panel is pinned in tests/test_career_rules_copy.py.
    from league_stats_runner.analysis.career.steps import ANCHOR_QUANTILE, BASELINE_GAMES

    anchor = f"P{int(ANCHOR_QUANTILE * 100)} of your last {BASELINE_GAMES} games"
    assert any(anchor in rule["value"] for rule in career["rules"])
    legend_states = [entry["name"] for entry in career["legend"]]
    assert legend_states == ["Locked", "In progress", "At risk", "Cleared", "Revoked"]


def test_first_block_is_active_with_goal_nodes(tmp_path: Path) -> None:
    career = _career_payload(tmp_path)
    live_block = career["blocks"][0]
    assert live_block["is_active"] is True
    assert live_block["is_locked"] is False
    assert live_block["position"] == "Block 1"
    assert live_block["state_label"].startswith("Live · ")
    assert len(live_block["goals"]) > 0


def test_remaining_blocks_are_locked_with_steps(tmp_path: Path) -> None:
    career = _career_payload(tmp_path)
    for block in career["blocks"][1:]:
        assert block["is_active"] is False
        assert block["is_locked"] is True
        assert block["state_label"] == "Locked"
        assert block["unlock"].endswith("is complete.")
        assert len(block["steps"]) > 0


def test_widget_mirrors_the_live_block_goals(tmp_path: Path) -> None:
    career = _career_payload(tmp_path)
    live_block = career["blocks"][0]
    assert len(career["widget"]) == len(live_block["goals"])
    for item in career["widget"]:
        assert item["note"].startswith(f"{live_block['name']} · ")


def test_career_styles_and_tokens_are_published() -> None:
    report_css = (
        Path(__file__).resolve().parent.parent / "frontend/src/styles/report.css"
    ).read_text(encoding="utf-8")
    components_css = (
        Path(__file__).resolve().parent.parent / "frontend/src/styles/components.css"
    ).read_text(encoding="utf-8")
    assert "--cat-career:" in report_css
    assert ".career-blocks" in report_css
    assert "career-ring--at-risk" in components_css


def test_every_queue_view_renders_the_same_ladder(tmp_path: Path) -> None:
    """Career is one ladder over all ranked games, so Solo/Duo has none of its own."""
    records = _records()
    config = _config(tmp_path)
    run_analysis(
        config,
        records,
        peer_comparison=_peer_with_percentiles(records),
        ranked=RankedEntry(tier="GOLD", rank="II", league_points=45, wins=80, losses=75),
    )
    payload = json.loads((config.report_dir / "report.json").read_text(encoding="utf-8"))

    for queue_key in ("solo", "flex", "all"):
        windows = payload["report_views"][queue_key]["windows"]
        for window in windows.values():
            assert window["career"]["has_career"] is True
            assert window["career"]["tracks_all_ranked"] is True
