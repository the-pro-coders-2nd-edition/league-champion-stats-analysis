"""Match download and record loading."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from league_stats_common.core.config import PlayerIdentity
from league_stats_common.core.models import MatchRecord
from league_stats_common.core.progress import STAGE_FETCHING, STAGE_PARSING
from league_stats_runner.infra.derived import KIND_RECORD, open_derived_store
from league_stats_runner.ingest.parser import BaseMatchFilter, ItemCatalog, MatchParser
from league_stats_runner.pipeline.services import PlayerContext, Services
from league_stats_common.utils import get_logger


@dataclass(frozen=True)
class FetchResult:
    """Resolved players plus match ids newly available after this fetch."""

    contexts: list[PlayerContext]
    new_match_ids: frozenset[str]


def _ranked_context_fields(ranked) -> tuple[str | None, str | None, int | None]:
    if ranked is None:
        return None, None, None
    tier = ranked.tier.upper()
    division = ranked.rank.upper() if ranked.rank else None
    return tier, division, ranked.league_points


def _resolve_player_context(services: Services, player: PlayerIdentity) -> PlayerContext:
    """Resolve PUUID and cache profile icon + solo/flex rank when available."""
    puuid = services.client.resolve_puuid(player.riot_id, player.tagline)
    icon_id = services.client.fetch_profile_icon_id(puuid)
    if icon_id is not None:
        services.assets.ensure_profile_icon(icon_id)
    queues = services.client.fetch_ranked_queues(puuid)
    solo_tier, solo_rank, solo_lp = _ranked_context_fields(queues.get("solo"))
    flex_tier, flex_rank, flex_lp = _ranked_context_fields(queues.get("flex"))
    for tier in (solo_tier, flex_tier):
        if tier:
            services.assets.ensure_rank_emblem(tier)
    return PlayerContext(
        riot_id=player.riot_id,
        tagline=player.tagline,
        puuid=puuid,
        profile_icon_id=icon_id,
        solo_tier=solo_tier,
        solo_rank=solo_rank,
        solo_lp=solo_lp,
        flex_tier=flex_tier,
        flex_rank=flex_rank,
        flex_lp=flex_lp,
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


def _catalog_fingerprint(raw_catalog: dict[str, Any]) -> str:
    """Short hash of the item catalog, so cached records follow item renames.

    Item names land in ``final_items`` / ``item_path``, so a Data Dragon bump
    changes parse output even though no code changed. Hashed once per run.
    """
    # Keys are sorted as strings because Data Dragon payloads mix int and str
    # keys, which sort_keys=True cannot order.
    ordered = sorted((str(key), value) for key, value in raw_catalog.items())
    digest = hashlib.sha256(json.dumps(ordered, default=str).encode())
    return digest.hexdigest()[:12]


def _record_key(match_id: str, puuid: str, catalog_key: str) -> str:
    return f"{match_id}|{puuid}|{catalog_key}"


def _with_account(record: MatchRecord, label: str | None) -> MatchRecord:
    """Stamp the configured Riot ID onto a record, if one was supplied."""
    return record.model_copy(update={"account": label}) if label else record


def load_all_records(
    services: Services,
    puuids: str | list[str],
    *,
    account_by_puuid: dict[str, str] | None = None,
) -> list[MatchRecord]:
    """Parse stored ranked queue games for one or more players.

    ``account_by_puuid`` fills in the configured Riot ID when the match payload
    omits ``riotIdGameName`` / ``riotIdTagline`` (older cached docs).

    Parsing is deterministic given the stored match, timeline and item catalog,
    so results are cached in the derived store: a re-run parses only the games it
    has not seen, instead of the whole history. The account label is applied
    *after* the cache so one cached record serves solo and group reports alike.
    """
    if isinstance(puuids, str):
        puuid_list = [puuids]
    else:
        puuid_list = list(puuids)
    labels = account_by_puuid or {}
    log = get_logger("pipeline")
    raw_catalog = services.client.fetch_item_catalog()
    catalog = ItemCatalog(raw_catalog)
    catalog_key = _catalog_fingerprint(raw_catalog)
    match_filter = BaseMatchFilter(services.config)
    parser = MatchParser(catalog)
    records: list[MatchRecord] = []
    hits = 0

    with open_derived_store() as derived:
        for puuid in puuid_list:
            match_ids = list(services.store.iter_match_ids(puuid))
            total = len(match_ids)
            label = labels.get(puuid)
            cached = derived.get_many(
                KIND_RECORD,
                [_record_key(match_id, puuid, catalog_key) for match_id in match_ids],
            )
            fresh: dict[str, Any] = {}
            for index, match_id in enumerate(match_ids, start=1):
                if index == 1 or index % 25 == 0 or index == total:
                    services.progress.update(
                        STAGE_PARSING,
                        current=index,
                        total=total,
                        detail=f"Parsing matches ({index}/{total})",
                    )
                key = _record_key(match_id, puuid, catalog_key)
                payload = cached.get(key)
                if payload is not None:
                    try:
                        records.append(
                            _with_account(MatchRecord.model_validate(payload), label)
                        )
                        hits += 1
                        continue
                    except Exception as exc:
                        log.warning("Discarding cached record %s: %s", match_id, exc)
                        derived.delete(KIND_RECORD, key)

                match = services.store.load_match(match_id)
                if not match:
                    continue
                if not match_filter.accept(match, puuid):
                    continue
                timeline = services.store.load_timeline(match_id)
                if not timeline:
                    continue
                try:
                    record = parser.parse(match, timeline, puuid)
                except Exception as exc:
                    log.warning("Failed to parse %s: %s", match_id, exc)
                    continue
                fresh[key] = record.model_dump(mode="json")
                records.append(_with_account(record, label))
            derived.put_many(KIND_RECORD, fresh)
        derived.evict_to_budget()

    records.sort(key=lambda r: r.game_creation_ms, reverse=True)
    log.info(
        "Parsed %d qualifying ranked queue games (%d from cache)", len(records), hits
    )
    return records


def group_records(records: list[MatchRecord], champion: str, role: str) -> list[MatchRecord]:
    """Filter parsed records to one champion + lane build."""
    return [r for r in records if r.champion == champion and r.role == role]
