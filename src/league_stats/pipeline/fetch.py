"""Match download and record loading."""

from __future__ import annotations

from dataclasses import dataclass

from tqdm import tqdm

from league_stats.core.config import PlayerIdentity
from league_stats.core.models import MatchRecord
from league_stats.core.progress import STAGE_FETCHING, STAGE_PARSING
from league_stats.ingest.parser import BaseMatchFilter, ItemCatalog, MatchParser
from league_stats.pipeline.services import PlayerContext, Services
from league_stats.utils import get_logger


@dataclass(frozen=True)
class FetchResult:
    """Resolved players plus match ids newly available after this fetch."""

    contexts: list[PlayerContext]
    new_match_ids: frozenset[str]


def _resolve_player_context(services: Services, player: PlayerIdentity) -> PlayerContext:
    """Resolve PUUID and cache profile icon + solo/duo rank when available."""
    puuid = services.client.resolve_puuid(player.riot_id, player.tagline)
    icon_id = services.client.fetch_profile_icon_id(puuid)
    if icon_id is not None:
        services.assets.ensure_profile_icon(icon_id)
    ranked = services.client.fetch_solo_rank(puuid)
    solo_tier = solo_rank = None
    solo_lp: int | None = None
    if ranked is not None:
        solo_tier = ranked.tier.upper()
        solo_rank = ranked.rank.upper() if ranked.rank else None
        solo_lp = ranked.league_points
        services.assets.ensure_rank_emblem(solo_tier)
    return PlayerContext(
        riot_id=player.riot_id,
        tagline=player.tagline,
        puuid=puuid,
        profile_icon_id=icon_id,
        solo_tier=solo_tier,
        solo_rank=solo_rank,
        solo_lp=solo_lp,
    )


def fetch_matches(services: Services) -> FetchResult:
    """Resolve every tracked player and download their match histories."""
    config = services.config
    contexts: list[PlayerContext] = []
    new_ids: set[str] = set()
    for player in config.players:
        services.progress.update(
            STAGE_FETCHING, detail=f"Looking up {player.label} match history"
        )
        context = _resolve_player_context(services, player)
        match_ids = services.client.fetch_ranked_match_ids(
            context.puuid, config.match_count
        )
        new_ids.update(services.client.download_matches(context.puuid, match_ids))
        contexts.append(context)
    return FetchResult(contexts=contexts, new_match_ids=frozenset(new_ids))


def resolve_player_contexts(services: Services) -> list[PlayerContext]:
    """Resolve PUUIDs for every configured player without downloading."""
    return [
        _resolve_player_context(services, player) for player in services.config.players
    ]


def load_all_records(
    services: Services,
    puuids: str | list[str],
    *,
    account_by_puuid: dict[str, str] | None = None,
) -> list[MatchRecord]:
    """Parse stored ranked queue games for one or more players.

    ``account_by_puuid`` fills in the configured Riot ID when the match payload
    omits ``riotIdGameName`` / ``riotIdTagline`` (older cached docs).
    """
    if isinstance(puuids, str):
        puuid_list = [puuids]
    else:
        puuid_list = list(puuids)
    labels = account_by_puuid or {}
    log = get_logger("pipeline")
    catalog = ItemCatalog(services.client.fetch_item_catalog())
    match_filter = BaseMatchFilter(services.config)
    parser = MatchParser(catalog)
    records: list[MatchRecord] = []
    for puuid in puuid_list:
        match_ids = list(services.store.iter_match_ids(puuid))
        total = len(match_ids)
        for index, match_id in enumerate(
            tqdm(match_ids, desc="Parsing matches", unit="match"), start=1
        ):
            if index == 1 or index % 25 == 0 or index == total:
                services.progress.update(
                    STAGE_PARSING,
                    current=index,
                    total=total,
                    detail=f"Parsing matches ({index}/{total})",
                )
            match = services.store.load_match(match_id)
            timeline = services.store.load_timeline(match_id)
            if not match or not timeline:
                continue
            if not match_filter.accept(match, puuid):
                continue
            try:
                record = parser.parse(match, timeline, puuid)
            except Exception as exc:
                log.warning("Failed to parse %s: %s", match_id, exc)
                continue
            label = labels.get(puuid)
            if label:
                record = record.model_copy(update={"account": label})
            records.append(record)
    records.sort(key=lambda r: r.game_creation_ms, reverse=True)
    log.info("Parsed %d qualifying ranked queue games", len(records))
    return records


def group_records(records: list[MatchRecord], champion: str, role: str) -> list[MatchRecord]:
    """Filter parsed records to one champion + lane build."""
    return [r for r in records if r.champion == champion and r.role == role]
