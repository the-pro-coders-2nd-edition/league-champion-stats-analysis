"""Game Review pipeline wiring."""

from __future__ import annotations

from pathlib import Path

from league_stats.analysis.game_review.views import build_game_review_views as _build_payload
from league_stats.analysis.game_review.views import game_review_to_template_context
from league_stats.core.config import AppConfig
from league_stats.core.models import (
    GameBuildInfo,
    GameDetail,
    GameFightRow,
    GameReviewPayload,
    GameReviewQueueBundle,
    KeyMoment,
    KeyMomentFrame,
    MatchRecord,
)
from league_stats.infra.ddragon_assets import DDragonAssets
from league_stats.pipeline.frames import AnalysisFrames


def _enrich_key_moment(
    moment: KeyMoment,
    *,
    assets: DDragonAssets,
    from_dir: Path,
) -> KeyMoment:
    frames: list[KeyMomentFrame] = []
    for frame in moment.frames:
        participants = [
            participant.model_copy(
                update={
                    "champion_icon": assets.champion_href(
                        participant.champion,
                        from_dir=from_dir,
                    ),
                }
            )
            for participant in frame.participants
        ]
        objectives = [
            objective.model_copy(
                update={
                    "objective_icon": assets.objective_href(objective.kind, from_dir=from_dir),
                }
            )
            for objective in frame.objectives
        ]
        frames.append(
            frame.model_copy(update={"participants": participants, "objectives": objectives})
        )
    return moment.model_copy(update={"frames": frames})


def _enrich_game_detail(
    detail: GameDetail,
    *,
    assets: DDragonAssets,
    from_dir: Path,
    champion: str,
) -> GameDetail:
    """Attach relative icon hrefs for champions, runes, items, and objectives."""
    deaths = [
        death.model_copy(
            update={
                "killer_icon": assets.champion_href(death.killer, from_dir=from_dir)
                if death.killer
                else None,
            }
        )
        for death in detail.deaths
    ]
    fights = [
        GameFightRow(
            start_minute=fight.start_minute,
            kills=fight.kills,
            deaths=fight.deaths,
            assists=fight.assists,
            damage=fight.damage,
            fight_won=fight.fight_won,
            allies_present=fight.allies_present,
            enemies_present=fight.enemies_present,
            manpower_advantage=fight.manpower_advantage,
            ally_champions=list(fight.ally_champions),
            enemy_champions=list(fight.enemy_champions),
            ally_icons=[
                assets.champion_href(name, from_dir=from_dir) for name in fight.ally_champions
            ],
            enemy_icons=[
                assets.champion_href(name, from_dir=from_dir) for name in fight.enemy_champions
            ],
        )
        for fight in detail.fights
    ]
    objectives = [
        objective.model_copy(
            update={
                "objective_icon": assets.objective_href(objective.kind, from_dir=from_dir),
            }
        )
        for objective in detail.objectives
    ]
    build = detail.build
    enriched_build = GameBuildInfo(
        keystone=build.keystone,
        primary_tree=build.primary_tree,
        secondary_tree=build.secondary_tree,
        primary_runes=list(build.primary_runes),
        secondary_runes=list(build.secondary_runes),
        shards=list(build.shards),
        summoners=list(build.summoners),
        skill_order=build.skill_order,
        items=list(build.items),
        keystone_icon=assets.rune_href(build.keystone, from_dir=from_dir),
        primary_tree_icon=assets.rune_tree_href(build.primary_tree, from_dir=from_dir),
        secondary_tree_icon=assets.rune_tree_href(build.secondary_tree, from_dir=from_dir),
        primary_rune_icons=[
            assets.rune_href(name, from_dir=from_dir) for name in build.primary_runes
        ],
        secondary_rune_icons=[
            assets.rune_href(name, from_dir=from_dir) for name in build.secondary_runes
        ],
        shard_icons=[assets.rune_href(name, from_dir=from_dir) for name in build.shards],
        summoner_icons=[
            assets.summoner_href(spell_name, from_dir=from_dir) for spell_name in build.summoners
        ],
        item_icons=[
            assets.item_href_by_name(item_name, from_dir=from_dir) for item_name in build.items
        ],
    )
    key_moments = [
        _enrich_key_moment(moment, assets=assets, from_dir=from_dir)
        for moment in detail.key_moments
    ]
    return detail.model_copy(
        update={
            "champion_icon": assets.champion_href(champion, from_dir=from_dir),
            "opponent_icon": assets.champion_href(detail.opponent, from_dir=from_dir),
            "deaths": deaths,
            "fights": fights,
            "objectives": objectives,
            "build": enriched_build,
            "key_moments": key_moments,
            "map_background": assets.map_href(from_dir=from_dir),
        }
    )


def build_game_review_views(
    config: AppConfig,
    records: list[MatchRecord],
    frames: AnalysisFrames,
    *,
    graphs_dir: Path | None = None,
    assets: DDragonAssets | None = None,
    from_dir: Path | None = None,
) -> GameReviewPayload:
    """Build game review payload with UI icon hrefs.

    ``graphs_dir`` is accepted for call-site compatibility; the story timeline
    is rendered client-side from per-minute series data.
    """
    _ = graphs_dir
    payload = _build_payload(config, records, frames)

    queues: dict[str, GameReviewQueueBundle] = {}
    for queue_key, bundle in payload.queues.items():
        games: list[GameDetail] = []
        for detail in bundle.games:
            enriched = detail
            if assets is not None and from_dir is not None:
                enriched = _enrich_game_detail(
                    detail,
                    assets=assets,
                    from_dir=from_dir,
                    champion=config.champion,
                )
            games.append(enriched)
        queues[queue_key] = bundle.model_copy(update={"games": games})
    return payload.model_copy(update={"queues": queues})


__all__ = [
    "build_game_review_views",
    "game_review_to_template_context",
]
