"""Slice-bundle caching: identical output, and honest invalidation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from league_stats_common.core.models import RankedEntry
from league_stats_runner.infra.derived import KIND_SLICE, DerivedStore
from league_stats_runner.pipeline import bundles as bundles_module
from league_stats_runner.pipeline.orchestrator import build_report_views
from tests.test_reports import _config, _make_records, _peer

RANKED = RankedEntry(tier="GOLD", rank="II", league_points=45, wins=80, losses=75)


def _views(tmp_path: Path, records=None):
    config = _config(tmp_path)
    recs = records if records is not None else _make_records()
    graphs = config.run_graphs_dir
    graphs.mkdir(parents=True, exist_ok=True)
    return build_report_views(
        config, recs, graphs, peer_comparison=_peer(recs)
    ), config, recs


def _canonical(views: dict) -> str:
    return json.dumps(views, sort_keys=True, default=str)


def test_warm_views_match_cold_views(tmp_path: Path) -> None:
    (cold, _, _), config, recs = _views(tmp_path)
    warm, _, _ = build_report_views(
        config, recs, config.run_graphs_dir, peer_comparison=_peer(recs)
    ), None, None

    assert _canonical(warm[0]) == _canonical(cold)


def test_second_run_builds_no_bundles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (_, _, _), config, recs = _views(tmp_path)

    calls = {"n": 0}
    original = bundles_module.build_window_bundle

    def counted(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr("league_stats_runner.pipeline.orchestrator.build_window_bundle", counted)
    build_report_views(config, recs, config.run_graphs_dir, peer_comparison=_peer(recs))

    assert calls["n"] == 0, "an unchanged record set should be fully cached"


def test_a_new_game_invalidates_every_slice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (_, _, _), config, recs = _views(tmp_path)

    # The shared win predictor is trained on all records and feeds every slice's
    # feature-importance figure, so a new game must invalidate all of them.
    extra = recs[0].model_copy(
        update={"match_id": "EUW1_new", "game_creation_ms": 1_900_000_000_000}
    )
    grown = [extra, *recs]

    calls = {"n": 0}
    original = bundles_module.build_window_bundle

    def counted(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr("league_stats_runner.pipeline.orchestrator.build_window_bundle", counted)
    build_report_views(config, grown, config.run_graphs_dir, peer_comparison=_peer(grown))

    assert calls["n"] == 9, "3 queues x 3 windows all depend on the shared model"


def test_a_code_change_invalidates_slices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (_, _, _), config, recs = _views(tmp_path)

    monkeypatch.setattr(
        "league_stats_runner.infra.derived.code_version", lambda kind: "0badc0de0badc0de"
    )
    calls = {"n": 0}
    original = bundles_module.build_window_bundle

    def counted(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr("league_stats_runner.pipeline.orchestrator.build_window_bundle", counted)
    build_report_views(config, recs, config.run_graphs_dir, peer_comparison=_peer(recs))

    assert calls["n"] > 0


def test_peer_data_is_restored_from_cache(tmp_path: Path) -> None:
    (cold, cold_peers, _), config, recs = _views(tmp_path)
    _, warm_peers, _ = build_report_views(
        config, recs, config.run_graphs_dir, peer_comparison=_peer(recs)
    )

    cold_solo = cold_peers["solo"]["all"]
    warm_solo = warm_peers["solo"]["all"]
    assert (cold_solo is None) == (warm_solo is None)
    if cold_solo is not None:
        assert warm_solo.model_dump(mode="json") == cold_solo.model_dump(mode="json")
    assert warm_peers["flex"]["all"] is None


def test_slices_are_actually_written_to_the_store(tmp_path: Path) -> None:
    (_, _, _), config, _ = _views(tmp_path)

    with DerivedStore(config.derived_db_path) as derived:
        count = derived._conn.execute(
            "SELECT COUNT(*) FROM derived WHERE kind = ?", (KIND_SLICE,)
        ).fetchone()[0]

    assert count == 9
