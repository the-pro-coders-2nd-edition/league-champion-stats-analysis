"""Game-review payload caching: identical output, honest invalidation."""

from __future__ import annotations

from pathlib import Path

import mongomock
import pytest

from league_stats_runner.infra import derived as derived_module
from league_stats_runner.infra.derived import KIND_GAME_REVIEW
from league_stats_runner.pipeline import game_review as gr
from league_stats_runner.pipeline.frames import build_analysis_frames
from tests.test_reports import _config, _make_records


def _run(config, records, frames):
    return gr.build_game_review_views(config, records, frames)


def test_warm_payload_matches_cold(tmp_path: Path) -> None:
    config = _config(tmp_path)
    records = _make_records()
    frames = build_analysis_frames(records)

    cold = _run(config, records, frames)
    warm = _run(config, records, frames)

    assert warm.model_dump(mode="json") == cold.model_dump(mode="json")


def test_second_run_does_not_rebuild(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path)
    records = _make_records()
    frames = build_analysis_frames(records)
    _run(config, records, frames)

    calls = {"n": 0}
    original = gr._build_payload

    def counted(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(gr, "_build_payload", counted)
    _run(config, records, frames)

    assert calls["n"] == 0


def test_a_new_game_rebuilds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path)
    records = _make_records()
    _run(config, records, build_analysis_frames(records))

    extra = records[0].model_copy(
        update={"match_id": "EUW1_new", "game_creation_ms": 1_900_000_000_000}
    )
    grown = [extra, *records]

    calls = {"n": 0}
    original = gr._build_payload

    def counted(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(gr, "_build_payload", counted)
    _run(config, grown, build_analysis_frames(grown))

    assert calls["n"] == 1, "per-game baselines depend on the surrounding set"


def test_a_corrupt_entry_is_recovered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = mongomock.MongoClient()
    monkeypatch.setattr(derived_module, "_build_mongo_client", lambda uri: client)
    db_name = derived_module.db_name_from_uri(derived_module._resolve_mongo_uri())

    config = _config(tmp_path)
    records = _make_records()
    frames = build_analysis_frames(records)
    cold = _run(config, records, frames)

    client[db_name]["derived"].update_many(
        {"kind": KIND_GAME_REVIEW}, {"$set": {"payload": {"queues": 7}}}
    )

    recovered = _run(config, records, frames)
    assert recovered.model_dump(mode="json") == cold.model_dump(mode="json")
