"""Tests for the report game-window toggle."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from league_stats_peers.analysis.peer import build_comparisons, peer_comparison_for_window
from league_stats_common.core.champions import champion_slug
from league_stats_common.core.config import DEFAULT_GAME_WINDOW
from league_stats_common.infra.report_store import open_report_store
from league_stats_runner.pipeline.bundles import default_game_window_key as _default_game_window_key
from league_stats_runner.pipeline.orchestrator import run_analysis
from league_stats_common.core.models import MatchRecord, PeerComparisonResult, RankedEntry
from tests.fixtures import FAKE_ITEMS, MY_PUUID, make_match, make_timeline
from tests.test_reports import _config, _peer
from league_stats_runner.ingest.parser import ItemCatalog, MatchParser


def _read_report(config) -> dict:
    build_slug = champion_slug(config.champion, config.role)
    with open_report_store() as store:
        return store.get_report(config.reports_group_slug, build_slug)


def _view_slice(config, queue_key: str, window_key: str) -> dict:
    build_slug = champion_slug(config.champion, config.role)
    with open_report_store() as store:
        return store.get_view_slice(config.reports_group_slug, build_slug, queue_key, window_key)


def _make_records(n: int, *, recent_wins: bool = False) -> list[MatchRecord]:
    base = MatchParser(ItemCatalog(FAKE_ITEMS)).parse(make_match(), make_timeline(), MY_PUUID)
    records: list[MatchRecord] = []
    for index in range(n):
        win = index % 2 == 0
        if recent_wins and index >= n - 10:
            win = True
        records.append(
            base.model_copy(
                deep=True,
                update={
                    "match_id": f"EUW1_{index}",
                    "win": win,
                    "game_creation_ms": 1_700_000_000_000 + index * 3_600_000,
                },
            )
        )
    return sorted(records, key=lambda record: record.game_creation_ms, reverse=True)


def test_default_game_window_key_prefers_50(tmp_path: Path) -> None:
    """Default window is 50 when enough games exist."""
    assert _default_game_window_key(120) == str(DEFAULT_GAME_WINDOW)
    assert _default_game_window_key(30) == "all"


def test_peer_comparison_for_window_updates_user_side() -> None:
    """Windowed peer comparison recomputes only the player averages."""
    records = _make_records(20)
    matches_df = pd.DataFrame([record.to_row() for record in records])
    base = _peer(records)
    windowed = peer_comparison_for_window(base, matches_df[:5], records[:5])
    assert windowed.comparisons
    assert windowed.rank_label == base.rank_label
    assert windowed.strengths != base.strengths or windowed.weaknesses != base.weaknesses


def test_report_contains_game_window_toggle(tmp_path: Path) -> None:
    """report.json carries the window toggle's default and all window snapshots."""
    config = _config(tmp_path)
    records = _make_records(25)
    peer = _peer(records)
    ranked = RankedEntry(tier="GOLD", rank="II", league_points=45, wins=80, losses=75)

    run_analysis(config, records, peer_comparison=peer, ranked=ranked)

    payload = _read_report(config)
    assert payload["game_window_default"] == "all"

    views = payload["view_manifest"]
    solo_windows = views["solo"]["windows"]
    assert set(solo_windows) == {"50", "100", "all"}
    assert _view_slice(config, "solo", "50")["total_games"] == 25
    assert _view_slice(config, "solo", "100")["total_games"] == 25
    assert _view_slice(config, "solo", "all")["total_games"] == 25


def test_default_window_active_when_enough_games(tmp_path: Path) -> None:
    """Last 50 is the initial view when at least 50 games exist."""
    config = _config(tmp_path)
    records = _make_records(120)
    peer = _peer(records)

    run_analysis(config, records, peer_comparison=peer, ranked=None)

    payload = _read_report(config)
    assert payload["game_window_default"] == str(DEFAULT_GAME_WINDOW)


def test_window_snapshots_change_winrate(tmp_path: Path) -> None:
    """Recent-window stats can differ from the full-history view."""
    config = _config(tmp_path)
    records = _make_records(60, recent_wins=True)
    peer = PeerComparisonResult(
        rank_label="GOLD II",
        tier="GOLD",
        source="test benchmark",
        peer_games=0,
        peer_players=0,
        comparisons=build_comparisons(
            {"win": 0.5},
            {"win": 0.5},
        ),
    )

    run_analysis(config, records, peer_comparison=peer, ranked=None)

    _read_report(config)
    solo_50 = _view_slice(config, "solo", "50")
    solo_all = _view_slice(config, "solo", "all")
    assert solo_50["overview"]["winrate"] > solo_all["overview"]["winrate"]
