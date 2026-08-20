"""Builds a synthetic multi-report set (report.json + manifest.json).

Reuses the same fixture helpers the test suite relies on (tests/fixtures.py)
so this can generate real report structure without hitting the Riot API or
needing any secrets in CI.

Not wired into the Netlify preview build: the preview now proxies /api and
/out to the real deployed app (see netlify.toml) instead of serving fixture
data, so this script is a standalone tool for local testing/dev only.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd

from league_stats_peers.analysis.peer import build_comparisons
from league_stats_common.core.config import AppConfig
from league_stats_common.core.models import MatchRecord, PeerComparisonResult, RankedEntry
from league_stats_runner.ingest.parser import ItemCatalog, MatchParser
from league_stats_runner.pipeline.orchestrator import run_analysis
from league_stats_runner.presentation.report import build_manifest_entry, refresh_report_indexes
from tests.fixtures import FAKE_ITEMS, MY_PUUID, make_match, make_timeline

@dataclass(frozen=True)
class PreviewBuild:
    """One synthetic player: a champion, a role, and a believable stat spread.

    Each series is cycled per game at co-prime lengths so consecutive games do
    not line up into a repeating pattern. Values are chosen so the three preview
    builds tell genuinely different stories -- and so their Career ladders lead
    with different tracks -- rather than sharing one record set.
    """

    champion: str
    role: str
    story: str
    cspm: tuple[float, ...]
    deaths_pre20: tuple[int, ...]
    deaths_post20: tuple[int, ...]
    damage_share: tuple[float, ...]
    vspm: tuple[float, ...]
    control_wards: tuple[int, ...]
    recall_gold: tuple[float, ...]
    fight_gold: tuple[float, ...]
    death_gold: tuple[float, ...]
    pit_presence: tuple[tuple[int, ...], ...]
    fight_presence: tuple[tuple[int, ...], ...]
    peer_p50: dict[str, float]
    peer_p75: dict[str, float]
    gpm: float
    games: int


# Peer baselines are role-shaped: a support is not measured against a mid
# laner's CS/min, and a mid laner is not measured against a support's vision.
_MID_PEER_P50 = {
    "win": 0.50, "kda": 2.6, "dpm": 700.0, "cspm": 7.4, "deaths": 5.0,
    "vspm": 0.85, "control_wards": 1.9, "kill_participation": 0.58, "damage_share": 0.27,
}
_MID_PEER_P75 = {
    "win": 0.57, "kda": 3.4, "dpm": 820.0, "cspm": 8.3, "deaths": 4.2,
    "vspm": 1.05, "control_wards": 2.6, "kill_participation": 0.66, "damage_share": 0.31,
}
_ADC_PEER_P50 = {
    "win": 0.50, "kda": 2.8, "dpm": 760.0, "cspm": 8.0, "deaths": 5.2,
    "vspm": 0.75, "control_wards": 1.6, "kill_participation": 0.60, "damage_share": 0.29,
}
_ADC_PEER_P75 = {
    "win": 0.58, "kda": 3.6, "dpm": 890.0, "cspm": 8.9, "deaths": 4.4,
    "vspm": 0.95, "control_wards": 2.3, "kill_participation": 0.68, "damage_share": 0.33,
}
_SUPPORT_PEER_P50 = {
    "win": 0.50, "kda": 2.9, "dpm": 330.0, "cspm": 2.6, "deaths": 6.0,
    "vspm": 1.55, "control_wards": 3.4, "kill_participation": 0.62, "damage_share": 0.12,
}
_SUPPORT_PEER_P75 = {
    "win": 0.58, "kda": 3.8, "dpm": 410.0, "cspm": 3.2, "deaths": 5.0,
    "vspm": 1.95, "control_wards": 4.5, "kill_participation": 0.70, "damage_share": 0.15,
}

PREVIEW_BUILDS = (
    PreviewBuild(
        champion="Viktor",
        role="MIDDLE",
        story="solid farm, dies too much early, never at the pit",
        cspm=(6.9, 7.4, 6.3, 7.1, 7.7, 6.6, 7.2, 6.7, 7.5, 6.4, 7.0),
        deaths_pre20=(4, 3, 5, 4, 3, 4, 2, 5, 3, 4, 3, 4, 6),
        deaths_post20=(3, 2, 3, 2, 4, 2, 3, 1, 3, 2, 4),
        damage_share=(0.24, 0.28, 0.21, 0.26, 0.30, 0.23, 0.27, 0.25),
        vspm=(0.72, 0.61, 0.88, 0.69, 0.55, 0.81, 0.74),
        control_wards=(1, 0, 2, 0, 1, 0, 0, 1, 0),
        recall_gold=(1480.0, 1720.0, 1290.0, 1610.0, 1850.0, 1370.0, 1540.0),
        fight_gold=(1180.0, 1420.0, 980.0, 1310.0, 1520.0, 1090.0),
        death_gold=(860.0, 1140.0, 690.0, 1020.0, 1260.0, 780.0, 950.0),
        pit_presence=(
            (1, 0, 0, 0), (0, 1, 0, 1), (1, 0, 1, 0),
            (0, 0, 1, 0), (1, 1, 0, 0), (0, 0, 0, 0), (1, 1, 1, 0),
        ),
        fight_presence=(
            (1, 1, 0, 0), (1, 0, 1, 0), (0, 1, 1, 1), (1, 1, 1, 1),
            (1, 0, 0, 0), (0, 1, 1, 0), (1, 1, 0, 1), (0, 1, 0, 0),
        ),
        peer_p50=_MID_PEER_P50,
        peer_p75=_MID_PEER_P75,
        gpm=395.0,
        games=60,
    ),
    PreviewBuild(
        champion="Jinx",
        role="BOTTOM",
        story="carries damage, hoards gold on recalls, no vision",
        cspm=(7.8, 8.4, 7.2, 8.0, 8.6, 7.5, 8.2, 7.6, 8.5, 7.4),
        deaths_pre20=(2, 3, 2, 4, 2, 3, 1, 3, 2, 4, 2),
        deaths_post20=(2, 1, 3, 2, 2, 1, 2, 3, 1),
        damage_share=(0.30, 0.34, 0.27, 0.32, 0.36, 0.29, 0.33),
        vspm=(0.52, 0.44, 0.63, 0.49, 0.38, 0.58, 0.55, 0.47),
        control_wards=(0, 1, 0, 0, 0, 1, 0),
        recall_gold=(1690.0, 1930.0, 1520.0, 1810.0, 2080.0, 1610.0, 1750.0, 1880.0),
        fight_gold=(1440.0, 1680.0, 1250.0, 1570.0, 1790.0, 1360.0),
        death_gold=(1080.0, 1340.0, 910.0, 1220.0, 1460.0, 990.0, 1150.0),
        pit_presence=(
            (1, 0, 1, 0), (0, 1, 0, 1), (1, 1, 0, 0),
            (0, 1, 1, 0), (1, 0, 0, 1), (1, 1, 1, 0),
        ),
        fight_presence=(
            (1, 1, 0, 1), (1, 0, 1, 1), (0, 1, 1, 1), (1, 1, 1, 1),
            (1, 1, 0, 0), (0, 1, 1, 0), (1, 0, 1, 0),
        ),
        peer_p50=_ADC_PEER_P50,
        peer_p75=_ADC_PEER_P75,
        gpm=430.0,
        games=44,
    ),
    PreviewBuild(
        champion="Thresh",
        role="UTILITY",
        story="thin vision uptime and late to fights",
        cspm=(2.3, 2.7, 2.0, 2.5, 2.9, 2.2, 2.6, 2.4),
        deaths_pre20=(3, 4, 2, 3, 5, 3, 4, 2, 3, 4),
        deaths_post20=(2, 3, 2, 1, 3, 2, 2, 3),
        damage_share=(0.10, 0.13, 0.09, 0.12, 0.14, 0.11, 0.12),
        vspm=(1.02, 1.18, 0.88, 1.10, 1.26, 0.95, 1.06, 1.14, 0.91),
        control_wards=(2, 1, 3, 1, 2, 1, 2, 0, 2),
        recall_gold=(1060.0, 1240.0, 880.0, 1150.0, 1380.0, 960.0, 1120.0),
        fight_gold=(820.0, 1010.0, 690.0, 940.0, 1160.0, 760.0),
        death_gold=(640.0, 830.0, 510.0, 740.0, 950.0, 580.0, 700.0),
        pit_presence=(
            (1, 0, 1, 0), (0, 1, 0, 0), (1, 1, 0, 0),
            (0, 0, 1, 1), (1, 0, 0, 0), (0, 1, 1, 0), (1, 1, 0, 1),
        ),
        fight_presence=(
            (1, 0, 1, 0), (0, 1, 0, 1), (1, 1, 0, 0), (0, 1, 1, 0),
            (1, 0, 0, 1), (0, 0, 1, 0), (1, 1, 1, 0),
        ),
        peer_p50=_SUPPORT_PEER_P50,
        peer_p75=_SUPPORT_PEER_P75,
        gpm=270.0,
        games=31,
    ),
)

_DURATION_S = (1620, 1980, 1440, 2160, 1800, 1500, 2040, 1740, 2280)


def _cycle(values: tuple, index: int):
    return values[index % len(values)]


def _vary(base: MatchRecord, build: PreviewBuild, index: int) -> MatchRecord:
    """Give one cloned fixture game its own believable numbers for this build."""
    duration_s = _cycle(_DURATION_S, index)
    duration_min = duration_s / 60
    cspm = _cycle(build.cspm, index)
    damage_share = _cycle(build.damage_share, index)
    vspm = _cycle(build.vspm, index)
    death_gold = _cycle(build.death_gold, index)

    # Early and late deaths are tracked separately: a build can be reckless in
    # the first 20 minutes while still having a respectable total, or the other
    # way round, and the Career tracks read those two facts from different
    # columns (deaths_pre20 vs deaths).
    early = _cycle(build.deaths_pre20, index)
    late = _cycle(build.deaths_post20, index)
    template = base.deaths[0]
    early_minutes = [round(4.0 + slot * (15.0 / max(1, early)), 1) for slot in range(early)]
    late_span = max(1.0, duration_min - 21.0)
    late_minutes = [
        round(21.0 + slot * (late_span / max(1, late)), 1) for slot in range(late)
    ]
    deaths = [
        template.model_copy(
            update={
                "minute": minute,
                "before_neutral_objective": slot % 3 == 1,
                "current_gold": int(death_gold + slot * 40),
            }
        )
        for slot, minute in enumerate(early_minutes + late_minutes)
    ]

    objective_template = base.objectives[0]
    objectives = [
        objective_template.model_copy(
            update={
                "minute": round(9.0 + slot * 6.0, 1),
                "present": bool(flag),
                "dead_before": not flag and slot % 2 == 0,
            }
        )
        for slot, flag in enumerate(_cycle(build.pit_presence, index))
    ]

    fight_template = base.teamfights[0]
    teamfights = [
        fight_template.model_copy(
            update={
                "start_minute": round(12.0 + slot * 5.0, 1),
                "end_minute": round(12.6 + slot * 5.0, 1),
                "participated": bool(flag),
                "unspent_gold": int(_cycle(build.fight_gold, index + slot)),
                "won": bool(flag) and slot % 2 == 0,
            }
        )
        for slot, flag in enumerate(_cycle(build.fight_presence, index))
    ]

    kills = 3 + (index % 5)
    assists = 4 + (index % 6)
    return base.model_copy(
        deep=True,
        update={
            "match_id": f"EUW1_{build.champion.lower()}_{index}",
            "champion": build.champion,
            "role": build.role,
            "win": index % 5 in (0, 1, 3),
            "game_creation_ms": 1_700_000_000_000 + index * 3_600_000,
            "duration_s": duration_s,
            "economy": base.economy.model_copy(
                update={
                    "cs": round(cspm * duration_min),
                    "cspm": cspm,
                    "gold": round(build.gpm * duration_min),
                    "gpm": build.gpm,
                }
            ),
            "combat": base.combat.model_copy(
                update={
                    "kills": kills,
                    "deaths": len(deaths),
                    "assists": assists,
                    "kda": round((kills + assists) / max(1, len(deaths)), 2),
                    "damage_share": damage_share,
                    "kill_participation": round(0.52 + (index % 7) * 0.02, 2),
                }
            ),
            "vision": base.vision.model_copy(
                update={
                    "vision_score_per_min": vspm,
                    "vision_score": round(vspm * duration_min, 1),
                    "control_wards_bought": _cycle(build.control_wards, index),
                }
            ),
            "timeline": base.timeline.model_copy(
                update={"avg_unspent_gold_before_recall": _cycle(build.recall_gold, index)}
            ),
            "deaths": deaths,
            "objectives": objectives,
            "teamfights": teamfights,
        },
    )


def _make_records(build: PreviewBuild) -> list[MatchRecord]:
    """Games for one build. Counts differ per build so the player hub's
    most-played-first ordering (and therefore its default report) is meaningful
    rather than decided by whichever build happened to render last."""
    base = MatchParser(ItemCatalog(FAKE_ITEMS)).parse(make_match(), make_timeline(), MY_PUUID)
    records = [_vary(base, build, index) for index in range(build.games)]
    return sorted(records, key=lambda record: record.game_creation_ms, reverse=True)


def _peer(records: list[MatchRecord], build: PreviewBuild) -> PeerComparisonResult:
    return PeerComparisonResult(
        rank_label="GOLD II",
        tier="GOLD",
        champion=build.champion,
        role=build.role,
        source="preview fixture",
        peer_games=0,
        peer_players=0,
        comparisons=build_comparisons(
            pd.DataFrame([r.to_row() for r in records]).mean(numeric_only=True).to_dict(),
            build.peer_p50,
            role=build.role,
            peer_p50=build.peer_p50,
            peer_p75=build.peer_p75,
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
    )
    config.ensure_directories()
    return config


def build_preview(output_dir: Path) -> Path:
    """Write a full synthetic multi-report set (report.json + manifest.json) to ``output_dir``.

    Returns:
        Path to the generated player manifest (``manifest.json``).
    """
    ranked = RankedEntry(tier="GOLD", rank="II", league_points=45, wins=80, losses=75)

    # Records are generated for every build before anything renders, because each
    # report's champion switcher needs the *full* build list -- rendering
    # one-at-a-time would leave the first report with nothing to switch to.
    histories = [(build, _make_records(build)) for build in PREVIEW_BUILDS]
    manifest_builds = [
        build_manifest_entry(
            champion=build.champion,
            role=build.role,
            games=len(records),
            winrate=sum(1 for record in records if record.win) / len(records),
        )
        for build, records in histories
    ]

    config = None
    for build, records in histories:
        config = _config(output_dir, champion=build.champion, role=build.role)
        run_analysis(
            config,
            records,
            peer_comparison=_peer(records, build),
            ranked=ranked,
            player_builds=manifest_builds,
        )

    hub = refresh_report_indexes(
        config.output_dir,
        config.template_dir,
        player_dir=config.player_reports_dir,
        player_label="Preview#EUW",
    )
    if hub is None:
        raise RuntimeError("expected a player manifest after building preview reports")
    return hub


if __name__ == "__main__":
    hub_path = build_preview(REPO_ROOT / "output")
    print(f"Preview report built at {hub_path}")
