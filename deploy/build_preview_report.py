"""Builds a synthetic multi-report set for Netlify preview deploys.

Reuses the same fixture helpers the test suite relies on (tests/fixtures.py)
so previews render real report structure without hitting the Riot API or
needing any secrets in CI.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd

from league_stats.analysis.peer import build_comparisons
from league_stats.core.config import AppConfig
from league_stats.core.models import MatchRecord, PeerComparisonResult, RankedEntry
from league_stats.ingest.parser import ItemCatalog, MatchParser
from league_stats.pipeline.orchestrator import run_analysis
from league_stats.presentation.report import refresh_report_indexes
from tests.fixtures import FAKE_ITEMS, MY_PUUID, make_match, make_timeline

TEMPLATE_DIR = REPO_ROOT / "src" / "league_stats" / "presentation" / "templates"

PREVIEW_BUILDS = [
    {"champion": "Viktor", "role": "MIDDLE"},
    {"champion": "Jinx", "role": "BOTTOM"},
    {"champion": "Thresh", "role": "UTILITY"},
]

_PEER_METRICS = {
    "win": 0.5,
    "kda": 2.4,
    "dpm": 640.0,
    "cspm": 7.0,
    "deaths": 5.0,
    "vspm": 1.0,
    "control_wards": 2.0,
    "kill_participation": 0.6,
    "damage_share": 0.2,
}


def _make_records(n: int = 12) -> list[MatchRecord]:
    base = MatchParser(ItemCatalog(FAKE_ITEMS)).parse(make_match(), make_timeline(), MY_PUUID)
    return [
        base.model_copy(
            deep=True,
            update={
                "match_id": f"EUW1_{index}",
                "win": index % 2 == 0,
                "game_creation_ms": 1_700_000_000_000 + index * 3_600_000,
            },
        )
        for index in range(n)
    ]


def _peer(records: list[MatchRecord]) -> PeerComparisonResult:
    return PeerComparisonResult(
        rank_label="GOLD II",
        tier="GOLD",
        source="preview fixture",
        peer_games=0,
        peer_players=0,
        comparisons=build_comparisons(
            pd.DataFrame([r.to_row() for r in records]).mean(numeric_only=True).to_dict(),
            _PEER_METRICS,
        ),
    )


def _config(output_dir: Path, *, champion: str, role: str) -> AppConfig:
    config = AppConfig(
        riot_id="Preview",
        tagline="EUW",
        region="europe",
        api_key="RGAPI-preview",
        champion=champion,
        role=role,
        output_dir=output_dir,
        cache_dir=output_dir.parent / "preview-cache",
        template_dir=TEMPLATE_DIR,
    )
    config.ensure_directories()
    return config


def build_preview(output_dir: Path) -> Path:
    """Render a full synthetic multi-report index into ``output_dir``.

    Returns:
        Path to the generated report hub (index) page.
    """
    ranked = RankedEntry(tier="GOLD", rank="II", league_points=45, wins=80, losses=75)
    records = _make_records()
    peer = _peer(records)

    config = None
    for build in PREVIEW_BUILDS:
        config = _config(output_dir, champion=build["champion"], role=build["role"])
        run_analysis(config, records, peer_comparison=peer, ranked=ranked)

    hub = refresh_report_indexes(
        config.output_dir,
        config.template_dir,
        player_dir=config.player_reports_dir,
        player_label="Preview#EUW",
    )
    if hub is None:
        raise RuntimeError("expected a report hub page after building preview reports")

    hub_relative_url = hub.relative_to(output_dir).as_posix()
    (output_dir / "index.html").write_text(
        f'<meta http-equiv="refresh" content="0; url={hub_relative_url}">',
        encoding="utf-8",
    )
    return hub


if __name__ == "__main__":
    hub_path = build_preview(REPO_ROOT / "output")
    print(f"Preview report built at {hub_path}")
