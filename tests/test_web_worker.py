"""Tests for the analysis worker: two-stage execution and failure handling."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from league_stats.core.config import WebConfig
from league_stats.ingest.parser import BuildPool
from league_stats.pipeline.fetch import FetchResult
from league_stats.pipeline.orchestrator import BuildBatch, NoEligibleBuildsError
from league_stats.web import jobs, worker
from league_stats.web.jobs import JobStore


@pytest.fixture()
def store(tmp_path: Path) -> JobStore:
    js = JobStore(tmp_path / "app.sqlite")
    js.upsert_player(slug="test_euw", riot_id="Test", tagline="EUW", region="euw1")
    yield js
    js.close()


@pytest.fixture()
def web_config(tmp_path: Path) -> WebConfig:
    return WebConfig(
        app_db_path=tmp_path / "app.sqlite", output_dir=tmp_path / "output"
    )


def _claimed_job(store: JobStore) -> dict[str, Any]:
    store.enqueue(
        kind=jobs.JOB_KIND_ANALYZE,
        riot_id="Test",
        tagline="EUW",
        region="euw1",
        player_slug="test_euw",
    )
    job = store.claim_next()
    assert job is not None
    return job


def _fake_services() -> SimpleNamespace:
    return SimpleNamespace(
        config=SimpleNamespace(),
        store=SimpleNamespace(close=lambda: None),
        http_cache=SimpleNamespace(close=lambda: None),
    )


def _fake_batch() -> BuildBatch:
    return BuildBatch(
        pools=[BuildPool(champion="Viktor", role="MIDDLE", games=25)],
        records=[],
        manifest_builds=[],
        primary_puuid="puuid",
    )


def _patch_pipeline(monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> list[str]:
    """Stub out every pipeline call the worker makes; record the call order."""
    calls: list[str] = []

    defaults: dict[str, Any] = {
        "_build_job_services": lambda job, cfg, reporter: _fake_services(),
        "fetch_matches": lambda services: calls.append("fetch")
        or FetchResult(contexts=["ctx"], new_match_ids=frozenset()),
        "prepare_builds": lambda services, contexts: calls.append("prepare") or _fake_batch(),
        "group_records": lambda records, champion, role: ["record"],
        "resolve_ranked": lambda services, batch, records: calls.append("ranked") or None,
        "should_skip_unchanged_build": lambda config, pool, records, new_ids: False,
        "analyze_build": (
            lambda services, batch, pool, *, ranked, peer_comparison: calls.append(
                f"analyze(peer={peer_comparison is not None})"
            )
            or Path("report.html")
        ),
        "build_peer_for_pool": (
            lambda services, batch, pool, ranked: calls.append("peer") or object()
        ),
    }
    defaults.update(overrides)
    for name, fn in defaults.items():
        monkeypatch.setattr(worker, name, fn)
    return calls


def test_execute_job_two_stage_happy_path(
    store: JobStore, web_config: WebConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = _claimed_job(store)
    calls = _patch_pipeline(monkeypatch)

    worker.execute_job(job, store, web_config)

    final = store.get(int(job["id"]))
    assert final["state"] == jobs.DONE
    assert final["error"] == ""
    # Stage A renders without peer, stage B builds peer then re-renders.
    assert calls == ["fetch", "prepare", "ranked", "analyze(peer=False)", "peer", "analyze(peer=True)"]

    player = store.get_player("test_euw")
    assert player["base_completed_at"] is not None
    assert player["peer_completed_at"] is not None
    assert player["peer_failed"] == 0


def test_execute_job_skips_unchanged_builds(
    store: JobStore, web_config: WebConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refresh with no new games keeps existing reports and skips peer work."""
    job = _claimed_job(store)
    calls = _patch_pipeline(
        monkeypatch,
        should_skip_unchanged_build=lambda *args, **kwargs: True,
    )

    worker.execute_job(job, store, web_config)

    final = store.get(int(job["id"]))
    assert final["state"] == jobs.DONE
    assert final["error"] == ""
    assert calls == ["fetch", "prepare"]
    assert "analyze(peer=False)" not in calls
    assert "peer" not in calls

    player = store.get_player("test_euw")
    assert player["base_completed_at"] is not None
    assert player["peer_completed_at"] is not None


def test_execute_job_peer_failure_is_soft(
    store: JobStore, web_config: WebConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = _claimed_job(store)

    def boom(services: Any, batch: Any, pool: Any, ranked: Any) -> Any:
        raise RuntimeError("riot exploded")

    _patch_pipeline(monkeypatch, build_peer_for_pool=boom)

    worker.execute_job(job, store, web_config)

    final = store.get(int(job["id"]))
    assert final["state"] == jobs.DONE
    assert "Peer analysis failed" in final["error"]

    player = store.get_player("test_euw")
    assert player["base_completed_at"] is not None
    assert player["peer_completed_at"] is None
    assert player["peer_failed"] == 1


def test_execute_job_fetch_failure_marks_failed(
    store: JobStore, web_config: WebConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = _claimed_job(store)

    def boom(services: Any) -> Any:
        raise RuntimeError("network down")

    _patch_pipeline(monkeypatch, fetch_matches=boom)

    worker.execute_job(job, store, web_config)

    final = store.get(int(job["id"]))
    assert final["state"] == jobs.FAILED
    assert "network down" in final["error"]
    assert store.get_player("test_euw")["base_completed_at"] is None


def test_execute_job_passes_group_players_to_config(
    store: JobStore, web_config: WebConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    players = [
        {"riot_id": "Alice", "tagline": "EUW"},
        {"riot_id": "Bob", "tagline": "EUW"},
    ]
    store.upsert_player(
        slug="alice_euw__bob_euw",
        riot_id="Alice",
        tagline="EUW",
        region="euw1",
        players=players,
    )
    store.enqueue(
        kind=jobs.JOB_KIND_ANALYZE,
        riot_id="Alice",
        tagline="EUW",
        region="euw1",
        player_slug="alice_euw__bob_euw",
        players=players,
    )
    job = store.claim_next()
    assert job is not None
    captured: dict[str, Any] = {}

    def capture_services(claimed: dict[str, Any], cfg: WebConfig, reporter: Any) -> Any:
        captured["players"] = claimed.get("players")
        return _fake_services()

    _patch_pipeline(monkeypatch, _build_job_services=capture_services)
    worker.execute_job(job, store, web_config)
    assert captured["players"] == players
    assert store.get(int(job["id"]))["state"] == jobs.DONE


def test_execute_job_no_builds_marks_failed(
    store: JobStore, web_config: WebConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = _claimed_job(store)

    def no_builds(services: Any, contexts: Any) -> Any:
        raise NoEligibleBuildsError("No champion+lane builds with at least 20 ranked games found.")

    _patch_pipeline(monkeypatch, prepare_builds=no_builds)

    worker.execute_job(job, store, web_config)

    final = store.get(int(job["id"]))
    assert final["state"] == jobs.FAILED
    assert "20 ranked games" in final["error"]
