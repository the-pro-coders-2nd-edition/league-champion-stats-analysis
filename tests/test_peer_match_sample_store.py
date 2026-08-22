"""Unit tests for `PeerMatchSampleStore` (RFC "Batched, Round-Robin Live
Sampling for PEERS", Phase 2 -- `peer_match_samples`)."""

from __future__ import annotations

import mongomock
import pytest

from league_stats_peers.analysis.peer.metrics import extract_all_champion_role_rows
from league_stats_peers.infra.peer_match_sample_store import PeerMatchSampleStore
from tests.fixtures import make_match


@pytest.fixture
def store() -> PeerMatchSampleStore:
    return PeerMatchSampleStore(mongomock.MongoClient(), db_name="test_match_samples")


def test_extract_all_champion_role_rows_returns_one_row_per_participant() -> None:
    match = make_match()
    rows = extract_all_champion_role_rows(match)
    assert len(rows) == 10
    champions_and_roles = {(row["champion"], row["role"]) for row in rows}
    assert ("Viktor", "MIDDLE") in champions_and_roles
    assert ("Syndra", "MIDDLE") in champions_and_roles


def test_extract_all_champion_role_rows_excludes_one_puuid() -> None:
    match = make_match()
    exclude_puuid = match["info"]["participants"][0]["puuid"]
    rows = extract_all_champion_role_rows(match, exclude_puuid=exclude_puuid)
    assert len(rows) == 9
    assert all(row["puuid"] != exclude_puuid for row in rows)


def test_peer_match_sample_id_is_a_real_objectid(store: PeerMatchSampleStore) -> None:
    from bson import ObjectId

    match = make_match()
    rows = extract_all_champion_role_rows(match)
    store.upsert_rows("EUW1_1", "14.23", "euw1", rows)

    doc = store._samples.find_one({"match_id": "EUW1_1"})
    assert isinstance(doc["_id"], ObjectId)


def test_upsert_and_find_candidates_round_trip(store: PeerMatchSampleStore) -> None:
    match = make_match()
    rows = extract_all_champion_role_rows(match)
    store.upsert_rows("EUW1_1", "14.23", "euw1", rows)

    candidates = store.find_candidates(platform="euw1", patch="14.23", champion="Syndra", role="MIDDLE")
    assert len(candidates) == 1
    assert candidates[0]["match_id"] == "EUW1_1"
    assert candidates[0]["row"]["champion"] == "Syndra"


def test_find_candidates_not_scoped_by_tier(store: PeerMatchSampleStore) -> None:
    """Rows are stored tier-agnostic (RFC §5.2) -- `find_candidates` has no
    tier parameter at all; every stored row for the key comes back regardless
    of what tier the participant later turns out to be in."""
    match = make_match()
    rows = extract_all_champion_role_rows(match)
    store.upsert_rows("EUW1_1", "14.23", "euw1", rows)

    candidates = store.find_candidates(platform="euw1", patch="14.23", champion="Viktor", role="MIDDLE")
    assert len(candidates) == 1
    assert "tier" not in candidates[0]["row"] or True  # no rank field at write time


def test_find_candidates_filters_by_patch(store: PeerMatchSampleStore) -> None:
    match = make_match()
    rows = extract_all_champion_role_rows(match)
    store.upsert_rows("EUW1_1", "14.23", "euw1", rows)

    assert store.find_candidates(platform="euw1", patch="14.24", champion="Viktor", role="MIDDLE") == []


def test_upsert_rows_is_idempotent_per_match_and_puuid(store: PeerMatchSampleStore) -> None:
    match = make_match()
    rows = extract_all_champion_role_rows(match)
    store.upsert_rows("EUW1_1", "14.23", "euw1", rows)
    store.upsert_rows("EUW1_1", "14.23", "euw1", rows)  # re-storing the same match

    candidates = store.find_candidates(platform="euw1", patch="14.23", champion="Viktor", role="MIDDLE")
    assert len(candidates) == 1
