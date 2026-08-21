"""Cross-cutting Riot API Prometheus metrics: request duration/outcome and
rate-limiter wait time, recorded from `RiotApiClient._get`/`RateLimiter.acquire`.

Mirrors the `_sample_value`/`_generate_latest_default_registry` pattern
already established in `tests/test_runner_metrics.py`/`tests/test_cron_watch_metrics.py`.
"""

from __future__ import annotations

import mongomock
import pytest
from prometheus_client.parser import text_string_to_metric_families

from league_stats_common.core.config import AppConfig
from league_stats_common.infra.cache import HttpCache
from league_stats_common.infra.riot_api import RateLimiter, RiotApiClient, RiotApiError
from league_stats_runner.infra.raw_match_store import RawMatchStore


def _sample_value(registry_text: str, metric_name: str, labels: dict[str, str] | None = None) -> float | None:
    labels = labels or {}
    for family in text_string_to_metric_families(registry_text):
        for sample in family.samples:
            if sample.name == metric_name and all(
                sample.labels.get(k) == v for k, v in labels.items()
            ):
                return sample.value
    return None


def _generate_latest_default_registry() -> str:
    from prometheus_client import generate_latest, REGISTRY

    return generate_latest(REGISTRY).decode("utf-8")


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, headers: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}
        self.text = "error body"

    def json(self):
        return self._payload


@pytest.fixture
def client(tmp_path):
    mongo = mongomock.MongoClient()
    store = RawMatchStore(mongo, db_name="league_stats_test")
    config = AppConfig(
        riot_id="Test", tagline="EUW", api_key="RGAPI-test", region="europe", platform="euw1"
    )
    http_cache = HttpCache(tmp_path / "http_cache.sqlite")
    return RiotApiClient(config, http_cache, store)


def test_get_records_ok_outcome_and_duration(client, monkeypatch) -> None:
    monkeypatch.setattr(client._session, "get", lambda *a, **k: _FakeResponse(200, {"puuid": "abc"}))

    before = _sample_value(
        _generate_latest_default_registry(),
        "riot_api_requests_total",
        {"endpoint": "account_v1", "outcome": "ok"},
    ) or 0.0

    client.resolve_puuid("Name", "EUW")

    after = _sample_value(
        _generate_latest_default_registry(),
        "riot_api_requests_total",
        {"endpoint": "account_v1", "outcome": "ok"},
    )
    assert after == before + 1

    duration_count = _sample_value(
        _generate_latest_default_registry(),
        "riot_api_request_duration_seconds_count",
        {"endpoint": "account_v1"},
    )
    assert duration_count is not None and duration_count >= 1


def test_get_records_client_error_outcome_on_404(client, monkeypatch) -> None:
    monkeypatch.setattr(client._session, "get", lambda *a, **k: _FakeResponse(404))

    before = _sample_value(
        _generate_latest_default_registry(),
        "riot_api_requests_total",
        {"endpoint": "account_v1", "outcome": "client_error"},
    ) or 0.0

    with pytest.raises(RiotApiError):
        client.resolve_puuid("Name", "EUW")

    after = _sample_value(
        _generate_latest_default_registry(),
        "riot_api_requests_total",
        {"endpoint": "account_v1", "outcome": "client_error"},
    )
    assert after == before + 1


def test_get_records_rate_limited_outcome_then_succeeds(client, monkeypatch) -> None:
    responses = [
        _FakeResponse(429, headers={"Retry-After": "0"}),
        _FakeResponse(200, {"puuid": "abc"}),
    ]

    def _fake_get(*_args, **_kwargs):
        return responses.pop(0)

    monkeypatch.setattr(client._session, "get", _fake_get)
    monkeypatch.setattr("league_stats_common.infra.riot_api.time.sleep", lambda _s: None)

    before = _sample_value(
        _generate_latest_default_registry(),
        "riot_api_requests_total",
        {"endpoint": "account_v1", "outcome": "rate_limited"},
    ) or 0.0

    client.resolve_puuid("Name", "EUW")

    after = _sample_value(
        _generate_latest_default_registry(),
        "riot_api_requests_total",
        {"endpoint": "account_v1", "outcome": "rate_limited"},
    )
    assert after == before + 1


def test_endpoint_label_matches_expected_url_shapes() -> None:
    from league_stats_common.infra.riot_api import _endpoint_label

    assert _endpoint_label("https://europe.api.riotgames.com/riot/account/v1/accounts/by-riot-id/a/b") == "account_v1"
    assert _endpoint_label("https://europe.api.riotgames.com/lol/match/v5/matches/by-puuid/x/ids") == "match_ids"
    assert _endpoint_label("https://europe.api.riotgames.com/lol/match/v5/matches/EUW1_1/timeline") == "match_timeline"
    assert _endpoint_label("https://europe.api.riotgames.com/lol/match/v5/matches/EUW1_1") == "match_v5"
    assert _endpoint_label("https://euw1.api.riotgames.com/lol/league/v4/entries/by-puuid/x") == "league_v4"
    assert _endpoint_label("https://euw1.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/x") == "summoner_v4"
    assert _endpoint_label("https://ddragon.leagueoflegends.com/api/versions.json") == "ddragon"


def test_rate_limiter_acquire_records_wait_seconds(monkeypatch) -> None:
    limiter = RateLimiter(per_second=1, per_two_minutes=100)
    monkeypatch.setattr("league_stats_common.infra.riot_api.time.sleep", lambda _s: None)

    limiter.acquire()

    before = _sample_value(
        _generate_latest_default_registry(), "riot_api_rate_limit_wait_seconds_count"
    ) or 0.0

    limiter.acquire()  # second call within the same 1s window must wait

    after = _sample_value(
        _generate_latest_default_registry(), "riot_api_rate_limit_wait_seconds_count"
    )
    assert after == before + 1
