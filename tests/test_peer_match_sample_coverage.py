"""`peers_match_sample_coverage_games`: verified peer-sample coverage by tier
(dashboard-observability follow-up -- see `service.refresh_match_sample_coverage`'s
docstring for why this is tier-only, not per-champion).

Mirrors `tests/test_cron_watch_metrics.py`'s structure for scraping the
default Prometheus registry.
"""

from __future__ import annotations

import time

import mongomock
import pytest
from prometheus_client.parser import text_string_to_metric_families

from league_stats_peers.analysis.peer.benchmarks import VALID_TIERS
from league_stats_peers.infra.peer_sample_store import PeerSampleStore
from league_stats_peers.service import refresh_match_sample_coverage, start_match_sample_coverage_refresher


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


def test_count_by_tier_only_counts_verified_rows(store: PeerSampleStore) -> None:
    _insert_row(store, champion="Ahri", role="MIDDLE", tier="GOLD", verified=True, idx=1)
    _insert_row(store, champion="Zac", role="JUNGLE", tier="GOLD", verified=True, idx=2)
    _insert_row(store, champion="Yasuo", role="MIDDLE", tier="DIAMOND", verified=True, idx=3)
    _insert_row(store, champion="Jinx", role="BOTTOM", tier="", verified=False, idx=4)

    counts = store.count_by_tier()

    assert counts == {"GOLD": 2, "DIAMOND": 1}


def test_refresh_sets_gauge_for_every_valid_tier_including_zero(store: PeerSampleStore) -> None:
    _insert_row(store, champion="Ahri", role="MIDDLE", tier="PLATINUM", verified=True, idx=1)

    counts = refresh_match_sample_coverage(store)

    assert counts == {"PLATINUM": 1}
    text = _generate_latest_default_registry()
    for tier in VALID_TIERS:
        expected = 1.0 if tier == "PLATINUM" else 0.0
        assert _sample_value(text, "peers_match_sample_coverage_games", {"tier": tier}) == expected


def test_refresh_reflects_new_rows_on_a_second_call(store: PeerSampleStore) -> None:
    refresh_match_sample_coverage(store)
    assert (
        _sample_value(
            _generate_latest_default_registry(), "peers_match_sample_coverage_games", {"tier": "IRON"}
        )
        == 0.0
    )

    _insert_row(store, champion="Sion", role="TOP", tier="IRON", verified=True, idx=1)
    refresh_match_sample_coverage(store)

    assert (
        _sample_value(
            _generate_latest_default_registry(), "peers_match_sample_coverage_games", {"tier": "IRON"}
        )
        == 1.0
    )


def test_refresher_thread_runs_at_least_once(store: PeerSampleStore) -> None:
    _insert_row(store, champion="Vi", role="JUNGLE", tier="SILVER", verified=True, idx=1)

    thread = start_match_sample_coverage_refresher(store, interval_s=0.01)
    try:
        deadline = time.monotonic() + 2.0
        value = None
        while time.monotonic() < deadline:
            value = _sample_value(
                _generate_latest_default_registry(), "peers_match_sample_coverage_games", {"tier": "SILVER"}
            )
            if value == 1.0:
                break
            time.sleep(0.02)
        assert value == 1.0
    finally:
        # Daemon thread -- no explicit stop mechanism (mirrors
        # SamplingScheduler's own worker threads); the process exiting is
        # what reclaims it. Nothing to join in a test.
        assert thread.is_alive()
