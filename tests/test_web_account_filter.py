"""Tests for the account-views API endpoint (worker disabled, no network)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient

from league_stats_common.core.config import WebConfig
from league_stats_common.infra.cache import MatchStore
from league_stats_runner.pipeline.services import PlayerContext
import league_stats_api_ui.app as web_app
from tests.fixtures import FAKE_ITEMS, MY_PUUID, make_player_match, make_timeline

ALT_PUUID = "alt-puuid-22222222-2222-2222-2222-222222222222"
GROUP_SLUG = "alice_euw__bob_na1"
BUILD_SLUG = "viktor_middle"
ENDPOINT = f"/api/players/{GROUP_SLUG}/builds/{BUILD_SLUG}/account-views"


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    config = WebConfig(
        app_db_path=tmp_path / "app.sqlite",
        output_dir=tmp_path / "output",
        runner_storage_mode="sqlite",
    )
    real_load_config = web_app.load_config

    def _load_config(**kwargs: Any) -> Any:
        kwargs.setdefault("cache_dir", tmp_path / "cache")
        kwargs.setdefault("api_key", "RGAPI-test")
        return real_load_config(**kwargs)

    monkeypatch.setattr(web_app, "load_config", _load_config)
    monkeypatch.setattr(
        web_app,
        "resolve_player_contexts",
        lambda services: [
            PlayerContext(riot_id="Alice", tagline="EUW", puuid=MY_PUUID),
            PlayerContext(riot_id="Bob", tagline="NA1", puuid=ALT_PUUID),
        ],
    )
    monkeypatch.setattr(
        web_app.RiotApiClient, "fetch_item_catalog", lambda self: FAKE_ITEMS
    )
    application = web_app.create_app(config, start_worker=False)
    with TestClient(application) as test_client:
        yield test_client


def _seed_group_report(tmp_path: Path, *, alice_games: int = 6, bob_games: int = 4) -> None:
    report_dir = tmp_path / "output" / "reports" / GROUP_SLUG / BUILD_SLUG
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "meta.json").write_text(
        json.dumps(
            {
                "player": "Alice#EUW, Bob#NA1",
                "riot_id": "Alice",
                "tagline": "EUW",
                "players": [
                    {"riot_id": "Alice", "tagline": "EUW"},
                    {"riot_id": "Bob", "tagline": "NA1"},
                ],
                "champion": "Viktor",
                "role": "MIDDLE",
                "games": alice_games + bob_games,
                "winrate": 0.5,
                "generated_at": "2026-08-01T10:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    (report_dir / "report.json").write_text("{}", encoding="utf-8")

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    store = MatchStore(cache_dir / "matches.sqlite")
    try:
        for index in range(alice_games):
            match_id = f"EUW1_alice_{index}"
            store.save_match(
                match_id, MY_PUUID, make_player_match(match_id, puuid=MY_PUUID)
            )
            store.save_timeline(match_id, make_timeline())
        for index in range(bob_games):
            match_id = f"EUW1_bob_{index}"
            store.save_match(
                match_id, ALT_PUUID, make_player_match(match_id, puuid=ALT_PUUID)
            )
            store.save_timeline(match_id, make_timeline())
    finally:
        store.close()


def test_account_views_builds_subset(client: TestClient, tmp_path: Path) -> None:
    """A single-account subset is rebuilt from cached matches and cached to disk."""
    _seed_group_report(tmp_path)
    response = client.post(ENDPOINT, json={"accounts": ["Alice#EUW"]})
    assert response.status_code == 200
    payload = response.json()
    assert payload["report_views"]["solo"]["total_games"] == 6
    assert payload["report_views"]["all"]["total_games"] == 6
    assert "progression_views" in payload
    assert "game_review_views" in payload
    cache_dir = (
        tmp_path / "output" / "reports" / GROUP_SLUG / BUILD_SLUG / "account_views"
    )
    assert len(list(cache_dir.glob("*.json"))) == 1


def test_account_views_serves_disk_cache(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A repeated request is answered from the on-disk cache without re-parsing."""
    _seed_group_report(tmp_path)
    first = client.post(ENDPOINT, json={"accounts": ["Bob#NA1"]})
    assert first.status_code == 200

    def _boom(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("cached request must not reload records")

    monkeypatch.setattr(web_app, "load_all_records", _boom)
    second = client.post(ENDPOINT, json={"accounts": ["Bob#NA1"]})
    assert second.status_code == 200
    assert second.json() == first.json()


def test_account_views_rejects_unknown_account(
    client: TestClient, tmp_path: Path
) -> None:
    _seed_group_report(tmp_path)
    response = client.post(ENDPOINT, json={"accounts": ["Mallory#EUW"]})
    assert response.status_code == 400


def test_account_views_missing_report_is_404(client: TestClient) -> None:
    response = client.post(
        "/api/players/nobody_euw/builds/viktor_middle/account-views",
        json={"accounts": ["Alice#EUW"]},
    )
    assert response.status_code == 404
