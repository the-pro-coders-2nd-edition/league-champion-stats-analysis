"""Tests for .env loading and player slug helpers."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from league_stats.core.champions import parse_riot_id, players_group_slug
from league_stats.core.config import AppConfig, WebConfig, load_config, load_web_config


def test_parse_riot_id_splits_name_and_tag() -> None:
    """Riot IDs are parsed from Name#Tag form."""
    assert parse_riot_id("Hide on Bush#KR1") == ("Hide on Bush", "KR1")


def test_parse_riot_id_rejects_missing_hash() -> None:
    """Invalid Riot ID strings raise a clear error."""
    with pytest.raises(ValueError, match="Name#Tag"):
        parse_riot_id("NoTagHere")


def test_players_group_slug_joins_multiple_players() -> None:
    """Multi-player groups get a stable sorted slug."""
    slug = players_group_slug([("Bob", "NA1"), ("Alice", "EUW")])
    assert slug == "alice_euw__bob_na1"


def test_output_reports_slug_pins_report_paths() -> None:
    """Web jobs can pin report output to the folder the user refreshed."""
    config = AppConfig(
        riot_id="Alice",
        tagline="EUW",
        api_key="RGAPI-test",
        output_reports_slug="alice_euw__bob_euw",
    )
    assert config.reports_group_slug == "alice_euw__bob_euw"
    assert config.player_reports_dir == Path("output") / "reports" / "alice_euw__bob_euw"


def test_load_config_reads_api_key_from_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RIOT_API_KEY is loaded from .env when not already in the environment."""
    monkeypatch.delenv("RIOT_API_KEY", raising=False)
    dotenv = tmp_path / ".env"
    dotenv.write_text("RIOT_API_KEY=RGAPI-from-dotenv\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    config = load_config(riot_id="Test", tagline="EUW")
    assert config.api_key == "RGAPI-from-dotenv"


def test_load_config_prefers_existing_env_over_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An environment variable wins over .env contents."""
    monkeypatch.setenv("RIOT_API_KEY", "RGAPI-from-env")
    dotenv = tmp_path / ".env"
    dotenv.write_text("RIOT_API_KEY=RGAPI-from-dotenv\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    config = load_config(riot_id="Test", tagline="EUW")
    assert config.api_key == "RGAPI-from-env"


def test_load_config_cli_api_key_overrides_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLI --api-key still takes precedence over .env."""
    monkeypatch.delenv("RIOT_API_KEY", raising=False)
    dotenv = tmp_path / ".env"
    dotenv.write_text("RIOT_API_KEY=RGAPI-from-dotenv\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    config = load_config(riot_id="Test", tagline="EUW", api_key="RGAPI-from-cli")
    assert config.api_key == "RGAPI-from-cli"


def test_gemini_api_key_defaults_to_none() -> None:
    """gemini_api_key is optional; AppConfig doesn't require it."""
    config = AppConfig(riot_id="Test", tagline="EUW", api_key="RGAPI-test")
    assert config.gemini_api_key is None


def test_load_config_reads_gemini_api_key_from_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GEMINI_API_KEY is loaded from .env when not already in the environment."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    dotenv = tmp_path / ".env"
    dotenv.write_text("GEMINI_API_KEY=AIza-from-dotenv\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    config = load_config(riot_id="Test", tagline="EUW", api_key="RGAPI-test")
    assert config.gemini_api_key == "AIza-from-dotenv"


def test_web_config_runner_mode_defaults_to_in_process() -> None:
    """runner_mode defaults off — the monolith runs jobs itself unless opted in."""
    assert WebConfig().runner_mode == "in_process"


def test_web_config_runner_grpc_target_defaults_to_localhost() -> None:
    assert WebConfig().runner_grpc_target == "localhost:50051"


def test_load_web_config_reads_runner_mode_from_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ANALYZER_RUNNER_MODE overrides the default, matching the other ANALYZER_WEB_*
    env vars load_web_config already reads."""
    monkeypatch.setenv("ANALYZER_RUNNER_MODE", "grpc")
    monkeypatch.chdir(tmp_path)

    config = load_web_config()
    assert config.runner_mode == "grpc"


def test_web_config_watch_mode_defaults_to_in_process() -> None:
    """watch_mode defaults off — the monolith runs its own WatchPoller unless opted in."""
    assert WebConfig().watch_mode == "in_process"


def test_load_web_config_reads_watch_mode_from_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ANALYZER_WATCH_MODE overrides the default, matching ANALYZER_RUNNER_MODE."""
    monkeypatch.setenv("ANALYZER_WATCH_MODE", "grpc")
    monkeypatch.chdir(tmp_path)

    config = load_web_config()
    assert config.watch_mode == "grpc"


def test_web_config_runner_storage_mode_defaults_to_sqlite() -> None:
    """runner_storage_mode defaults off — RUNNER uses the local SQLite MatchStore
    unless opted into RawMatchStore/Mongo."""
    assert WebConfig().runner_storage_mode == "sqlite"


def test_web_config_runner_mongo_uri_defaults_to_localhost() -> None:
    assert WebConfig().runner_mongo_uri == "mongodb://localhost:27017/league_stats"


def test_web_config_runner_storage_mode_mongo_requires_peers_grpc() -> None:
    """RawMatchStore doesn't implement MatchStore's peer-game methods, which the
    in-process peer path calls -- this combination must fail loudly at
    construction time, not crash mid-job."""
    with pytest.raises(ValueError, match="requires peers_mode='grpc'"):
        WebConfig(runner_storage_mode="mongo", peers_mode="in_process")


def test_web_config_runner_storage_mode_mongo_allowed_with_peers_grpc() -> None:
    config = WebConfig(runner_storage_mode="mongo", peers_mode="grpc")
    assert config.runner_storage_mode == "mongo"


def test_load_web_config_reads_runner_storage_mode_from_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANALYZER_RUNNER_STORAGE_MODE", "mongo")
    monkeypatch.setenv("ANALYZER_PEERS_MODE", "grpc")
    monkeypatch.chdir(tmp_path)

    config = load_web_config()
    assert config.runner_storage_mode == "mongo"


def test_load_web_config_reads_runner_mongo_uri_from_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RUNNER_MONGO_URI", "mongodb://mongo.internal:27017/league_stats")
    monkeypatch.chdir(tmp_path)

    config = load_web_config()
    assert config.runner_mongo_uri == "mongodb://mongo.internal:27017/league_stats"


def test_load_web_config_runner_mongo_uri_falls_back_to_mongo_uri(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RUNNER_MONGO_URI is unset in the real docker-compose deployment -- only
    the shared MONGO_URI is (see docker-compose.yml's `runner` service). Without
    this fallback, enabling runner_storage_mode=mongo there would silently
    dial the wrong (default localhost) Mongo instance instead of the compose
    network's `mongo` service."""
    monkeypatch.delenv("RUNNER_MONGO_URI", raising=False)
    monkeypatch.setenv("MONGO_URI", "mongodb://mongo:27017/league_stats")
    monkeypatch.chdir(tmp_path)

    config = load_web_config()
    assert config.runner_mongo_uri == "mongodb://mongo:27017/league_stats"


def test_load_web_config_runner_mongo_uri_prefers_explicit_override_over_mongo_uri(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When both are set, RUNNER_MONGO_URI (the RUNNER-specific override) wins
    over the shared MONGO_URI."""
    monkeypatch.setenv("MONGO_URI", "mongodb://mongo:27017/league_stats")
    monkeypatch.setenv("RUNNER_MONGO_URI", "mongodb://mongo-for-runner-only:27017/league_stats")
    monkeypatch.chdir(tmp_path)

    config = load_web_config()
    assert config.runner_mongo_uri == "mongodb://mongo-for-runner-only:27017/league_stats"


def test_load_web_config_reads_runner_grpc_target_from_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RUNNER_GRPC_TARGET overrides the default localhost:50051 target."""
    monkeypatch.setenv("RUNNER_GRPC_TARGET", "runner.internal:9000")
    monkeypatch.chdir(tmp_path)

    config = load_web_config()
    assert config.runner_grpc_target == "runner.internal:9000"
