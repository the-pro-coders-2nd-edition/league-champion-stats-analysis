"""Career mode rendering inside the generated report."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from league_stats.analysis.career.models import BLOCK_SLOTS
from league_stats.analysis.peer import build_comparisons
from league_stats.core.models import MatchRecord, PeerComparisonResult, RankedEntry
from league_stats.pipeline.orchestrator import run_analysis
from tests.fixtures import FAKE_ITEMS, MY_PUUID, make_match, make_timeline
from tests.test_reports import _config
from league_stats.ingest.parser import ItemCatalog, MatchParser

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


def _render(tmp_path: Path) -> str:
    records = _records()
    path = run_analysis(
        _config(tmp_path),
        records,
        peer_comparison=_peer_with_percentiles(records),
        ranked=RankedEntry(tier="GOLD", rank="II", league_points=45, wins=80, losses=75),
    )
    return Path(path).read_text(encoding="utf-8")


def test_report_has_a_career_tab_and_panel(tmp_path: Path) -> None:
    html = _render(tmp_path)
    assert 'id="tab-career"' in html
    assert 'data-category="career"' in html
    assert 'id="category-career"' in html
    assert ">Career</button>" in html


def test_career_is_the_second_tab_and_panel(tmp_path: Path) -> None:
    html = _render(tmp_path)
    assert html.index('id="tab-career"') < html.index('id="tab-performance"')
    assert html.index('id="category-career"') < html.index('id="category-performance"')
    assert html.index('id="tab-summary"') < html.index('id="tab-career"')


def test_report_renders_the_career_rules_and_legend(tmp_path: Path) -> None:
    html = _render(tmp_path)
    assert "Career mode" in html
    assert "Two blocks of three goals; only the left block is live." in html
    assert "The five states a goal can be in" in html
    for state in ("Locked", "In progress", "At risk", "Cleared", "Revoked"):
        assert f'career-legend-name--{state.lower().replace(" ", "-")}' in html
    assert "your p50 toward peer p75" in html


def test_report_renders_a_live_block_with_goal_nodes(tmp_path: Path) -> None:
    html = _render(tmp_path)
    assert "career-blocks" in html
    assert "Block 1" in html
    assert "career-ring career-ring--in-progress" in html
    assert "in 15 of 20 games" in html
    assert "Live · " in html


def test_report_renders_locked_blocks_as_steps(tmp_path: Path) -> None:
    html = _render(tmp_path)
    assert "career-step-text" in html
    assert " is complete." in html


def test_report_renders_exactly_the_configured_block_count(tmp_path: Path) -> None:
    import re

    html = _render(tmp_path)
    panel = html[html.index('id="category-career"') : html.index('id="category-performance"')]
    assert len(re.findall(r'class="career-block"', panel)) == BLOCK_SLOTS


def test_summary_tab_shows_the_live_block_widget(tmp_path: Path) -> None:
    html = _render(tmp_path)
    assert 'id="career-widget"' in html
    assert "career-node--compact" in html
    assert ">Live block</span>" in html
    assert 'id="career-widget-link"' in html


def test_career_styles_and_tokens_are_published(tmp_path: Path) -> None:
    _render(tmp_path)
    css = (
        Path(__file__).resolve().parent.parent
        / "src/league_stats/presentation/templates/static/report.css"
    ).read_text(encoding="utf-8")
    components = (
        Path(__file__).resolve().parent.parent
        / "src/league_stats/presentation/templates/static/components.css"
    ).read_text(encoding="utf-8")
    assert "--cat-career:" in css
    assert ".career-blocks" in css
    assert "career-ring--at-risk" in components
