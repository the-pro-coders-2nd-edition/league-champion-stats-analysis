"""Game Review pipeline wiring."""

from __future__ import annotations

import hashlib
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
from league_stats.infra.ddragon_assets import ABILITY_SLOTS, DDragonAssets
from league_stats.infra.derived import KIND_GAME_REVIEW, DerivedStore, slice_fingerprint
from league_stats.pipeline.frames import AnalysisFrames
from league_stats.utils import get_logger


def _lookup_account_icon(
    account: str | None,
    account_icons: dict[str, str] | None,
) -> str | None:
    if not account or not account_icons:
        return None
    direct = account_icons.get(account)
    if direct:
        return direct
    return account_icons.get(account.casefold())


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
    account_icons: dict[str, str] | None = None,
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
                "pit_ally_icons": [
                    assets.champion_href(name, from_dir=from_dir)
                    for name in objective.pit_ally_champions
                ],
                "pit_enemy_icons": [
                    assets.champion_href(name, from_dir=from_dir)
                    for name in objective.pit_enemy_champions
                ],
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
        skill_sequence=list(build.skill_sequence),
        skill_levels_by_level=list(build.skill_levels_by_level),
        skill_max_level=build.skill_max_level,
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
        ability_icons={
            slot: assets.ability_href(champion, slot, from_dir=from_dir) for slot in ABILITY_SLOTS
        },
        item_icons=[
            assets.item_href_by_name(item_name, from_dir=from_dir) for item_name in build.items
        ],
    )
    key_moments = [
        _enrich_key_moment(moment, assets=assets, from_dir=from_dir)
        for moment in detail.key_moments
    ]
    account_icon = _lookup_account_icon(detail.account, account_icons)

    return detail.model_copy(
        update={
            "champion_icon": assets.champion_href(champion, from_dir=from_dir),
            "opponent_icon": assets.champion_href(detail.opponent, from_dir=from_dir),
            "account_icon": account_icon,
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
    account_icons: dict[str, str] | None = None,
    goal_columns: tuple[str, ...] = (),
) -> GameReviewPayload:
    """Build game review payload with UI icon hrefs.

    ``graphs_dir`` is accepted for call-site compatibility; the story timeline
    is rendered client-side from per-minute series data. ``goal_columns`` are
    the live Career block's current goal columns, folded into the cache key so
    a block retiring/dropping (which can change those columns) busts the
    cached payload instead of serving stale ``career_goal_values``.
    """
    _ = graphs_dir

    # Per-game details are not independently cacheable: _baseline_for_game scores
    # each game against the games around it (leave-one-out, or an exclusive
    # baseline window), so keying on match_id alone would serve a baseline
    # computed from a different record set. The whole payload is cached against
    # the full set instead, which pays off on re-renders that add no games.
    fingerprint = slice_fingerprint(
        [record.match_id for record in records],
        salt="|".join(
            (
                "game_review",
                config.champion,
                config.role,
                str(config.progression_recent_n),
                str(config.progression_baseline_m),
                from_dir.as_posix() if from_dir is not None else "-",
                _icons_salt(account_icons),
                "|".join(goal_columns),
            )
        ),
    )
    with DerivedStore(config.derived_db_path) as derived:
        cached = derived.get(KIND_GAME_REVIEW, fingerprint)
        if cached is not None:
            try:
                return GameReviewPayload.model_validate(cached)
            except Exception as exc:  # noqa: BLE001 - a bad entry must not break a report
                get_logger("game_review").warning(
                    "Discarding cached game review: %s", exc
                )
                derived.delete(KIND_GAME_REVIEW, fingerprint)

        payload = _build_payload(config, records, frames, goal_columns=goal_columns)
        result = _enrich_payload(
            payload,
            config=config,
            assets=assets,
            from_dir=from_dir,
            account_icons=account_icons,
        )
        derived.put(KIND_GAME_REVIEW, fingerprint, result.model_dump(mode="json"))
    return result


def _icons_salt(account_icons: dict[str, str] | None) -> str:
    """Stable fingerprint of the account-icon map, which lands in the output."""
    if not account_icons:
        return "-"
    joined = "|".join(f"{key}={value}" for key, value in sorted(account_icons.items()))
    return hashlib.sha256(joined.encode()).hexdigest()[:12]


def _enrich_payload(
    payload: GameReviewPayload,
    *,
    config: AppConfig,
    assets: DDragonAssets | None,
    from_dir: Path | None,
    account_icons: dict[str, str] | None,
) -> GameReviewPayload:
    """Attach icon hrefs to every game in every queue bundle."""
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
                    account_icons=account_icons,
                )
            games.append(enriched)
        queues[queue_key] = bundle.model_copy(update={"games": games})
    return payload.model_copy(update={"queues": queues})


__all__ = [
    "build_game_review_views",
    "game_review_to_template_context",
]
