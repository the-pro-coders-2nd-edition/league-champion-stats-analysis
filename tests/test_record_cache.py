"""Parsed-record caching: identical output, fewer parses."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from league_stats.core.config import AppConfig
from league_stats.core.models import MatchRecord
from league_stats.core.progress import ProgressReporter
from league_stats.infra.cache import MatchStore
from league_stats.infra.derived import KIND_RECORD, DerivedStore
from league_stats.ingest import parser as parser_module
from league_stats.pipeline.fetch import load_all_records
from league_stats.pipeline.services import Services
from tests.fixtures import FAKE_ITEMS, MY_PUUID, make_match, make_timeline

PUUID = MY_PUUID


@pytest.fixture()
def make_services(request: pytest.FixtureRequest):
    """Factory that closes every MatchStore it opens.

    Windows cannot remove a SQLite file that is still open, so leaking stores
    turns tmp_path cleanup into a CI-only failure.
    """
    opened: list[MatchStore] = []

    def factory(tmp_path: Path, match_count: int = 6) -> Services:
        services = _services(tmp_path, match_count)
        opened.append(services.store)
        return services

    def close_all() -> None:
        for store in opened:
            store.close()

    request.addfinalizer(close_all)
    return factory


def _services(tmp_path: Path, match_count: int = 6) -> Services:
    config = AppConfig(
        riot_id="Test",
        tagline="EUW",
        api_key="RGAPI-test",
        champion="Viktor",
        role="MIDDLE",
        output_dir=tmp_path / "output",
        cache_dir=tmp_path / "cache",
    )
    config.ensure_directories()
    store = MatchStore(config.db_path)
    for index in range(match_count):
        match = make_match()
        match["info"]["gameCreation"] = 1_700_000_000_000 + index * 3_600_000
        store.save_match(f"EUW1_{index}", PUUID, match)
        store.save_timeline(f"EUW1_{index}", make_timeline())

    client = MagicMock()
    client.fetch_item_catalog.return_value = FAKE_ITEMS
    return Services(
        config=config,
        http_cache=MagicMock(),
        store=store,
        client=client,
        assets=MagicMock(),
        progress=ProgressReporter(),
    )


def _dump(records: list[MatchRecord]) -> list[dict[str, Any]]:
    return [record.model_dump(mode="json") for record in records]


def test_cached_records_are_identical_to_freshly_parsed_ones(tmp_path: Path, make_services) -> None:
    services = make_services(tmp_path)

    cold = load_all_records(services, PUUID)
    warm = load_all_records(services, PUUID)

    assert cold
    assert _dump(warm) == _dump(cold)


def test_second_run_does_not_reparse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_services
) -> None:
    services = make_services(tmp_path)
    load_all_records(services, PUUID)

    calls = {"n": 0}
    original = parser_module.MatchParser.parse

    def counted(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        return original(self, *args, **kwargs)

    monkeypatch.setattr(parser_module.MatchParser, "parse", counted)
    warm = load_all_records(services, PUUID)

    assert warm
    assert calls["n"] == 0


def test_only_new_games_are_parsed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_services
) -> None:
    services = make_services(tmp_path, match_count=5)
    first = load_all_records(services, PUUID)

    match = make_match()
    match["info"]["gameCreation"] = 1_800_000_000_000
    services.store.save_match("EUW1_new", PUUID, match)
    services.store.save_timeline("EUW1_new", make_timeline())

    calls = {"n": 0}
    original = parser_module.MatchParser.parse

    def counted(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        return original(self, *args, **kwargs)

    monkeypatch.setattr(parser_module.MatchParser, "parse", counted)
    second = load_all_records(services, PUUID)

    assert calls["n"] == 1, "only the new game should be parsed"
    assert len(second) == len(first) + 1


def test_a_code_change_forces_a_reparse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_services
) -> None:
    services = make_services(tmp_path)
    load_all_records(services, PUUID)

    monkeypatch.setattr(
        "league_stats.infra.derived.code_version", lambda kind: "cafebabecafebabe"
    )
    calls = {"n": 0}
    original = parser_module.MatchParser.parse

    def counted(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        return original(self, *args, **kwargs)

    monkeypatch.setattr(parser_module.MatchParser, "parse", counted)
    load_all_records(services, PUUID)

    assert calls["n"] > 0, "a code-version change must invalidate cached records"


def test_an_item_catalog_change_forces_a_reparse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_services
) -> None:
    services = make_services(tmp_path)
    load_all_records(services, PUUID)

    bumped = {**FAKE_ITEMS, 9999: "Brand New Item"}
    services.client.fetch_item_catalog.return_value = bumped

    calls = {"n": 0}
    original = parser_module.MatchParser.parse

    def counted(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        return original(self, *args, **kwargs)

    monkeypatch.setattr(parser_module.MatchParser, "parse", counted)
    load_all_records(services, PUUID)

    assert calls["n"] > 0, "item renames change parse output, so must invalidate"


def test_account_label_is_not_baked_into_the_cache(tmp_path: Path, make_services) -> None:
    services = make_services(tmp_path)

    # Cold run stamps a label; the warm run must not inherit it from the cache,
    # so one cached record can serve solo and group reports alike.
    labelled = load_all_records(services, PUUID, account_by_puuid={PUUID: "Smurf#EUW"})
    assert {record.account for record in labelled} == {"Smurf#EUW"}

    warm = load_all_records(services, PUUID)
    assert warm
    assert "Smurf#EUW" not in {record.account for record in warm}


def test_a_corrupt_cached_record_is_recovered(tmp_path: Path, make_services) -> None:
    services = make_services(tmp_path)
    load_all_records(services, PUUID)

    with DerivedStore(services.config.derived_db_path) as derived:
        derived._conn.execute(
            "UPDATE derived SET payload = '{\"nonsense\": true}' WHERE kind = ?",
            (KIND_RECORD,),
        )
        derived._conn.commit()

    recovered = load_all_records(services, PUUID)
    assert len(recovered) == 6
