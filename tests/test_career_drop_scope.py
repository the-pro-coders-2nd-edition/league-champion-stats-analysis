"""Dropping one build's Career block must only re-analyse that build.

The drop is performed by the regenerate run it schedules, and a regenerate sets
``new_match_ids = None`` (``worker.py``), which makes
``should_skip_unchanged_build`` return False for *every* build. So an unscoped
regenerate re-analyses the player's whole report set to act on one champion's
ladder. ``filter_champion``/``filter_role`` narrow ``analysis_pools``
(``orchestrator.py:895-901``), and the drop has both to hand from meta.json.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from league_stats.core.config import WebConfig
from league_stats.web.app import create_app

SLUG = "hugros_euw"
BUILD = "aatrox_top"
OTHER = "jinx_bottom"


def _write_build(reports: Path, slug: str, build_slug: str, champion: str, role: str) -> None:
    build_dir = reports / slug / build_slug
    build_dir.mkdir(parents=True, exist_ok=True)
    (build_dir / "meta.json").write_text(
        json.dumps(
            {
                "champion": champion,
                "role": role,
                "riot_id": "Hugros",
                "tagline": "EUW",
                "region": "euw1",
                "games": 30,
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # AppConfig.cache_dir defaults to the relative ".cache", and career_db_path
    # hangs off it, so without this the drop route writes into the repo's real
    # career database instead of the test's.
    monkeypatch.chdir(tmp_path)
    config = WebConfig(output_dir=tmp_path / "out", app_db_path=tmp_path / "app.sqlite")
    reports = tmp_path / "out" / "reports"
    _write_build(reports, SLUG, BUILD, "Aatrox", "TOP")
    _write_build(reports, SLUG, OTHER, "Jinx", "BOTTOM")
    app = create_app(config, start_worker=False)
    with TestClient(app) as handle:
        app.state.job_store.upsert_player(
            slug=SLUG,
            riot_id="Hugros",
            tagline="EUW",
            region="euw1",
            players=[{"riot_id": "Hugros", "tagline": "EUW"}],
        )
        handle.job_store = app.state.job_store  # type: ignore[attr-defined]
        yield handle


def _seed_ladder(client: TestClient, build_slug: str, champion: str, role: str) -> None:
    """Give the build a ladder so the drop route finds a block in slot 0."""
    import pandas as pd

    from league_stats.analysis.career.engine import advance_career
    from league_stats.analysis.career.tracks import TrackContext
    from league_stats.core.champions import player_slug
    from league_stats.core.config import load_config
    from league_stats.infra.career_store import CareerStore, build_key

    class _C:
        def __init__(self, name: str, score: float) -> None:
            self.name, self.score = name, score

    frame = pd.DataFrame(
        {
            "game_creation_ms": [i * 3_600_000 for i in range(25)],
            "cspm": [6.0] * 25,
            "deaths_pre20": [3.0] * 25,
            "greed_deaths": [1.0] * 25,
            "solo_deaths": [1.0] * 25,
            "shutdown_given": [400.0] * 25,
        }
    )
    app_config = load_config(
        require_api_key=False,
        riot_id="Hugros",
        tagline="EUW",
        region="euw1",
        output_dir=client.app.state.web_config.output_dir,
    )
    components = [_C("Survival", 10.0), _C("Laning", 80.0)]
    with CareerStore(app_config.career_db_path) as store:
        advance_career(
            store,
            build_key(player_slug("Hugros", "EUW"), champion, role),
            TrackContext(
                matches_df=frame,
                objectives_df=pd.DataFrame({"present": [1] * 6}),
                role=role,
                peer_p75={"cspm": 7.5},
            ),
            components,
        )


def test_dropping_a_block_scopes_the_regenerate_to_that_build(client: TestClient) -> None:
    _seed_ladder(client, BUILD, "Aatrox", "TOP")

    response = client.post(f"/api/players/{SLUG}/builds/{BUILD}/career/drop", json={"slot": 0})

    assert response.status_code == 200
    job = client.job_store.get(response.json()["job"]["id"])  # type: ignore[attr-defined]
    assert job["filter_champion"] == "Aatrox"
    assert job["filter_role"] == "TOP"


def test_the_scoped_job_names_the_build_it_will_rebuild(client: TestClient) -> None:
    """A player with several reports should see which one is being rebuilt."""
    _seed_ladder(client, OTHER, "Jinx", "BOTTOM")

    response = client.post(f"/api/players/{SLUG}/builds/{OTHER}/career/drop", json={"slot": 0})

    job = client.job_store.get(response.json()["job"]["id"])  # type: ignore[attr-defined]
    assert job["filter_champion"] == "Jinx"
    assert job["filter_role"] == "BOTTOM"


def test_dropping_a_block_that_does_not_exist_enqueues_nothing(client: TestClient) -> None:
    """No ladder seeded for this build, so there is nothing in slot 0 to drop."""
    response = client.post(f"/api/players/{SLUG}/builds/{BUILD}/career/drop", json={"slot": 0})

    assert response.status_code == 404
    assert client.job_store.list_active_jobs() == []  # type: ignore[attr-defined]
