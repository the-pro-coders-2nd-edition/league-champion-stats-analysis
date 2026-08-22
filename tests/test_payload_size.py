"""The report payload must not ship reports nobody asked for.

A 3-account group report was 123 MB, of which 100 MB (81%) was
``account_filter.views``: a fully precomputed report for all 6 account subsets,
none of which the default view uses -- ``reportState.js`` maps the ``all`` key to
the base payload, not to a subset. Switching build took ~15s on a home connection
purely on transfer.

Subsets are now computed on demand by the endpoint that already exists for larger
groups, and responses are compressed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from league_stats_common.core.config import WebConfig
from league_stats_runner.pipeline.orchestrator import (
    ACCOUNT_FULL_COMBINATION_LIMIT,
    account_subset_keys,
)
from league_stats_api_ui.app import create_app

SLUG = "group_euw"
BUILD = "aatrox_top"


# --- what gets precomputed -------------------------------------------------


def test_no_subset_is_precomputed_by_default() -> None:
    """The on-demand endpoint covers every combination, so none is worth 17 MB."""
    assert account_subset_keys(["A#1", "B#1", "C#1"]) == []


def test_the_default_limit_precomputes_nothing() -> None:
    assert ACCOUNT_FULL_COMBINATION_LIMIT == 0


@pytest.mark.parametrize("count", [2, 3, 4, 5, 8])
def test_no_group_size_precomputes_subsets(count: int) -> None:
    labels = [f"P{i}#EUW" for i in range(count)]

    assert account_subset_keys(labels) == []


def test_an_explicit_limit_still_precomputes_when_asked() -> None:
    """The capability stays, so the trade-off can be reversed without a rewrite."""
    subsets = account_subset_keys(["B#1", "A#1"], full_combination_limit=4)

    assert subsets == [("A#1",), ("B#1",)]


def test_an_explicit_limit_below_the_group_size_falls_back_to_singletons() -> None:
    subsets = account_subset_keys(["C#1", "A#1", "B#1"], full_combination_limit=2)

    assert subsets == [("A#1",), ("B#1",), ("C#1",)]


# --- response compression --------------------------------------------------


def _write_report(reports: Path, payload: dict) -> None:
    build_dir = reports / SLUG / BUILD
    build_dir.mkdir(parents=True, exist_ok=True)
    (build_dir / "report.json").write_text(json.dumps(payload), encoding="utf-8")
    (build_dir / "meta.json").write_text(
        json.dumps(
            {
                "champion": "Aatrox", "role": "TOP", "riot_id": "A", "tagline": "EUW",
                "region": "euw1", "games": 30,
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    config = WebConfig(
        output_dir=tmp_path / "out"
    )
    # Compressible bulk, the shape a real report's figures have.
    _write_report(
        tmp_path / "out" / "reports",
        {"figures": {f"fig_{i}": "<div>" + "0.123456," * 900 + "</div>" for i in range(12)}},
    )
    app = create_app(config, start_worker=False)
    with TestClient(app) as handle:
        yield handle


def test_a_large_payload_is_compressed_when_the_client_accepts_it(
    client: TestClient,
) -> None:
    response = client.get(
        f"/api/players/{SLUG}/builds/{BUILD}", headers={"Accept-Encoding": "gzip"}
    )

    assert response.status_code == 200
    assert response.headers.get("content-encoding") == "gzip"


def test_the_compressed_payload_still_decodes_to_the_same_json(
    client: TestClient,
) -> None:
    response = client.get(
        f"/api/players/{SLUG}/builds/{BUILD}", headers={"Accept-Encoding": "gzip"}
    )

    assert len(response.json()["figures"]) == 12


def test_compression_actually_shrinks_the_payload(client: TestClient) -> None:
    plain = client.get(
        f"/api/players/{SLUG}/builds/{BUILD}", headers={"Accept-Encoding": "identity"}
    )
    gzipped = client.get(
        f"/api/players/{SLUG}/builds/{BUILD}", headers={"Accept-Encoding": "gzip"}
    )

    assert plain.headers.get("content-encoding") is None
    assert int(gzipped.headers["content-length"]) < len(plain.content)


def test_a_client_that_does_not_accept_gzip_gets_plain_json(client: TestClient) -> None:
    response = client.get(
        f"/api/players/{SLUG}/builds/{BUILD}", headers={"Accept-Encoding": "identity"}
    )

    assert response.headers.get("content-encoding") is None
    assert response.json()["figures"]


def test_a_tiny_response_is_not_worth_compressing(client: TestClient) -> None:
    """Below the threshold gzip costs CPU and framing bytes for nothing."""
    response = client.get("/api/activity", headers={"Accept-Encoding": "gzip"})

    assert response.status_code == 200
    assert response.headers.get("content-encoding") is None
