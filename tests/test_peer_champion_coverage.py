"""Per-(champion, role) peer-sample coverage: a Loki-only signal, not a metric.

`PeerSampleStore.count_by_champion_role` / `service.log_champion_coverage`
are the "games per champion" dashboard-observability follow-up. Champion is a
Data Dragon-sourced, ~170-value-and-growing set with no fixed enum in code,
so this deliberately stays out of Prometheus (see both docstrings) and is
only ever emitted as a structured log line for Grafana's Loki side --
mirrors `tests/test_peer_match_sample_coverage.py`'s structure, but asserts
against `caplog` instead of the Prometheus registry.
"""

from __future__ import annotations

import logging

import mongomock
import pytest

from league_stats_peers.infra.peer_sample_store import PeerSampleStore
from league_stats_peers.service import log_champion_coverage


@pytest.fixture()
def store() -> PeerSampleStore:
    return PeerSampleStore(mongomock.MongoClient())


def _insert_row(store: PeerSampleStore, *, champion: str, role: str, tier: str, verified: bool, idx: int) -> None:
    store.upsert_peer_game(
        {
            "match_id": f"EUW1_{idx}",
            "puuid": f"puuid-{idx}",
            "champion": champion,
            "role": role,
            "tier": tier if verified else "",
            "rank": "II" if verified else "",
            "platform": "euw1",
            "queue_id": 420,
            "metrics": {},
            "ingested_at": float(idx),
            "rank_verified": int(verified),
            "patch": "15.1",
        }
    )


def test_count_by_champion_role_only_counts_verified_rows(store: PeerSampleStore) -> None:
    _insert_row(store, champion="Ahri", role="MIDDLE", tier="GOLD", verified=True, idx=1)
    _insert_row(store, champion="Ahri", role="MIDDLE", tier="DIAMOND", verified=True, idx=2)
    _insert_row(store, champion="Zac", role="JUNGLE", tier="GOLD", verified=True, idx=3)
    _insert_row(store, champion="Jinx", role="BOTTOM", tier="", verified=False, idx=4)

    counts = store.count_by_champion_role()

    assert counts == {("Ahri", "MIDDLE"): 2, ("Zac", "JUNGLE"): 1}


def test_count_by_champion_role_returns_empty_dict_when_no_verified_rows(store: PeerSampleStore) -> None:
    _insert_row(store, champion="Jinx", role="BOTTOM", tier="", verified=False, idx=1)

    assert store.count_by_champion_role() == {}


def test_log_champion_coverage_emits_one_structured_line_per_champion_role(
    store: PeerSampleStore, caplog: pytest.LogCaptureFixture
) -> None:
    _insert_row(store, champion="Ahri", role="MIDDLE", tier="GOLD", verified=True, idx=1)
    _insert_row(store, champion="Ahri", role="MIDDLE", tier="GOLD", verified=True, idx=2)
    _insert_row(store, champion="Zac", role="JUNGLE", tier="GOLD", verified=True, idx=3)

    with caplog.at_level(logging.INFO):
        counts = log_champion_coverage(store)

    assert counts == {("Ahri", "MIDDLE"): 2, ("Zac", "JUNGLE"): 1}
    messages = [r.getMessage() for r in caplog.records]
    assert any(
        "peer_sample_champion_coverage" in m and "champion=Ahri" in m and "role=MIDDLE" in m and "games=2" in m
        for m in messages
    )
    assert any(
        "peer_sample_champion_coverage" in m and "champion=Zac" in m and "role=JUNGLE" in m and "games=1" in m
        for m in messages
    )


def test_log_champion_coverage_logs_nothing_when_store_is_empty(
    store: PeerSampleStore, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        counts = log_champion_coverage(store)

    assert counts == {}
    assert not any("peer_sample_champion_coverage" in r.getMessage() for r in caplog.records)
