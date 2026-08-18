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

# Career rung targets step from the player's p50 toward peer p75, so the preview
# needs a peer *distribution*, not just a mean, for the peer-driven tracks to
# show up with realistic numbers.
_PEER_P50 = {key: value for key, value in _PEER_METRICS.items()}
_PEER_P75 = {
    "win": 0.58,
    "kda": 3.2,
    "dpm": 760.0,
    "cspm": 7.9,
    "deaths": 4.2,
    "vspm": 1.25,
    "control_wards": 2.8,
    "kill_participation": 0.68,
    "damage_share": 0.28,
}

# Deterministic per-game variation, cycled at co-prime lengths so consecutive
# games don't line up into a repeating pattern. Values describe a plausible
# Gold-II player: decent farm, sloppy early deaths, absent at objectives,
# hoards gold on recalls, thin vision.
_CSPM = (6.4, 7.1, 5.9, 6.8, 7.4, 6.1, 6.9, 6.3, 7.2, 6.6, 5.8)
_DEATHS_PRE20 = (4, 3, 5, 4, 3, 4, 2, 5, 3, 4, 3, 4, 6)
_DAMAGE_SHARE = (0.22, 0.26, 0.19, 0.24, 0.28, 0.21, 0.25, 0.23)
_VSPM = (0.72, 0.61, 0.88, 0.69, 0.55, 0.81, 0.74)
_CONTROL_WARDS = (1, 0, 2, 0, 1, 0, 0, 1, 0)
_RECALL_GOLD = (1480.0, 1720.0, 1290.0, 1610.0, 1850.0, 1370.0, 1540.0)
_FIGHT_GOLD = (1180.0, 1420.0, 980.0, 1310.0, 1520.0, 1090.0)
_DEATH_GOLD = (860.0, 1140.0, 690.0, 1020.0, 1260.0, 780.0, 950.0)
_PIT_PRESENCE = (
    (1, 0, 0, 0),
    (0, 1, 0, 1),
    (1, 0, 1, 0),
    (0, 0, 1, 0),
    (1, 1, 0, 0),
    (0, 0, 0, 0),
    (1, 1, 1, 0),
)
_FIGHT_PRESENCE = (
    (1, 1, 0, 0),
    (1, 0, 1, 0),
    (0, 1, 1, 1),
    (1, 1, 1, 1),
    (1, 0, 0, 0),
    (0, 1, 1, 0),
    (1, 1, 0, 1),
    (0, 1, 0, 0),
)
_DURATION_S = (1620, 1980, 1440, 2160, 1800, 1500, 2040)


def _cycle(values: tuple, index: int):
    return values[index % len(values)]


def _vary(base: MatchRecord, index: int) -> MatchRecord:
    """Give one cloned fixture game its own believable numbers."""
    duration_s = _cycle(_DURATION_S, index)
    duration_min = duration_s / 60
    cspm = _cycle(_CSPM, index)
    damage_share = _cycle(_DAMAGE_SHARE, index)
    vspm = _cycle(_VSPM, index)
    death_gold = _cycle(_DEATH_GOLD, index)

    death_count = _cycle(_DEATHS_PRE20, index)
    template = base.deaths[0]
    deaths = [
        template.model_copy(
            update={
                "minute": round(4.0 + slot * (15.0 / max(1, death_count)), 1),
                "before_neutral_objective": slot % 3 == 1,
                "current_gold": int(death_gold + slot * 40),
            }
        )
        for slot in range(death_count)
    ]

    pit_flags = _cycle(_PIT_PRESENCE, index)
    objective_template = base.objectives[0]
    objectives = [
        objective_template.model_copy(
            update={
                "minute": round(9.0 + slot * 6.0, 1),
                "present": bool(flag),
                "dead_before": not flag and slot % 2 == 0,
            }
        )
        for slot, flag in enumerate(pit_flags)
    ]

    fight_flags = _cycle(_FIGHT_PRESENCE, index)
    fight_template = base.teamfights[0]
    teamfights = [
        fight_template.model_copy(
            update={
                "start_minute": round(12.0 + slot * 5.0, 1),
                "end_minute": round(12.6 + slot * 5.0, 1),
                "participated": bool(flag),
                "unspent_gold": int(_cycle(_FIGHT_GOLD, index + slot)),
                "won": bool(flag) and slot % 2 == 0,
            }
        )
        for slot, flag in enumerate(fight_flags)
    ]

    kills = 3 + (index % 5)
    assists = 4 + (index % 6)
    return base.model_copy(
        deep=True,
        update={
            "match_id": f"EUW1_{index}",
            "win": index % 5 in (0, 1, 3),
            "game_creation_ms": 1_700_000_000_000 + index * 3_600_000,
            "duration_s": duration_s,
            "economy": base.economy.model_copy(
                update={
                    "cs": round(cspm * duration_min),
                    "cspm": cspm,
                    "gold": round(380 * duration_min),
                    "gpm": 380.0,
                }
            ),
            "combat": base.combat.model_copy(
                update={
                    "kills": kills,
                    "deaths": death_count,
                    "assists": assists,
                    "kda": round((kills + assists) / max(1, death_count), 2),
                    "damage_share": damage_share,
                    "kill_participation": round(0.52 + (index % 7) * 0.02, 2),
                }
            ),
            "vision": base.vision.model_copy(
                update={
                    "vision_score_per_min": vspm,
                    "vision_score": round(vspm * duration_min, 1),
                    "control_wards_bought": _cycle(_CONTROL_WARDS, index),
                }
            ),
            "timeline": base.timeline.model_copy(
                update={"avg_unspent_gold_before_recall": _cycle(_RECALL_GOLD, index)}
            ),
            "deaths": deaths,
            "objectives": objectives,
            "teamfights": teamfights,
        },
    )


def _make_records(n: int = 30) -> list[MatchRecord]:
    base = MatchParser(ItemCatalog(FAKE_ITEMS)).parse(make_match(), make_timeline(), MY_PUUID)
    records = [_vary(base, index) for index in range(n)]
    return sorted(records, key=lambda record: record.game_creation_ms, reverse=True)


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
            peer_p50=_PEER_P50,
            peer_p75=_PEER_P75,
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
