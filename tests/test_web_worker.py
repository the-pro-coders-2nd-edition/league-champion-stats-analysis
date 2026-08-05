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
from league_stats.pipeline.services import PlayerContext
from league_stats.web import jobs, worker
from league_stats.web.jobs import JobStore


def _fake_context(
    *,
    riot_id: str = "Test",
    tagline: str = "EUW",
    puuid: str = "puuid",
    profile_icon_id: int | None = 42,
) -> PlayerContext:
    return PlayerContext(
        riot_id=riot_id,
        tagline=tagline,
        puuid=puuid,
        profile_icon_id=profile_icon_id,
    )


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
        "_build_job_services": lambda job, cfg, reporter, **kwargs: _fake_services(),
        "fetch_matches": lambda services: calls.append("fetch")
        or FetchResult(contexts=[_fake_context()], new_match_ids=frozenset()),
        "resolve_player_contexts": lambda services: calls.append("resolve")
        or [_fake_context()],
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


def _claimed_regenerate_job(store: JobStore) -> dict[str, Any]:
    store.enqueue(
        kind=jobs.JOB_KIND_REGENERATE,
        riot_id="Test",
        tagline="EUW",
        region="euw1",
        player_slug="test_euw",
    )
    job = store.claim_next()
    assert job is not None
    return job


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


def test_execute_regenerate_uses_cache_and_forces_reanalysis(
    store: JobStore, web_config: WebConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regenerate skips Riot download and re-analyses even with no new games."""
    job = _claimed_regenerate_job(store)
    seen_new_ids: list[Any] = []

    def track_skip(config: Any, pool: Any, records: Any, new_ids: Any) -> bool:
        seen_new_ids.append(new_ids)
        return False

    calls = _patch_pipeline(monkeypatch, should_skip_unchanged_build=track_skip)

    worker.execute_job(job, store, web_config)

    final = store.get(int(job["id"]))
    assert final["state"] == jobs.DONE
    assert final["error"] == ""
    assert calls == [
        "resolve",
        "prepare",
        "ranked",
        "analyze(peer=False)",
        "peer",
        "analyze(peer=True)",
    ]
    assert "fetch" not in calls
    assert seen_new_ids == [None, None]


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

    def capture_services(
        claimed: dict[str, Any], cfg: WebConfig, reporter: Any, **kwargs: Any
    ) -> Any:
        captured["players"] = claimed.get("players")
        return _fake_services()

    group_contexts = [
        _fake_context(riot_id="Alice", tagline="EUW", puuid="alice", profile_icon_id=1),
        _fake_context(riot_id="Bob", tagline="EUW", puuid="bob", profile_icon_id=2),
    ]
    _patch_pipeline(
        monkeypatch,
        _build_job_services=capture_services,
        fetch_matches=lambda services: FetchResult(
            contexts=group_contexts, new_match_ids=frozenset()
        ),
    )
    worker.execute_job(job, store, web_config)
    assert captured["players"] == players
    assert store.get(int(job["id"]))["state"] == jobs.DONE
    saved = store.get_player("alice_euw__bob_euw")
    assert saved is not None
    assert saved["players"] == [
        {"riot_id": "Alice", "tagline": "EUW", "profile_icon_id": 1},
        {"riot_id": "Bob", "tagline": "EUW", "profile_icon_id": 2},
    ]


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


def test_execute_job_honours_cancel_before_peer(
    store: JobStore, web_config: WebConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancel after stage A keeps the job cancelled and skips peer work."""
    job = _claimed_job(store)
    job_id = int(job["id"])

    def cancel_after_analyze(
        services: Any, batch: Any, pool: Any, *, ranked: Any, peer_comparison: Any
    ) -> Path:
        store.cancel(job_id)
        return Path("report.html")

    calls = _patch_pipeline(monkeypatch, analyze_build=cancel_after_analyze)

    worker.execute_job(job, store, web_config)

    final = store.get(job_id)
    assert final["state"] == jobs.CANCELLED
    assert "peer" not in calls
    assert store.get_player("test_euw")["peer_completed_at"] is None


def test_execute_job_cancel_during_fetch_does_not_mark_failed(
    store: JobStore, web_config: WebConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = _claimed_job(store)
    job_id = int(job["id"])

    def cancel_mid_fetch(services: Any) -> Any:
        store.cancel(job_id)
        raise worker.JobCancelled()

    _patch_pipeline(monkeypatch, fetch_matches=cancel_mid_fetch)

    worker.execute_job(job, store, web_config)

    final = store.get(job_id)
    assert final["state"] == jobs.CANCELLED
    assert final["error"] == ""
    assert store.get_player("test_euw")["base_completed_at"] is None


def test_tracked_players_recovers_group_from_registry(store: JobStore) -> None:
    """Incomplete job players_json must not collapse a group slug to a solo path."""
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
    job = {
        "player_slug": "alice_euw__bob_euw",
        "riot_id": "Alice",
        "tagline": "EUW",
        # Only the primary — the bug that wrote solo reports under a group job.
        "players_json": '[{"riot_id":"Alice","tagline":"EUW"}]',
    }
    assert worker._tracked_players_for_job(job, store) == players
