"""FastAPI application: SPA hosting, JSON API, static report serving.

Generated reports remain plain files under ``output/`` (served at ``/out``);
the shared Data Dragon icon cache lives in its own volume under
``assets_dir`` (served read-only at ``/ddragon``, separate from ``/out``
since it is not tied to any individual job's lifecycle -- see
``AppConfig.assets_dir``'s field comment); the Svelte SPA (built to
``spa_dist/``) is served at ``/`` and talks to the job API and the Gemini
chat proxy defined here.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import threading
import time
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

import pymongo
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from starlette.middleware.gzip import GZipMiddleware
from starlette.routing import Match
from pydantic import BaseModel, Field

from league_stats_common.core.champions import (
    champion_display_name,
    champion_icon_id,
    normalize_role,
    parse_riot_id,
    player_slug,
    players_group_slug,
)
from league_stats_common.core.config import (
    VALID_PLATFORMS,
    VALID_REGIONS,
    PlayerIdentity,
    WebConfig,
    load_config,
    load_web_config,
)
from league_stats_common.infra.cache import HttpCache
from league_stats_common.infra.ddragon_assets import DDragonAssets
from league_stats_common.infra.mongo import db_name_from_uri
from league_stats_common.infra.riot_api import RiotApiClient, RiotApiError, shared_rate_limiter
from league_stats_runner.infra.raw_match_store import RawMatchStore
from league_stats_runner.pipeline.bundles import _overall_score_verdict
from league_stats_runner.pipeline.fetch import group_records, load_all_records, resolve_player_contexts
from league_stats_runner.pipeline.orchestrator import (
    _account_icon_hrefs,
    account_view_key,
    build_account_subset_views,
)
from league_stats_runner.pipeline.services import Services
from league_stats_runner.presentation.brand_assets import (
    APP_TITLE,
    ensure_brand_assets,
    refresh_saved_report_branding,
)
from league_stats_runner.presentation.report import (
    discover_player_builds,
    game_creation_ms_to_iso,
    is_group_player_label,
)
from league_stats_runner.presentation.report_json import prepare_web_report_payload
from league_stats_common.utils import (
    current_trace_id,
    get_logger,
    set_trace_id,
    setup_logging,
)
from league_stats_runner.analysis.career.models import BLOCK_SLOTS
from league_stats_common.infra.career_store import (
    build_key as career_build_key,
    open_career_store,
)
from league_stats_common.watch_fields import watch_public_fields
from league_stats_api_ui.chat import (
    OUTBOUND_RPC_DURATION,
    ChatError,
    gemini_reply,
    load_report_summary,
    resolve_chat_stats,
    validate_history,
)
from league_stats_common.infra.jobs import (
    JOB_KIND_ANALYZE,
    JOB_KIND_REGENERATE,
    JOB_KIND_REFRESH,
    JobStore,
    decode_players,
    players_label,
)
from league_stats_api_ui.job_events import JobEventBus
from league_stats_api_ui.notifying_job_store import open_notifying_jobs_store
from league_stats_api_ui.welcome_back_cache import WelcomeBackCache, WelcomeBackSubscriber
from league_stats_runner.worker import AnalysisWorker

log = get_logger("api_ui")

SPA_DIST_DIR = Path(__file__).resolve().parent / "spa_dist"

# Minimum ranked games a champion+lane needs before a report is generated.
MIN_GAMES_CHOICES: tuple[int, ...] = (5, 10, 15, 20, 25, 30, 50)

MAX_GROUP_PLAYERS = 8

# Responses at least this large are gzipped. Report payloads are megabytes; the
# job-status polls the report page makes every 3s are a few hundred bytes.
RESPONSE_COMPRESSION_MIN_BYTES = 4096

# API-UI's own Prometheus metrics -- RUNNER/CronWatch/PEERS each got theirs in
# earlier phases via a standalone `start_http_server` port (they are gRPC-only
# processes with no other HTTP surface); API-UI already runs one uvicorn HTTP
# server, so a `/metrics` route on it is the natural fit instead of a second,
# redundant HTTP listener. Module-level like RUNNER's `RUNNER_JOB_DURATION`/
# `RUNNER_JOBS_TOTAL`, so re-calling `create_app` (as tests do, once per test)
# does not re-register the same metric names.
HTTP_REQUEST_DURATION = Histogram(
    "api_ui_http_request_duration_seconds",
    "Time API-UI took to handle one HTTP request, labeled by method and route.",
    ["method", "route"],
)
HTTP_REQUESTS_TOTAL = Counter(
    "api_ui_http_requests_total",
    "API-UI HTTP requests that completed, labeled by method, route and status code.",
    ["method", "route", "status_code"],
)
HTTP_REQUESTS_IN_FLIGHT = Gauge(
    "api_ui_http_requests_in_flight",
    "HTTP requests API-UI is currently handling, labeled by route.",
    ["route"],
)


class AnalysisRequest(BaseModel):
    """Body of ``POST /api/analyses``.

    Accepts either a single ``riot_id`` (``Name#Tag`` or name + ``tagline``)
    or a ``players`` list to pool multiple accounts into one report group.
    """

    riot_id: str = Field(default="", max_length=64)
    tagline: str = Field(default="", max_length=16)
    players: list[str] = Field(default_factory=list, max_length=MAX_GROUP_PLAYERS)
    region: str = Field(default="euw1", max_length=16)
    min_games: int | None = Field(default=None, ge=1, le=200)


class ChatRequest(BaseModel):
    """Body of ``POST /api/chat``."""

    report: str = Field(min_length=3, max_length=200)
    history: list[Any]
    tab: str | None = Field(default=None, max_length=32)
    context: dict[str, Any] | None = None


class WatchRequest(BaseModel):
    """Optional body for ``POST /api/players/{slug}/watch``.

    ``interval_s`` is floored at 60 by the store: polling faster than that spends
    rate-limit budget the analysis jobs need, for no benefit on games that last
    25-35 minutes.
    """

    interval_s: int | None = Field(default=None, ge=60, le=3600)


class RefreshRequest(BaseModel):
    """Optional body for ``POST /api/players/{slug}/refresh``.

    When ``champion`` and ``role`` are set, only that build is re-analysed
    after fetching the latest matches; peers still run async for that build.
    """

    champion: str = Field(default="", max_length=64)
    role: str = Field(default="", max_length=32)


class AccountViewsRequest(BaseModel):
    """Body of ``POST /api/players/{slug}/builds/{build_slug}/account-views``."""

    accounts: list[str] = Field(min_length=1, max_length=MAX_GROUP_PLAYERS)


class CareerDropRequest(BaseModel):
    """Body of ``POST /api/players/{slug}/builds/{build_slug}/career/drop``."""

    slot: int = Field(ge=0, lt=BLOCK_SLOTS)


class RecapAckRequest(BaseModel):
    """Body of ``POST /api/players/{slug}/builds/{build_slug}/career/recap/ack``."""

    match_id: str = ""
    game_ms: int = 0
    hits: dict[str, int] = Field(default_factory=dict)
    track_key: str = ""


def _parse_player_entry(value: str, tagline: str = "") -> dict[str, str]:
    """Resolve one Riot ID input into ``{riot_id, tagline}``."""
    riot_id = value.strip()
    tag = tagline.strip().lstrip("#")
    if "#" in riot_id and not tag:
        riot_id, tag = parse_riot_id(riot_id)
    if not riot_id or not tag:
        raise ValueError("Provide a Riot ID and tagline (Name#Tag).")
    return {"riot_id": riot_id, "tagline": tag}


def _resolve_players(body: AnalysisRequest) -> list[dict[str, str]]:
    """Collect unique players from ``players`` and/or the single-player fields."""
    entries: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def _add(player: dict[str, str]) -> None:
        key = (player["riot_id"].casefold(), player["tagline"].casefold())
        if key in seen:
            return
        seen.add(key)
        entries.append(player)

    for raw in body.players:
        text = raw.strip()
        if not text:
            continue
        _add(_parse_player_entry(text))

    if body.riot_id.strip():
        _add(_parse_player_entry(body.riot_id, body.tagline))

    if not entries:
        raise ValueError("Provide at least one Riot ID as Name#Tag.")
    if len(entries) > MAX_GROUP_PLAYERS:
        raise ValueError(f"At most {MAX_GROUP_PLAYERS} players per group.")
    return entries


# Process-wide Mongo clients keyed by URI, mirroring `career_store.py`/
# `derived.py`/`jobs.py`/`worker.py`'s own `_SHARED_MONGO_CLIENTS`: this
# module's `_build_mongo_client` is reached on real per-request HTTP paths
# (`_verify_players_exist` on every `POST /api/analyses`, every in-process
# watch-poll tick, the per-champion refresh route) and previously opened a
# brand new, never-closed `pymongo.MongoClient` (with its own connection
# pool) on every single call -- an unbounded resource leak over this
# long-running process's lifetime. Caching by URI here fixes that.
_SHARED_MONGO_CLIENTS: dict[str, pymongo.MongoClient] = {}
_SHARED_MONGO_CLIENTS_LOCK = threading.Lock()


def _build_mongo_client(mongo_uri: str) -> pymongo.MongoClient:
    """Return the process-wide Mongo client for this URI, creating it once.

    A separate seam (rather than calling `pymongo.MongoClient` directly) so
    tests can monkeypatch this one function to return a `mongomock.MongoClient`
    instead of dialing a real Mongo -- matching the pattern
    `league_stats_runner/worker.py`'s own `_build_mongo_client` already uses.
    """
    with _SHARED_MONGO_CLIENTS_LOCK:
        client = _SHARED_MONGO_CLIENTS.get(mongo_uri)
        if client is None:
            client = pymongo.MongoClient(mongo_uri)
            _SHARED_MONGO_CLIENTS[mongo_uri] = client
        return client


def _build_precheck_client(
    region: str, output_dir: Path, web_config: WebConfig
) -> RiotApiClient:
    """Build a Riot client sharing cache + rate limiter with analysis jobs.

    Match/timeline storage moved from `MatchStore` (a local on-disk store) to
    `RawMatchStore` (Mongo) in Phase 8, Task 1 -- this client never uses any
    of `MatchStore`'s peer-game methods, only the raw match/timeline surface
    `RawMatchStore` already implements identically.
    """
    config = load_config(
        riot_id="precheck",
        tagline="PRE",
        region=region,
        output_dir=output_dir,
    )
    config.ensure_directories()
    mongo_client = _build_mongo_client(web_config.runner_mongo_uri)
    store = RawMatchStore(
        mongo_client, db_name=db_name_from_uri(web_config.runner_mongo_uri)
    )
    return RiotApiClient(
        config,
        HttpCache(config.http_cache_dir),
        store,
        limiter=shared_rate_limiter(
            config.requests_per_second, config.requests_per_two_minutes
        ),
    )


def _verify_players_exist(
    players: list[dict[str, str]],
    region: str,
    output_dir: Path,
    web_config: WebConfig,
) -> None:
    """Resolve each Riot ID via account-v1 before enqueueing.

    Raises:
        ValueError: When one or more Riot IDs are unknown for the region.
        RiotApiError: When the Riot API fails for a non-404 reason.
    """
    client = _build_precheck_client(region, output_dir, web_config)
    missing: list[str] = []
    t0 = time.monotonic()
    for index, player in enumerate(players, start=1):
        label = f"{player['riot_id']}#{player['tagline']}"
        start = time.perf_counter()
        log.info("Verifying player %d of %d: %s (%s)", index, len(players), label, region)
        try:
            client.resolve_puuid(player["riot_id"], player["tagline"])
        except RiotApiError as exc:
            OUTBOUND_RPC_DURATION.labels(
                target="riot_api", operation="resolve_puuid", outcome="error"
            ).observe(time.perf_counter() - start)
            message = str(exc)
            if "404" in message and "by-riot-id" in message:
                missing.append(label)
                continue
            raise
        else:
            OUTBOUND_RPC_DURATION.labels(
                target="riot_api", operation="resolve_puuid", outcome="ok"
            ).observe(time.perf_counter() - start)
    log.info(
        "Verified %d player(s) in %.1fs (%d missing)",
        len(players),
        time.monotonic() - t0,
        len(missing),
    )
    if not missing:
        return
    if len(missing) == 1:
        raise ValueError(
            f"Player {missing[0]} was not found on {region}. "
            "Check the Riot ID, tagline and region."
        )
    raise ValueError(
        f"Players not found on {region}: {', '.join(missing)}. "
        "Check each Riot ID, tagline and region."
    )


def _parse_players_label(label: str) -> list[dict[str, str]]:
    """Parse a comma-separated ``Name#Tag, Name2#Tag2`` group label."""
    entries: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for part in str(label or "").split(","):
        text = part.strip()
        if "#" not in text:
            continue
        name, tag = text.rsplit("#", 1)
        name, tag = name.strip(), tag.strip().lstrip("#")
        if not name or not tag:
            continue
        key = (name.casefold(), tag.casefold())
        if key in seen:
            continue
        seen.add(key)
        entries.append({"riot_id": name, "tagline": tag})
    return entries


def _slug_for_players(players: list[dict[str, Any]]) -> str:
    """Filesystem group slug for a tracked-player list."""
    return players_group_slug(
        [(str(player["riot_id"]), str(player["tagline"])) for player in players]
    )


def _merge_player_icons(
    primary: list[dict[str, Any]], *sources: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Copy profile icon + ranked queues onto ``primary`` from matching sources."""
    from league_stats_common.core.models import queue_rank_fields

    icons: dict[tuple[str, str], int] = {}
    ranks: dict[tuple[str, str], dict[str, Any]] = {}
    for source in sources:
        for player in source:
            key = (
                str(player.get("riot_id", "")).casefold(),
                str(player.get("tagline", "")).casefold(),
            )
            raw = player.get("profile_icon_id")
            if raw is not None:
                try:
                    icons[key] = int(raw)
                except (TypeError, ValueError):
                    pass
            for queue in ("solo", "flex"):
                queue_rank = queue_rank_fields(player, queue)
                if queue_rank:
                    stored = ranks.setdefault(key, {})
                    stored.update(queue_rank)
    merged: list[dict[str, Any]] = []
    for player in primary:
        entry: dict[str, Any] = {
            "riot_id": str(player["riot_id"]),
            "tagline": str(player["tagline"]),
        }
        key = (entry["riot_id"].casefold(), entry["tagline"].casefold())
        raw = player.get("profile_icon_id")
        if raw is not None:
            try:
                entry["profile_icon_id"] = int(raw)
            except (TypeError, ValueError):
                pass
        elif (icon := icons.get(key)) is not None:
            entry["profile_icon_id"] = icon
        stored_rank = ranks.get(key, {})
        for queue in ("solo", "flex"):
            own_queue = queue_rank_fields(player, queue)
            if own_queue:
                entry.update(own_queue)
            elif stored_queue := queue_rank_fields(stored_rank, queue):
                entry.update(stored_queue)
        merged.append(entry)
    return merged


def _players_from_meta(meta: dict[str, Any]) -> list[dict[str, Any]]:
    """Recover tracked players from on-disk report metadata.

    Prefers the structured ``players`` list. Older CLI group reports only stored
    a comma-separated ``player`` label plus the primary ``riot_id``/``tagline``;
    parse the label so refresh/regenerate keep every account in the group.
    """
    from league_stats_common.core.models import player_rank_fields

    raw_players = meta.get("players")
    if isinstance(raw_players, list) and raw_players:
        recovered: list[dict[str, Any]] = []
        for item in raw_players:
            if not isinstance(item, dict):
                continue
            name = str(item.get("riot_id", "")).strip()
            tag = str(item.get("tagline", "")).strip()
            if not name or not tag:
                continue
            entry: dict[str, Any] = {"riot_id": name, "tagline": tag}
            raw_icon = item.get("profile_icon_id")
            if raw_icon is not None:
                try:
                    entry["profile_icon_id"] = int(raw_icon)
                except (TypeError, ValueError):
                    pass
            entry.update(player_rank_fields(item))
            recovered.append(entry)
        if recovered:
            return recovered

    from_label = _parse_players_label(str(meta.get("player", "")))
    if len(from_label) > 1:
        return from_label

    riot_id = str(meta.get("riot_id", "")).strip()
    tagline = str(meta.get("tagline", "")).strip()
    if riot_id and tagline:
        return [{"riot_id": riot_id, "tagline": tagline}]
    if from_label:
        return from_label
    return []


def _resolve_tracked_players(
    slug: str,
    *,
    store_players: list[dict[str, Any]] | None = None,
    meta: dict[str, Any] | None = None,
    metas: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Pick the best tracked-player list for a report slug.

    Prefers candidates whose ``players_group_slug`` matches ``slug`` (so a stale
    single-player registry row cannot override a multi-player on-disk group).
    """
    candidates: list[list[dict[str, Any]]] = []
    meta_list = metas if metas is not None else ([meta] if meta is not None else [])
    for meta_item in meta_list:
        from_meta = _players_from_meta(meta_item)
        if from_meta:
            candidates.append(from_meta)
    if store_players:
        candidates.append(list(store_players))

    if not candidates:
        return []

    matching = [
        candidate
        for candidate in candidates
        if _slug_for_players(candidate) == slug
    ]
    pool = matching or candidates
    best = max(pool, key=len)
    return _merge_player_icons(best, *candidates)


def _tracked_region(
    *,
    player: dict[str, Any] | None,
    builds: list[dict[str, Any]],
) -> str:
    if player and player.get("region"):
        return str(player["region"])
    for build in builds:
        region = build.get("region")
        if region:
            return str(region)
    return ""


def _hydrate_tracked_ranks(
    tracked: list[dict[str, Any]],
    *,
    region: str,
    output_dir: Path,
    web_config: WebConfig,
) -> tuple[list[dict[str, Any]], bool]:
    """Fill missing flex ranks from Riot when the local cache is stale."""
    from league_stats_common.core.models import queue_rank_fields
    from league_stats_runner.pipeline.fetch import _ranked_context_fields

    log = get_logger("web.ranks")

    if not tracked or not region.strip():
        return tracked, False
    if not any(not queue_rank_fields(player, "flex") for player in tracked):
        return tracked, False

    try:
        client = _build_precheck_client(region, output_dir, web_config)
    except ValueError as exc:
        log.info("Skipping flex rank refresh (no Riot API key): %s", exc)
        return tracked, False
    except Exception:
        log.exception("Could not build Riot client for flex rank refresh")
        return tracked, False

    changed = False
    hydrated: list[dict[str, Any]] = []
    for player in tracked:
        entry = dict(player)
        if queue_rank_fields(player, "flex"):
            hydrated.append(entry)
            continue
        label = f"{player.get('riot_id')}#{player.get('tagline')}"
        try:
            puuid = client.resolve_puuid(str(player["riot_id"]), str(player["tagline"]))
            queues = client.fetch_ranked_queues(puuid)
        except RiotApiError as exc:
            log.warning("Could not refresh flex rank for %s: %s", label, exc)
            hydrated.append(entry)
            continue
        flex = queues.get("flex")
        if flex is None:
            log.info("No flex rank entry for %s", label)
            hydrated.append(entry)
            continue
        tier, division, lp = _ranked_context_fields(flex)
        if not tier:
            hydrated.append(entry)
            continue
        entry["flex_tier"] = tier
        if division:
            entry["flex_rank"] = division
        if lp is not None:
            entry["flex_lp"] = lp
        changed = True
        hydrated.append(entry)
    return hydrated, changed


def _player_label_from_tracked(tracked: list[dict[str, Any]], slug: str) -> str:
    """Display label for resolved tracked players."""
    if tracked:
        return players_label(tracked)
    return slug


def _job_public(store: JobStore, job: dict[str, Any] | None) -> dict[str, Any] | None:
    """Shape a job row for API responses."""
    if job is None:
        return None
    public = {
        "id": job["id"],
        "kind": job["kind"],
        "player_slug": job["player_slug"],
        "state": job["state"],
        "stage_detail": job["stage_detail"],
        "stage_current": job["stage_current"],
        "stage_total": job["stage_total"],
        "error": job["error"],
        "created_at": job["created_at"],
        "started_at": job["started_at"],
        "finished_at": job["finished_at"],
        "filter_champion": job.get("filter_champion") or None,
        "filter_role": job.get("filter_role") or None,
        "min_games": job.get("min_games"),
    }
    position = store.queue_position(int(job["id"]))
    public["queue_position"] = position
    public["eta_s"] = (
        (position + 1) * store.average_duration_s() if position is not None else None
    )
    return public


def _ddragon_asset_href(assets_dir: Path, *parts: str) -> str | None:
    """Absolute ``/ddragon/...`` URL when the cached icon exists on disk."""
    path = assets_dir.joinpath(*parts)
    if not path.is_file():
        return None
    return "/ddragon/" + "/".join(parts)


def _web_asset_href(output_dir: Path, *parts: str) -> str | None:
    """Absolute ``/out/...`` URL when the asset exists on disk."""
    path = output_dir.joinpath(*parts)
    if not path.is_file():
        return None
    return "/out/" + "/".join(parts)


def _shape_queue_rank(
    entry: dict[str, Any],
    player: dict[str, Any],
    queue: str,
    output_dir: Path,
    *,
    apex_tiers: frozenset[str],
) -> None:
    """Attach shaped rank fields for one queue onto an API player entry."""
    from league_stats_common.core.models import (
        format_rank_division,
        format_solo_rank_label,
        queue_rank_fields,
    )
    from league_stats_common.infra.ddragon_assets import fetch_rank_emblem

    rank = queue_rank_fields(player, queue)
    if not rank:
        return
    tier = str(rank[f"{queue}_tier"])
    division = str(rank.get(f"{queue}_rank") or "")
    lp = rank.get(f"{queue}_lp")
    entry[f"{queue}_rank_label"] = format_solo_rank_label(tier, division, lp)
    entry[f"{queue}_rank_division"] = format_rank_division(
        tier, division, apex_tiers=apex_tiers
    )
    entry[f"{queue}_lp"] = lp
    emblem = fetch_rank_emblem(output_dir / "assets" / "ranks", tier)
    if emblem is not None:
        entry[f"{queue}_rank_icon"] = _web_asset_href(
            output_dir, "assets", "ranks", emblem.name
        )


def _shaped_players(
    assets_dir: Path,
    output_dir: Path,
    tracked: list[dict[str, Any]],
    *,
    region: str = "",
) -> list[dict[str, Any]]:
    """API/template shape for group members (label + optional icon/rank)."""
    apex_tiers = frozenset({"MASTER", "GRANDMASTER", "CHALLENGER"})
    region_label = str(region or "").upper()
    shaped: list[dict[str, Any]] = []
    for player in tracked:
        riot_id = str(player["riot_id"])
        tagline = str(player["tagline"])
        icon_href = None
        raw_icon = player.get("profile_icon_id")
        if raw_icon is not None:
            try:
                icon_href = _ddragon_asset_href(
                    assets_dir, "profile_icons", f"{int(raw_icon)}.png"
                )
            except (TypeError, ValueError):
                icon_href = None
        entry: dict[str, Any] = {
            "riot_id": riot_id,
            "tagline": tagline,
            "label": f"{riot_id}#{tagline}",
            "profile_icon": icon_href,
        }
        if region_label:
            entry["region"] = region_label
        for queue in ("solo", "flex"):
            _shape_queue_rank(
                entry, player, queue, output_dir, apex_tiers=apex_tiers
            )
        shaped.append(entry)
    return shaped


def _profile_icon_hrefs(
    assets_dir: Path,
    players: list[dict[str, Any]] | None = None,
    *,
    primary_icon_id: int | None = None,
) -> list[str]:
    """Resolve cached profile-icon URLs for home-page cards."""
    hrefs: list[str] = []
    seen: set[int] = set()
    for player in players or []:
        raw = player.get("profile_icon_id")
        if raw is None:
            continue
        try:
            icon_id = int(raw)
        except (TypeError, ValueError):
            continue
        if icon_id in seen:
            continue
        href = _ddragon_asset_href(assets_dir, "profile_icons", f"{icon_id}.png")
        if href:
            hrefs.append(href)
            seen.add(icon_id)
    if not hrefs and primary_icon_id is not None:
        href = _ddragon_asset_href(
            assets_dir, "profile_icons", f"{int(primary_icon_id)}.png"
        )
        if href:
            hrefs.append(href)
    return hrefs


def _last_game_at_from_report(report: dict[str, Any]) -> str:
    """Newest match timestamp embedded in a saved report payload."""
    latest_ms = 0
    review = report.get("game_review") or {}
    if isinstance(review, dict):
        for bundle in review.values():
            if not isinstance(bundle, dict):
                continue
            for game in bundle.get("games") or []:
                if not isinstance(game, dict):
                    continue
                ms = int(game.get("game_creation_ms") or 0)
                if ms > latest_ms:
                    latest_ms = ms
    if latest_ms > 0:
        return game_creation_ms_to_iso(latest_ms)
    return str(report.get("generated_at") or "")


def _hub_build_fields(meta: dict[str, Any], report_dir: Path) -> dict[str, Any]:
    """Score and last-played fields for player-hub build cards."""
    score = meta.get("score")
    score_color = str(meta.get("score_color") or "")
    score_verdict_label = str(meta.get("score_verdict_label") or "")
    last_game_at = str(meta.get("last_game_at") or "")
    needs_report = (
        score is None
        or not score_color
        or not score_verdict_label
        or not last_game_at
    )
    if needs_report:
        report_path = report_dir / "report.json"
        if report_path.is_file():
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                report = {}
            if score is None:
                score = report.get("score")
            if not score_color:
                score_color = str(report.get("score_color") or "")
            if not score_verdict_label:
                score_verdict_label = str(report.get("score_verdict_label") or "")
            if not last_game_at:
                last_game_at = _last_game_at_from_report(report)
    if not last_game_at:
        last_game_at = str(meta.get("generated_at") or "")
    if score is not None:
        try:
            numeric = float(score)
            if math.isfinite(numeric):
                score_color, score_verdict_label = _overall_score_verdict(numeric)
        except (TypeError, ValueError):
            pass
    return {
        "score": score,
        "score_color": score_color,
        "score_verdict_label": score_verdict_label,
        "last_game_at": last_game_at,
    }


def _player_builds(output_dir: Path, assets_dir: Path, slug: str) -> list[dict[str, Any]]:
    """On-disk builds for one player, with web hrefs and icon URLs."""
    builds = discover_player_builds(output_dir / "reports" / slug)
    shaped: list[dict[str, Any]] = []
    for build in builds:
        build_slug = str(build.get("href", "")).split("/", 1)[0]
        champion_id = str(build.get("champion", ""))
        role = str(build.get("role", ""))
        report_dir = output_dir / "reports" / slug / build_slug
        hub = _hub_build_fields(build, report_dir)
        shaped.append(
            {
                "slug": build_slug,
                "player": build.get("player", ""),
                "champion": champion_display_name(champion_id),
                "role": role,
                "role_display": build.get("role_display", ""),
                "build_label": build.get("build_label", ""),
                "games": build.get("games", 0),
                "winrate": build.get("winrate"),
                "generated_at": build.get("generated_at", ""),
                "last_game_at": hub["last_game_at"],
                "score": hub["score"],
                "score_color": hub["score_color"],
                "score_verdict_label": hub["score_verdict_label"],
                "peers_ready": _build_peers_ready(build, report_dir),
                "href": f"/out/reports/{slug}/{build.get('href', '')}",
                "champion_icon": _ddragon_asset_href(
                    assets_dir,
                    "champions",
                    f"{champion_icon_id(champion_id)}.png",
                ),
                "role_icon": _ddragon_asset_href(assets_dir, "roles", f"{role}.png"),
            }
        )
    return shaped


def _preview_builds(
    output_dir: Path,
    assets_dir: Path,
    slug: str,
    builds: list[dict[str, Any]],
    *,
    limit: int = 6,
) -> list[dict[str, Any]]:
    """Most recently played builds first, for home-library champion portraits."""
    ranked: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for build in builds:
        build_slug = str(build.get("href", "")).split("/", 1)[0]
        report_dir = output_dir / "reports" / slug / build_slug
        hub = _hub_build_fields(build, report_dir)
        sort_at = str(hub.get("last_game_at") or build.get("generated_at") or "")
        ranked.append((sort_at, build, hub))
    ranked.sort(key=lambda item: item[0], reverse=True)

    shaped: list[dict[str, Any]] = []
    for _, build, hub in ranked[:limit]:
        build_slug = str(build.get("href", "")).split("/", 1)[0]
        champion_id = str(build.get("champion", ""))
        shaped.append(
            {
                "slug": build_slug,
                "champion": champion_display_name(champion_id),
                "role": str(build.get("role", "")),
                "games": build.get("games", 0),
                "winrate": build.get("winrate"),
                "last_game_at": hub["last_game_at"],
                "champion_icon": _ddragon_asset_href(
                    assets_dir,
                    "champions",
                    f"{champion_icon_id(champion_id)}.png",
                ),
            }
        )
    return shaped


def _is_report_slug(value: str) -> bool:
    """Whether a path segment is safe to use in a reports-dir filesystem path."""
    return bool(value) and all(ch.isalnum() or ch == "_" for ch in value)


def _build_peers_ready(meta: dict[str, Any], report_dir: Path) -> bool:
    """Whether this build's on-disk report already includes peer comparison."""
    if "has_peer_comparison" in meta:
        return bool(meta.get("has_peer_comparison"))
    # Legacy reports written before the meta flag: infer from peer export.
    return (report_dir / "rank_comparison.csv").is_file()


def _report_groups(
    reports_dir: Path, assets_dir: Path, store: JobStore | None = None
) -> list[dict[str, Any]]:
    """Player cards for the landing page from on-disk report metadata.

    When ``store`` is provided, cards are marked ``busy`` if that player has an
    active (queued/running) job, and players with an active job but no report
    yet are included so queued first-time analyses appear on the home page.
    """
    output_dir = reports_dir.parent
    groups: list[dict[str, Any]] = []
    seen: set[str] = set()
    if reports_dir.is_dir():
        for player_dir in sorted(reports_dir.iterdir()):
            if not player_dir.is_dir():
                continue
            builds = discover_player_builds(player_dir)
            if not builds:
                continue
            meta = builds[0]
            store_players = None
            if store is not None:
                row = store.get_player(player_dir.name)
                if row and row.get("players"):
                    store_players = list(row["players"])
            tracked = _resolve_tracked_players(
                player_dir.name, store_players=store_players, meta=meta
            )
            label = _player_label_from_tracked(
                tracked, str(meta.get("player", player_dir.name))
            )
            primary_icon = meta.get("profile_icon_id")
            try:
                primary_icon_id = int(primary_icon) if primary_icon is not None else None
            except (TypeError, ValueError):
                primary_icon_id = None
            shaped = _shaped_players(assets_dir, output_dir, tracked)
            if not shaped and label:
                shaped = [{"label": label, "profile_icon": None}]
            elif not shaped and primary_icon_id is not None:
                icons = _profile_icon_hrefs(
                    assets_dir, None, primary_icon_id=primary_icon_id
                )
                shaped = [
                    {
                        "label": str(meta.get("player", player_dir.name)),
                        "profile_icon": icons[0] if icons else None,
                    }
                ]
            seen.add(player_dir.name)
            groups.append(
                {
                    "slug": player_dir.name,
                    "player": label,
                    "players": shaped,
                    "is_group": len(tracked) > 1 or is_group_player_label(label),
                    "build_count": len(builds),
                    "total_games": sum(int(build.get("games", 0)) for build in builds),
                    "preview_builds": _preview_builds(
                        output_dir, assets_dir, player_dir.name, builds
                    ),
                    "last_updated": max(
                        (str(build.get("generated_at", "")) for build in builds),
                        default="",
                    ),
                    "busy": False,
                    "job_state": None,
                    "has_report": True,
                }
            )

    if store is not None:
        active_by_slug = {
            str(job["player_slug"]): job for job in store.list_active_jobs()
        }
        for group in groups:
            row = store.get_player(group["slug"])
            group.update(watch_public_fields(row or {}))
            job = active_by_slug.get(group["slug"])
            if job is None:
                continue
            group["busy"] = True
            group["job_state"] = job.get("state")
        for slug, job in active_by_slug.items():
            if slug in seen:
                continue
            tracked = list(job.get("players") or [])
            row = store.get_player(slug)
            if row and row.get("players"):
                tracked = list(row["players"])
            label = (
                players_label(tracked)
                if tracked
                else f"{job['riot_id']}#{job['tagline']}"
            )
            shaped = _shaped_players(assets_dir, output_dir, tracked)
            if not shaped:
                shaped = [{"label": label, "profile_icon": None}]
            groups.append(
                {
                    "slug": slug,
                    "player": label,
                    "players": shaped,
                    "is_group": len(tracked) > 1 or is_group_player_label(label),
                    "build_count": 0,
                    "total_games": 0,
                    "preview_builds": [],
                    "last_updated": "",
                    "busy": True,
                    "job_state": job.get("state"),
                    "has_report": False,
                }
            )

    busy = [group for group in groups if group.get("busy")]
    idle = [group for group in groups if not group.get("busy")]
    idle.sort(key=lambda group: group.get("last_updated") or "", reverse=True)
    return busy + idle


def _route_template_for(request: Request) -> str:
    """Return the matched route's path template, resolved independently of
    Starlette's own dispatch (see `record_http_metrics`'s docstring for why
    this can't just wait for `request.scope["route"]`).

    Falls back to the raw path when nothing matches (e.g. a genuine 404) --
    that path is at most one of the SPA catch-all's non-matches, not an
    arbitrary user-controlled value repeated across many distinct labels, so
    it does not reintroduce the cardinality risk this exists to avoid.
    """
    for route in request.app.router.routes:
        match, _ = route.matches(request.scope)
        if match == Match.FULL:
            return getattr(route, "path", request.url.path)
    return request.url.path


def create_app(
    web_config: WebConfig | None = None, *, start_worker: bool = True
) -> FastAPI:
    """Build the FastAPI application (worker optional, for tests)."""
    setup_logging(service="api-ui", version=os.environ.get("GIT_COMMIT", "dev"))
    config = web_config or load_web_config()
    config.output_dir.mkdir(parents=True, exist_ok=True)
    config.reports_dir.mkdir(parents=True, exist_ok=True)
    # DDragon icon cache: a separate volume from output_dir (see
    # `WebConfig.assets_dir`'s field comment). RUNNER is the only writer;
    # api-ui only ever mounts/reads it, but still needs it to exist before the
    # `/ddragon` StaticFiles mount below, e.g. on a fresh dev checkout that
    # never ran RUNNER first.
    config.assets_dir.mkdir(parents=True, exist_ok=True)
    ensure_brand_assets(config.output_dir)
    refresh_saved_report_branding(config.output_dir)

    # SSE relay (see the SSE migration design doc): `NotifyingJobStore` publishes to
    # `job_event_bus` on every state-changing call, `AnalysisWorker`'s worker threads
    # and `WelcomeBackSubscriber`'s event-loop task both write through it. The bus
    # itself is inert until `bind_loop()` runs in the lifespan below (it needs a
    # running event loop, only available once uvicorn/TestClient starts one).
    job_event_bus = JobEventBus()
    store = open_notifying_jobs_store(job_event_bus)
    worker = AnalysisWorker(store, config)
    # Always created (cheap, gRPC-free) so a later task can read from it
    # unconditionally; only the subscriber below is gated on the env var.
    welcome_back_cache = WelcomeBackCache()
    welcome_back_subscriber = (
        WelcomeBackSubscriber(welcome_back_cache, config.cron_watch_grpc_target, job_event_bus)
        if config.cron_watch_grpc_target
        else None
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # Always bound, regardless of `start_worker`: `TestClient` runs this lifespan
        # even for tests that pass `start_worker=False`, and SSE endpoint tests need
        # `publish()` to actually reach subscribers.
        job_event_bus.bind_loop(asyncio.get_running_loop())
        store.recover_orphans()
        if start_worker:
            worker.start()
            # No-op unless CRON_WATCH_GRPC_TARGET is set (see WebConfig's field
            # comment): welcome_back_subscriber is None by default, so nothing
            # opens a gRPC channel and this whole subsystem stays inert.
            if welcome_back_subscriber is not None:
                welcome_back_subscriber.start()
        yield
        if start_worker:
            if welcome_back_subscriber is not None:
                await welcome_back_subscriber.stop()
            worker.stop()
        store.close()

    app = FastAPI(title=APP_TITLE, lifespan=lifespan)
    app.state.job_store = store
    app.state.job_event_bus = job_event_bus
    app.state.web_config = config
    app.state.welcome_back_cache = welcome_back_cache

    # Report payloads are the largest thing this app serves by orders of magnitude,
    # and nothing compressed them: a request offering `gzip, br` got back tens of MB
    # with no content-encoding at all. Plotly's numeric arrays are high-entropy, so
    # gzip only manages ~1.8x here (measured; brotli reaches 2.5x and zstd 2.9x but
    # both need a dependency this app does not have). The threshold keeps small JSON
    # replies -- the status polling the report page does every 3s -- uncompressed,
    # where framing bytes and CPU would cost more than they save.
    app.add_middleware(GZipMiddleware, minimum_size=RESPONSE_COMPRESSION_MIN_BYTES)

    @app.middleware("http")
    async def revalidate_report_stylesheets(request: Request, call_next):  # type: ignore[no-untyped-def]
        """Force CSS under /out to revalidate so HTML/CSS versions cannot skew."""
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/out/") and path.endswith(".css"):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response

    @app.middleware("http")
    async def originate_trace_id(request: Request, call_next):  # type: ignore[no-untyped-def]
        """Mint a trace_id for every incoming request that doesn't already carry one.

        Registered last so Starlette makes it the outermost middleware (it runs
        before every other middleware/handler) -- a user-initiated action gets a
        trace_id from the very start of API-UI's own request handling, the same
        "mint if absent" rule the gRPC server interceptor (`trace_context.py`)
        uses for calls with no upstream trace_id.
        """
        trace_id = request.headers.get("x-trace-id") or uuid.uuid4().hex
        set_trace_id(trace_id)
        response = await call_next(request)
        response.headers["X-Trace-Id"] = trace_id
        return response

    @app.middleware("http")
    async def record_http_metrics(request: Request, call_next):  # type: ignore[no-untyped-def]
        """Record HTTP_REQUEST_DURATION/HTTP_REQUESTS_TOTAL/HTTP_REQUESTS_IN_FLIGHT
        around every request.

        Registered after `originate_trace_id`, making this the new outermost
        middleware (Starlette: last registered = outermost). Deliberate: a
        request-duration metric should reflect the full lifecycle a client
        experiences, including trace_id origination overhead, not just the
        inner handler chain -- there is no dependency in the other direction
        (this middleware never reads trace_id), so ordering here is purely
        about duration scope, not correctness.

        The route template (not the raw interpolated path) must be known
        *before* `call_next` returns for `HTTP_REQUESTS_IN_FLIGHT` to be safe
        to label by -- `request.scope["route"]` is only populated by
        Starlette's router as a side effect of dispatching the request, which
        happens inside `call_next`, too late to label the increment without
        risking an unbounded raw path (e.g. `/api/players/{slug}` with a real
        slug) as a gauge label. `_route_template_for` resolves the match
        itself, against the same fixed ~19-route table, before `call_next`
        runs, so both the increment and the eventual decrement/duration/count
        use one consistent, bounded route template.
        """
        route_path = _route_template_for(request)
        start = time.perf_counter()
        status_code = 500
        HTTP_REQUESTS_IN_FLIGHT.labels(route=route_path).inc()
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            HTTP_REQUESTS_IN_FLIGHT.labels(route=route_path).dec()
            HTTP_REQUEST_DURATION.labels(
                method=request.method, route=route_path
            ).observe(time.perf_counter() - start)
            HTTP_REQUESTS_TOTAL.labels(
                method=request.method, route=route_path, status_code=str(status_code)
            ).inc()

    app.mount("/out", StaticFiles(directory=str(config.output_dir), html=True), name="out")
    # Read-only on api-ui's side of the mount (RUNNER is the only writer, per
    # `docker-compose.yml`'s `ddragon-assets` volume comment); served under
    # its own prefix, not nested under `/out`, since this cache is not tied to
    # any individual job's lifecycle the way report artifacts are.
    app.mount("/ddragon", StaticFiles(directory=str(config.assets_dir)), name="ddragon")

    # ------------------------------------------------------------------ pages

    # -------------------------------------------------------------------- API

    @app.post("/api/analyses")
    def submit_analysis(body: AnalysisRequest) -> dict[str, Any]:
        try:
            tracked = _resolve_players(body)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        region = body.region.strip().lower()
        if region not in VALID_PLATFORMS and region not in VALID_REGIONS:
            raise HTTPException(status_code=422, detail=f"Unknown region {region!r}.")

        min_games = body.min_games
        if min_games is not None and min_games not in MIN_GAMES_CHOICES:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"min_games must be one of {', '.join(str(v) for v in MIN_GAMES_CHOICES)}."
                ),
            )

        try:
            _verify_players_exist(tracked, region, config.output_dir, config)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RiotApiError as exc:
            raise HTTPException(
                status_code=502,
                detail="Could not verify Riot ID with Riot API. Try again shortly.",
            ) from exc

        primary = tracked[0]
        slug = players_group_slug([(p["riot_id"], p["tagline"]) for p in tracked])
        store.upsert_player(
            slug=slug,
            riot_id=primary["riot_id"],
            tagline=primary["tagline"],
            region=region,
            players=tracked,
        )
        job, created = store.enqueue(
            kind=JOB_KIND_ANALYZE,
            riot_id=primary["riot_id"],
            tagline=primary["tagline"],
            region=region,
            player_slug=slug,
            players=tracked,
            min_games=min_games,
            trace_id=current_trace_id(),
        )
        return {
            "job": _job_public(store, job),
            "created": created,
            "player_slug": slug,
            "has_report": bool(_player_builds(config.output_dir, config.assets_dir, slug)),
        }

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: int) -> dict[str, Any]:
        job = store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Unknown job")
        return {"job": _job_public(store, job)}

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: int) -> dict[str, Any]:
        """Cancel a queued or in-progress job. Existing base reports are kept."""
        job = store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Unknown job")
        cancelled = store.cancel(job_id)
        if cancelled is None:
            raise HTTPException(
                status_code=409,
                detail="Job is already finished and cannot be cancelled.",
            )
        return {"job": _job_public(store, cancelled)}

    @app.get("/api/groups")
    def groups() -> dict[str, Any]:
        """Report groups for the landing page (same shape as the ``groups`` template var)."""
        return {"groups": _report_groups(config.reports_dir, config.assets_dir, store)}

    def _activity_payload() -> dict[str, Any]:
        """Active jobs for the landing-page status dots.

        Shared by the plain `GET /api/activity` and `GET /api/activity/events`
        (SSE) handlers below -- unlike `_player_status_payload`, this has no
        consume-on-read state, so both call it directly with no single-flight
        wrapper needed.
        """
        items: list[dict[str, Any]] = []
        for job in store.list_active_jobs():
            slug = str(job["player_slug"])
            tracked = list(job.get("players") or [])
            row = store.get_player(slug)
            if row and row.get("players"):
                tracked = list(row["players"])
            label = (
                players_label(tracked)
                if tracked
                else f"{job['riot_id']}#{job['tagline']}"
            )
            shaped = _shaped_players(config.assets_dir, config.output_dir, tracked)
            if not shaped:
                shaped = [{"label": label, "profile_icon": None}]
            items.append(
                {
                    "slug": slug,
                    "player_label": label,
                    "players": shaped,
                    "state": job.get("state"),
                    "has_report": bool(
                        _player_builds(config.output_dir, config.assets_dir, slug)
                    ),
                }
            )
        return {"items": items}

    @app.get("/api/activity")
    def activity() -> dict[str, Any]:
        """Active jobs for the landing-page status dots."""
        return _activity_payload()

    def _player_status_payload(slug: str) -> dict[str, Any] | None:
        """Player/job status, including the welcome-back payload if any.

        `None` means "unknown player" (the caller turns that into a 404).
        Shared by the plain `GET /api/players/{slug}` and
        `GET /api/players/{slug}/events` (SSE) handlers below -- the plain GET
        handler calls this directly (independent computation per request,
        matching its current, already-accepted behavior); the SSE handler
        calls it through `_player_status_snapshot`'s single-flight wrapper
        instead (see that function's docstring for why).

        The `welcome_back` field is consumed-on-read (`WelcomeBackCache.get`
        pops it), so a caller must never cache or coalesce results across
        distinct logical reads: a second reader (a duplicate tab, a
        prefetch, a caching proxy) would silently eat the one delivery a
        genuine poller was waiting for.
        """
        player = store.get_player(slug)
        builds = _player_builds(config.output_dir, config.assets_dir, slug)
        if player is None and not builds:
            return None
        active = store.active_job_for_player(slug)
        meta_builds = discover_player_builds(config.reports_dir / slug)
        tracked = _resolve_tracked_players(
            slug,
            store_players=(player.get("players") if player else None),
            metas=meta_builds or None,
        )
        label = _player_label_from_tracked(tracked, slug)
        if not tracked and builds:
            label = str(builds[0].get("player") or slug)
        region = _tracked_region(player=player, builds=builds)
        if region and tracked:
            tracked, ranks_updated = _hydrate_tracked_ranks(
                tracked,
                region=region,
                output_dir=config.output_dir,
                web_config=config,
            )
            if ranks_updated and player is not None:
                primary = tracked[0]
                store.upsert_player(
                    slug=slug,
                    riot_id=str(primary["riot_id"]),
                    tagline=str(primary["tagline"]),
                    region=str(player["region"]),
                    players=tracked,
                )
        region_label = region.upper() if region else ""
        return {
            "slug": slug,
            "player_label": label,
            "players": _shaped_players(
                config.assets_dir, config.output_dir, tracked, region=region_label
            ),
            "active_job": _job_public(store, active),
            "builds": builds,
            "has_report": bool(builds),
            "peer_failed": bool(player["peer_failed"]) if player else False,
            "base_completed_at": player["base_completed_at"] if player else None,
            "peer_completed_at": player["peer_completed_at"] if player else None,
            "can_watch": player is not None,
            "welcome_back": welcome_back_cache.get(slug),
            **watch_public_fields(player or {}),
        }

    @app.get("/api/players/{slug}")
    def player_status(slug: str, response: Response) -> dict[str, Any]:
        """Player/job status, including the welcome-back payload if any.

        See `_player_status_payload`'s docstring for the consume-on-read caveat.
        """
        response.headers["Cache-Control"] = "no-store"
        payload = _player_status_payload(slug)
        if payload is None:
            raise HTTPException(status_code=404, detail="Unknown player")
        return payload

    # ---------------------------------------------------------- SSE (events)

    # Per-slug single-flight guard for `_player_status_payload`, scoped to this
    # `create_app` call (fresh per app instance, same lifetime as `job_event_bus`) --
    # see `_player_status_snapshot` below for why this exists at all.
    _player_status_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
    _player_status_cache: dict[str, tuple[int, dict[str, Any] | None]] = {}

    async def _player_status_snapshot(slug: str) -> dict[str, Any] | None:
        """Single-flight `_player_status_payload(slug)` across sibling SSE subscribers.

        `WelcomeBackCache.get` is consume-on-read: if two browser tabs are open on
        the same slug and both independently recomputed their own payload on the
        same bus wake-up, only the first to run would see a pending welcome-back
        payload; the second would get `None`. Memoized by `job_event_bus`'s
        per-slug generation counter (bumped once per `publish(slug)` call): the
        first subscriber task to wake for a given `(slug, generation)` pair
        computes the payload; any sibling woken by that same publish reuses it
        instead of recomputing. The next publish bumps the generation, forcing a
        fresh computation. Run off the event loop thread (`asyncio.to_thread`)
        since `_player_status_payload` does blocking Mongo I/O, mirroring why the
        plain GET routes in this app are defined as `def`, not `async def`
        (FastAPI dispatches those through a threadpool automatically; this SSE
        route is `async def` so it can `await bus.subscribe(...)`, so the
        blocking call needs an explicit hop instead).
        """
        generation = job_event_bus.generation(slug)
        async with _player_status_locks[slug]:
            cached = _player_status_cache.get(slug)
            if cached is not None and cached[0] == generation:
                return cached[1]
            payload = await asyncio.to_thread(_player_status_payload, slug)
            _player_status_cache[slug] = (generation, payload)
            return payload

    @app.get("/api/players/{slug}/events")
    async def player_status_events(slug: str) -> StreamingResponse:
        """SSE stream of `_player_status_payload(slug)`, same shape as the plain GET.

        Subscribes to `job_event_bus` before computing anything (see
        `JobEventBus.subscribe`'s docstring: registration happens synchronously,
        so a publish landing between subscribing and the first snapshot cannot be
        missed), sends one snapshot immediately, then a fresh one on every
        subsequent wake-up. A client disconnect cancels this generator; the
        `finally:` in `JobEventBus.subscribe` handles cleanup with no extra code
        here.
        """
        updates = await job_event_bus.subscribe(slug)
        initial = await _player_status_snapshot(slug)
        if initial is None:
            raise HTTPException(status_code=404, detail="Unknown player")

        async def event_stream() -> AsyncIterator[str]:
            yield f"data: {json.dumps(initial)}\n\n"
            async for _ in updates:
                payload = await _player_status_snapshot(slug)
                if payload is not None:
                    yield f"data: {json.dumps(payload)}\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @app.get("/api/activity/events")
    async def activity_events() -> StreamingResponse:
        """SSE stream of `_activity_payload()`, same shape as the plain GET.

        Subscribes to the wildcard topic (`slug=None`): every job-state publish,
        for any slug, wakes this stream, matching `/api/activity`'s "every active
        job" scope. No single-flight needed here (no consume-on-read state).
        """
        updates = await job_event_bus.subscribe(None)

        async def event_stream() -> AsyncIterator[str]:
            yield f"data: {json.dumps(await asyncio.to_thread(_activity_payload))}\n\n"
            async for _ in updates:
                payload = await asyncio.to_thread(_activity_payload)
                yield f"data: {json.dumps(payload)}\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    def _career_ladder_ref(slug: str, build_slug: str) -> tuple[str, str, str]:
        """The ladder key (plus champion/role) behind a report URL.

        Only the Career store is touched, so no Riot key is required -- these
        routes never talk to the Riot API.
        """
        if not (_is_report_slug(slug) and _is_report_slug(build_slug)):
            raise HTTPException(status_code=400, detail="Invalid report reference.")
        meta_path = config.reports_dir / slug / build_slug / "meta.json"
        if not meta_path.is_file():
            raise HTTPException(status_code=404, detail="Unknown build")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        champion = str(meta.get("champion", ""))
        role = str(meta.get("role", ""))
        riot_id = str(meta.get("riot_id", ""))
        tagline = str(meta.get("tagline", ""))
        if not (champion and role and riot_id and tagline):
            raise HTTPException(status_code=409, detail="Build metadata is incomplete.")
        ladder_key = career_build_key(player_slug(riot_id, tagline), champion, role)
        return ladder_key, champion, role

    @app.post("/api/players/{slug}/builds/{build_slug}/career/ack")
    def acknowledge_career_banner(slug: str, build_slug: str) -> dict[str, Any]:
        """Mark a Career block-complete banner as seen.

        The flag survives report builds so a background watch refresh cannot
        swallow the milestone; this is how a reader retires it.
        """
        ladder_key, _champion, _role = _career_ladder_ref(slug, build_slug)
        with open_career_store() as career:
            career.clear_pending_congrats(ladder_key)
        return {"acknowledged": True}

    @app.post("/api/players/{slug}/builds/{build_slug}/career/recap/ack")
    def acknowledge_career_recap(
        slug: str, build_slug: str, body: RecapAckRequest
    ) -> dict[str, Any]:
        """Mark the "what's new" recap modal as seen up to one game.

        Recorded by match id/timestamp rather than cleared outright, so a game
        played while the modal is open is not swallowed by the ack.
        """
        ladder_key, _champion, _role = _career_ladder_ref(slug, build_slug)
        with open_career_store() as career:
            career.ack_recap(
                ladder_key,
                match_id=body.match_id,
                game_ms=body.game_ms,
                hits=body.hits,
                track_key=body.track_key,
            )
        return {"acknowledged": True}

    @app.post("/api/players/{slug}/builds/{build_slug}/career/drop")
    def drop_career_block(
        slug: str, build_slug: str, body: CareerDropRequest
    ) -> dict[str, Any]:
        """Discard one Career block and regenerate the ladder behind it.

        The drop needs match data to restamp the promoted block's window and to
        build a replacement, so it is queued here and performed by the regenerate
        run this schedules. A dropped track is not recorded as used, so if it is
        still the best fit for the build it comes straight back, with rungs
        recomputed against current peer percentiles.

        The regenerate is scoped to this champion and lane. A regenerate sets
        ``new_match_ids = None``, which makes ``should_skip_unchanged_build`` return
        False for every build, so an unscoped one would re-analyse the player's
        whole report set to act on one champion's ladder.
        """
        ladder_key, champion, role = _career_ladder_ref(slug, build_slug)
        with open_career_store() as career:
            if not any(goal.slot == body.slot for goal in career.load_goals(ladder_key)):
                raise HTTPException(status_code=404, detail="No block in that slot.")
            career.request_drop(ladder_key, body.slot)
        job = _enqueue_player_job(
            slug,
            JOB_KIND_REGENERATE,
            filter_champion=champion,
            filter_role=role,
        )
        return {"dropped_slot": body.slot, "build_slug": build_slug, **job}

    @app.get("/api/players/{slug}/builds/{build_slug}")
    def build_payload(slug: str, build_slug: str) -> dict[str, Any]:
        if not (_is_report_slug(slug) and _is_report_slug(build_slug)):
            raise HTTPException(status_code=400, detail="Invalid report reference.")
        report_json_path = config.reports_dir / slug / build_slug / "report.json"
        if not report_json_path.is_file():
            raise HTTPException(status_code=404, detail="Unknown build")
        payload = json.loads(report_json_path.read_text(encoding="utf-8"))
        return prepare_web_report_payload(payload)

    def _resolve_build_filter(
        slug: str,
        champion: str,
        role: str,
        builds: list[dict[str, Any]],
    ) -> tuple[str, str]:
        """Map a refresh body to canonical Riot champion id + role.

        Raises:
            HTTPException: When the role is invalid or no matching report exists.
        """
        champion_raw = champion.strip()
        role_raw = role.strip()
        if not champion_raw or not role_raw:
            raise HTTPException(
                status_code=422,
                detail="Both champion and role are required to refresh a single build.",
            )
        try:
            role_norm = normalize_role(role_raw)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        needle = champion_raw.lower()
        for build in builds:
            build_champion = str(build.get("champion", ""))
            build_role = str(build.get("role", ""))
            if build_champion.lower() != needle:
                continue
            try:
                if normalize_role(build_role) != role_norm:
                    continue
            except ValueError:
                continue
            return build_champion, role_norm
        raise HTTPException(
            status_code=404,
            detail=f"No {champion_raw} {role_norm.lower()} report for this player.",
        )

    def _enqueue_player_job(
        slug: str,
        kind: str,
        *,
        filter_champion: str | None = None,
        filter_role: str | None = None,
    ) -> dict[str, Any]:
        """Queue a job for a known player, recovering identity from disk if needed."""
        player = store.get_player(slug)
        builds = discover_player_builds(config.reports_dir / slug)
        meta = builds[0] if builds else None
        store_players = None
        if player is not None:
            store_players = player.get("players") or decode_players(
                player.get("players_json"),
                riot_id=str(player.get("riot_id", "")),
                tagline=str(player.get("tagline", "")),
            )
        tracked = _resolve_tracked_players(
            slug, store_players=store_players, meta=meta
        )
        if not tracked:
            if player is None and not builds:
                raise HTTPException(status_code=404, detail="Unknown player")
            raise HTTPException(
                status_code=409,
                detail="This report predates the web app; submit the player again.",
            )
        primary = tracked[0]
        region = str(player["region"]) if player is not None else "euw1"
        # Repair a stale single-player registry row for a multi-player report slug.
        store.upsert_player(
            slug=slug,
            riot_id=primary["riot_id"],
            tagline=primary["tagline"],
            region=region,
            players=tracked,
        )
        job, created = store.enqueue(
            kind=kind,
            riot_id=primary["riot_id"],
            tagline=primary["tagline"],
            region=region,
            player_slug=slug,
            players=tracked,
            filter_champion=filter_champion,
            filter_role=filter_role,
            trace_id=current_trace_id(),
        )
        return {"job": _job_public(store, job), "created": created, "player_slug": slug}

    @app.post("/api/players/{slug}/refresh")
    def refresh_player(
        slug: str, body: RefreshRequest | None = None
    ) -> dict[str, Any]:
        """Fetch latest matches and re-analyse; optionally scope to one build."""
        payload = body or RefreshRequest()
        champion = payload.champion.strip()
        role = payload.role.strip()
        if champion or role:
            builds = discover_player_builds(config.reports_dir / slug)
            if not builds and store.get_player(slug) is None:
                raise HTTPException(status_code=404, detail="Unknown player")
            filter_champion, filter_role = _resolve_build_filter(
                slug, champion, role, builds
            )
            return _enqueue_player_job(
                slug,
                JOB_KIND_REFRESH,
                filter_champion=filter_champion,
                filter_role=filter_role,
            )
        return _enqueue_player_job(slug, JOB_KIND_REFRESH)

    def _backfill_player_registry_if_needed(slug: str) -> None:
        """Self-heal a missing registry row for a slug that already has reports.

        Recovery path for the "dropped Stage-B StreamJobProgress event" bug
        (see `worker.py`'s `_execute_job_via_runner`): a `docker-compose`
        restart racing that one-shot registry write can leave `has_report:
        true` on disk with no matching `store.get_player(slug)` row --
        `can_watch` stays `False` forever, and this is the "Unknown player"
        404 a user hits when clicking watch/unwatch on such a slug. RUNNER's
        own worker.py now carries a DONE-time safety net that prevents *new*
        occurrences of this, but this recovers any row already stuck that way.

        Only acts when there's on-disk proof reports exist for `slug` and no
        registry row already covers it -- mirrors the exact disk-recovery
        `_enqueue_player_job` above already relies on to repair a stale
        registry row (same `_resolve_tracked_players` call, same "euw1"
        region fallback -- also used by `WatchPoller.tick` for a row with no
        region, see `cron_watch/watch.py`), so this isn't a new, unproven
        pattern in this codebase.
        """
        if store.get_player(slug) is not None:
            return
        if not _player_builds(config.output_dir, config.assets_dir, slug):
            return
        meta_builds = discover_player_builds(config.reports_dir / slug)
        meta = meta_builds[0] if meta_builds else None
        tracked = _resolve_tracked_players(slug, store_players=None, meta=meta)
        if not tracked:
            return
        primary = tracked[0]
        store.upsert_player(
            slug=slug,
            riot_id=str(primary["riot_id"]),
            tagline=str(primary["tagline"]),
            region="euw1",
            players=tracked,
        )

    @app.post("/api/players/{slug}/watch")
    def enable_watch(slug: str, body: WatchRequest | None = None) -> dict[str, Any]:
        """Watch a group: poll for new games and refresh automatically."""
        payload = body or WatchRequest()
        _backfill_player_registry_if_needed(slug)
        if not store.set_watch(slug, enabled=True, interval_s=payload.interval_s):
            raise HTTPException(status_code=404, detail="Unknown player")
        row = store.get_player(slug) or {}
        return {"slug": slug, **watch_public_fields(row)}

    @app.delete("/api/players/{slug}/watch")
    def disable_watch(slug: str) -> dict[str, Any]:
        """Stop watching a group."""
        _backfill_player_registry_if_needed(slug)
        if not store.set_watch(slug, enabled=False):
            raise HTTPException(status_code=404, detail="Unknown player")
        row = store.get_player(slug) or {}
        return {"slug": slug, **watch_public_fields(row)}

    @app.post("/api/players/{slug}/regenerate")
    def regenerate_player(slug: str) -> dict[str, Any]:
        """Re-render reports from cached matches without fetching newer games."""
        return _enqueue_player_job(slug, JOB_KIND_REGENERATE)

    @app.post("/api/players/{slug}/builds/{build_slug}/account-views")
    def account_views(
        slug: str, build_slug: str, body: AccountViewsRequest
    ) -> dict[str, Any]:
        """Dashboard views for one account subset of a group report.

        The report page calls this when a toggled account combination was not
        precomputed into the HTML (groups above the precompute limit). Views
        are rebuilt from the local match store and cached beside the report.
        """

        if not (_is_report_slug(slug) and _is_report_slug(build_slug)):
            raise HTTPException(status_code=400, detail="Invalid report reference.")
        build_dir = config.reports_dir / slug / build_slug
        meta_path = build_dir / "meta.json"
        if not meta_path.is_file():
            raise HTTPException(status_code=404, detail="Report not found.")
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise HTTPException(status_code=500, detail="Report metadata unreadable.")
        champion = str(meta.get("champion", "")).strip()
        role = str(meta.get("role", "")).strip()
        tracked = _players_from_meta(meta)
        if not champion or not role or len(tracked) < 2:
            raise HTTPException(
                status_code=400, detail="Not a group report with account data."
            )

        by_label = {
            f"{p['riot_id']}#{p['tagline']}".casefold(): p for p in tracked
        }
        requested: dict[str, dict[str, Any]] = {}
        for raw in body.accounts:
            player = by_label.get(str(raw).strip().casefold())
            if player is None:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown account {raw!r} for this report.",
                )
            requested[f"{player['riot_id']}#{player['tagline']}"] = player
        labels = sorted(requested)

        cache_key = hashlib.sha1(
            f"{account_view_key(labels).casefold()}|{meta.get('generated_at', '')}".encode()
        ).hexdigest()[:16]
        cache_path = build_dir / "account_views" / f"{cache_key}.json"
        if cache_path.is_file():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                return prepare_web_report_payload(cached)
            except (OSError, json.JSONDecodeError):
                pass

        player_row = store.get_player(slug)
        region = str(player_row["region"]) if player_row else "euw1"
        run_config = load_config(
            riot_id=str(tracked[0]["riot_id"]),
            tagline=str(tracked[0]["tagline"]),
            region=region,
            output_dir=config.output_dir,
            assets_dir=config.assets_dir,
            players=[
                PlayerIdentity(riot_id=str(p["riot_id"]), tagline=str(p["tagline"]))
                for p in tracked
            ],
            output_reports_slug=slug,
        )
        build_config = run_config.model_copy(update={"champion": champion, "role": role})
        http_cache = HttpCache(build_config.http_cache_dir)
        mongo_client = _build_mongo_client(config.runner_mongo_uri)
        match_store = RawMatchStore(
            mongo_client, db_name=db_name_from_uri(config.runner_mongo_uri)
        )
        client = RiotApiClient(
            build_config,
            http_cache,
            match_store,
            limiter=shared_rate_limiter(
                build_config.requests_per_second,
                build_config.requests_per_two_minutes,
            ),
        )
        assets = DDragonAssets(build_config)
        services = Services(
            config=build_config,
            http_cache=http_cache,
            store=match_store,
            client=client,
            assets=assets,
        )
        try:
            contexts = resolve_player_contexts(services)
            wanted = {label.casefold() for label in labels}
            selected = [c for c in contexts if c.label.casefold() in wanted]
            if not selected:
                raise HTTPException(
                    status_code=404, detail="No cached matches for these accounts."
                )
            records = load_all_records(
                services,
                [context.puuid for context in selected],
                account_by_puuid={context.puuid: context.label for context in selected},
            )
            records = group_records(records, champion, role)
            if not records:
                raise HTTPException(
                    status_code=404, detail="No games for this account selection."
                )
            account_icons = _account_icon_hrefs(tracked, assets, build_dir)
            build_config.run_graphs_dir.mkdir(parents=True, exist_ok=True)
            log.info(
                "Building account-subset views for %s/%s: %d account(s), %d record(s)",
                slug,
                build_slug,
                len(labels),
                len(records),
            )
            t0 = time.monotonic()
            views = build_account_subset_views(
                build_config,
                records,
                build_config.run_graphs_dir,
                assets=assets,
                account_icons=account_icons,
                run_dir=build_dir,
            )
            log.info(
                "Built account-subset views for %s/%s in %.1fs",
                slug,
                build_slug,
                time.monotonic() - t0,
            )
        finally:
            match_store.close()
            http_cache.close()

        # Same encoding as the embedded report JSON (stringifies numpy scalars).
        payload: dict[str, Any] = json.loads(json.dumps(views, default=str))
        payload = prepare_web_report_payload(payload)
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(payload), encoding="utf-8")
        except OSError:
            pass
        return payload

    @app.post("/api/chat")
    def chat(body: ChatRequest) -> dict[str, Any]:
        if not config.gemini_api_key:
            raise HTTPException(status_code=503, detail="Chat is not configured.")
        try:
            history = validate_history(body.history)
            summary = load_report_summary(config.reports_dir, body.report)
            stats = resolve_chat_stats(summary, body.context)
            text = gemini_reply(
                config.gemini_api_key,
                stats=stats,
                build_label=str(summary.get("build_label", "")),
                player_name=str(summary.get("player", "")),
                tab=body.tab,
                history=history,
            )
        except ChatError as exc:
            status = 404 if "not found" in str(exc) else 400
            raise HTTPException(status_code=status, detail=str(exc))
        return {"text": text}

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        """Unlike RUNNER/CronWatch/PEERS' dedicated internal-only metrics ports,
        this reuses api-ui's own public HTTP server (see this route's original
        docstring above HTTP_REQUEST_DURATION), so nothing at this layer
        restricts who can read it. Phase 6 final review, Finding 3: gated at
        the reverse-proxy layer instead -- `deploy/run.sh`'s `write_caddyfile()`
        wraps `/metrics` in a `route` block that 403s any request whose
        `remote_ip` is outside Caddy's `private_ranges`, so only same-host/
        private-network callers (e.g. Prometheus, an operator over a VPN) ever
        reach this handler in the deployed topology. Not gated here in Python
        because the deployed topology always sits behind that Caddy layer;
        a test hitting this app directly (bypassing Caddy, as `TestClient`
        does) intentionally still gets a 200, matching every other route here.
        """
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/riot.txt", response_class=PlainTextResponse)
    def riot_site_verification() -> PlainTextResponse:
        """Serve Riot Developer Portal domain ownership proof."""
        path = Path(__file__).resolve().parents[2] / "riot.txt"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="riot.txt missing")
        return PlainTextResponse(path.read_text(encoding="utf-8").strip() + "\n")

    # --------------------------------------------------------------- SPA host
    # Registered last: every /api and /out route above wins on an exact match,
    # everything else falls through to the SPA shell so svelte-spa-router's
    # client-side routes survive a hard refresh.
    if (SPA_DIST_DIR / "assets").is_dir():
        app.mount(
            "/assets", StaticFiles(directory=str(SPA_DIST_DIR / "assets")), name="spa-assets"
        )

    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_spa(full_path: str) -> FileResponse:
        index_path = SPA_DIST_DIR / "index.html"
        if not index_path.is_file():
            raise HTTPException(status_code=404, detail="SPA build not found")
        return FileResponse(index_path)

    return app
