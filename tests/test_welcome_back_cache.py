"""Tests for `WelcomeBackCache` and its wiring into `create_app`'s lifespan.

The cache itself (`WelcomeBackCache`) is plain dict-backed get/record logic with
no gRPC involved, tested in isolation first. The subscription wiring is tested
by monkeypatching `WelcomeBackSubscriber` the same way `test_web_api.py`'s
`_FakeWatcher` stands in for `WatchPoller`, to prove `create_app` starts/stops
it exactly when `CRON_WATCH_GRPC_TARGET` (`WebConfig.cron_watch_grpc_target`)
is set, and stays a complete no-op otherwise.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from league_stats.core.config import WebConfig
from league_stats.web import app as web_app
from league_stats.web.welcome_back_cache import WelcomeBackCache


# ------------------------------------------------------------------ the cache


def test_get_on_an_empty_cache_returns_none() -> None:
    cache = WelcomeBackCache()
    assert cache.get("hugros") is None


def test_record_then_get_returns_the_stored_payload() -> None:
    cache = WelcomeBackCache()
    payload = {"new_match_id": "EUW1_1", "match_summary": {"win": True}, "detected_at_unix": 100}
    cache.record("hugros", payload)
    assert cache.get("hugros") == payload


def test_get_consumes_the_payload() -> None:
    """A second `get()` for the same slug returns None: reads clear the cache."""
    cache = WelcomeBackCache()
    cache.record("hugros", {"new_match_id": "EUW1_1"})
    assert cache.get("hugros") is not None
    assert cache.get("hugros") is None


def test_record_overwrites_a_prior_unread_payload() -> None:
    cache = WelcomeBackCache()
    cache.record("hugros", {"new_match_id": "EUW1_1"})
    cache.record("hugros", {"new_match_id": "EUW1_2"})
    assert cache.get("hugros") == {"new_match_id": "EUW1_2"}
    assert cache.get("hugros") is None


def test_different_slugs_are_cached_independently() -> None:
    cache = WelcomeBackCache()
    cache.record("hugros", {"new_match_id": "EUW1_1"})
    cache.record("other", {"new_match_id": "EUW1_2"})
    assert cache.get("hugros") == {"new_match_id": "EUW1_1"}
    assert cache.get("other") == {"new_match_id": "EUW1_2"}


# --------------------------------------------------- create_app / lifespan wiring


class _FakeSubscriber:
    """Stand-in for WelcomeBackSubscriber: records start()/stop() calls only."""

    instances: list["_FakeSubscriber"] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.started = False
        self.stopped = False
        _FakeSubscriber.instances.append(self)

    def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


def test_no_subscriber_is_created_when_cron_watch_grpc_target_is_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default config (no CRON_WATCH_GRPC_TARGET): this whole subsystem is a
    no-op. Nothing calls WelcomeBackSubscriber at all, proving no gRPC channel
    is ever opened when the feature is unconfigured."""
    monkeypatch.setattr(web_app, "_verify_players_exist", lambda *args, **kwargs: None)
    _FakeSubscriber.instances = []
    monkeypatch.setattr(web_app, "WelcomeBackSubscriber", _FakeSubscriber)

    config = WebConfig(app_db_path=tmp_path / "app.sqlite", output_dir=tmp_path / "output")
    assert config.cron_watch_grpc_target is None
    application = web_app.create_app(config, start_worker=True)
    with TestClient(application):
        assert _FakeSubscriber.instances == []
    assert _FakeSubscriber.instances == []


def test_welcome_back_cache_is_always_exposed_on_app_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cache is created unconditionally (cheap, no gRPC) so a later task can
    read from it regardless of whether the subscriber feature is enabled."""
    monkeypatch.setattr(web_app, "_verify_players_exist", lambda *args, **kwargs: None)
    config = WebConfig(app_db_path=tmp_path / "app.sqlite", output_dir=tmp_path / "output")
    application = web_app.create_app(config, start_worker=False)
    assert isinstance(application.state.welcome_back_cache, WelcomeBackCache)


def test_cron_watch_grpc_target_starts_and_stops_the_subscriber(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Setting cron_watch_grpc_target must start the subscriber on startup and
    stop it on shutdown, mirroring runner_mode/watch_mode/peers_mode's pattern."""
    monkeypatch.setattr(web_app, "_verify_players_exist", lambda *args, **kwargs: None)
    _FakeSubscriber.instances = []
    monkeypatch.setattr(web_app, "WelcomeBackSubscriber", _FakeSubscriber)

    config = WebConfig(
        app_db_path=tmp_path / "app.sqlite",
        output_dir=tmp_path / "output",
        cron_watch_grpc_target="localhost:50054",
    )
    application = web_app.create_app(config, start_worker=True)
    with TestClient(application):
        subscriber = _FakeSubscriber.instances[0]
        assert subscriber.started is True
        assert subscriber.stopped is False
    assert subscriber.stopped is True


def test_cron_watch_grpc_target_does_not_start_subscriber_when_start_worker_is_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """start_worker=False (the test-suite default) must skip the subscriber too,
    exactly like it already skips the AnalysisWorker and WatchPoller."""
    monkeypatch.setattr(web_app, "_verify_players_exist", lambda *args, **kwargs: None)
    _FakeSubscriber.instances = []
    monkeypatch.setattr(web_app, "WelcomeBackSubscriber", _FakeSubscriber)

    config = WebConfig(
        app_db_path=tmp_path / "app.sqlite",
        output_dir=tmp_path / "output",
        cron_watch_grpc_target="localhost:50054",
    )
    application = web_app.create_app(config, start_worker=False)
    with TestClient(application):
        pass
    assert all(not s.started for s in _FakeSubscriber.instances)
