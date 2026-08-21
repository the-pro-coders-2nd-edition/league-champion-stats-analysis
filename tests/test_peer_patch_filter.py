"""Peer store patch filtering: prefer the current patch, widen only if thin."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from league_stats_peers.analysis.peer.cache import patch_sort_key, select_by_patch
from league_stats_peers.analysis.peer.ingest import extract_peer_rows
from tests.fixtures import CombinedMatchAndPeerStore


def _rows(spec: dict[str, int]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    index = 0
    for patch, count in spec.items():
        for _ in range(count):
            out.append({"puuid": f"p{index}", "match_id": f"m{index}", "patch": patch})
            index += 1
    return out


def test_patch_sort_key_orders_numerically() -> None:
    assert patch_sort_key("14.9") < patch_sort_key("14.10")
    assert patch_sort_key("14.23") < patch_sort_key("15.1")
    assert patch_sort_key("") == (0, 0)
    assert patch_sort_key("garbage") == (0, 0)


def test_current_patch_alone_is_used_when_it_is_enough() -> None:
    rows = _rows({"14.23": 60, "14.22": 40})
    selected = select_by_patch(rows, "14.23", min_games=50)

    assert len(selected) == 60
    assert {row["patch"] for row in selected} == {"14.23"}


def test_widens_to_the_previous_patch_when_current_is_thin() -> None:
    rows = _rows({"14.23": 20, "14.22": 40, "14.21": 40})
    selected = select_by_patch(rows, "14.23", min_games=50)

    assert {row["patch"] for row in selected} == {"14.23", "14.22"}
    assert len(selected) == 60


def test_future_patches_are_never_used() -> None:
    rows = _rows({"14.24": 80, "14.23": 60})
    selected = select_by_patch(rows, "14.23", min_games=50)

    assert {row["patch"] for row in selected} == {"14.23"}


def test_falls_back_to_everything_when_even_the_history_is_thin() -> None:
    # Sparse store: keep serving what we have rather than dropping to no peers.
    rows = _rows({"14.23": 5, "": 10})
    selected = select_by_patch(rows, "14.23", min_games=50)

    assert len(selected) == len(rows)


def test_no_wanted_patch_is_a_passthrough() -> None:
    rows = _rows({"14.23": 5, "14.22": 5})
    assert select_by_patch(rows, "", min_games=50) is rows


def test_ingest_records_the_patch_from_game_version() -> None:
    from tests.fixtures import make_match

    match = make_match()
    match["info"]["gameVersion"] = "14.23.654.9999"
    rows = extract_peer_rows(match, match_id="EUW1_1", platform="euw1")

    assert rows
    assert {row["patch"] for row in rows} == {"14.23"}


def test_patch_round_trips_through_the_store(tmp_path: Path) -> None:
    store = CombinedMatchAndPeerStore()
    store.upsert_peer_game(
        {
            "match_id": "EUW1_1",
            "puuid": "peer-1",
            "champion": "Zac",
            "role": "JUNGLE",
            "platform": "euw1",
            "queue_id": 420,
            "metrics": {"cspm": 6.0},
            "ingested_at": time.time(),
            "patch": "14.23",
        }
    )
    rows = store.load_peer_games(champion="Zac", role="JUNGLE", platform="euw1")

    assert [row["patch"] for row in rows] == ["14.23"]
    store.close()
