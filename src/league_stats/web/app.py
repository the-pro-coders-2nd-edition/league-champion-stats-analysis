"""FastAPI application: pages, JSON API, static report serving.

Generated reports remain plain files under ``output/`` (served at ``/out``);
the app adds a landing/search page, a player status page, the job API and
the Gemini chat proxy on top.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from league_stats.core.champions import (
    champion_display_name,
    champion_icon_id,
    normalize_role,
    parse_riot_id,
    players_group_slug,
)
from league_stats.core.config import (
    DEFAULT_TEMPLATE_DIR,
    VALID_PLATFORMS,
    VALID_REGIONS,
    AppConfig,
    PlayerIdentity,
    WebConfig,
    load_config,
    load_web_config,
)
from league_stats.infra.cache import HttpCache, MatchStore
from league_stats.infra.ddragon_assets import DDragonAssets
from league_stats.infra.riot_api import RiotApiClient, RiotApiError, shared_rate_limiter
from league_stats.pipeline.fetch import group_records, load_all_records, resolve_player_contexts
from league_stats.pipeline.orchestrator import (
    _account_icon_hrefs,
    account_view_key,
    build_account_subset_views,
)
from league_stats.pipeline.services import Services
from league_stats.presentation.brand_assets import (
    APP_TITLE,
    FAVICON_FILENAME,
    LOGO_FILENAME,
    ensure_brand_assets,
    refresh_saved_report_branding,
)
from league_stats.presentation.report import discover_player_builds, is_group_player_label
from league_stats.presentation.report_static import ensure_report_static_assets
from league_stats.utils import setup_logging
from league_stats.web.chat import ChatError, gemini_reply, load_report_summary, validate_history
from league_stats.web.jobs import (
    JOB_KIND_ANALYZE,
    JOB_KIND_REGENERATE,
    JOB_KIND_REFRESH,
    JobStore,
    decode_players,
    players_label,
)
from league_stats.web.worker import AnalysisWorker

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

# Platform choices offered in the search form (label, value).
REGION_CHOICES: tuple[tuple[str, str], ...] = (
    ("EUW", "euw1"),
    ("EUNE", "eun1"),
    ("NA", "na1"),
    ("KR", "kr"),
    ("BR", "br1"),
    ("LAN", "la1"),
    ("LAS", "la2"),
    ("OCE", "oc1"),
    ("TR", "tr1"),
    ("RU", "ru"),
    ("JP", "jp1"),
)

# Minimum ranked games a champion+lane needs before a report is generated.
DEFAULT_MIN_GAMES: int = AppConfig.model_fields["min_games"].default
MIN_GAMES_CHOICES: tuple[int, ...] = (5, 10, 15, 20, 25, 30, 50)

MAX_GROUP_PLAYERS = 8


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


def _build_precheck_client(region: str, output_dir: Path) -> RiotApiClient:
    """Build a Riot client sharing cache + rate limiter with analysis jobs."""
    config = load_config(
        riot_id="precheck",
        tagline="PRE",
        region=region,
        output_dir=output_dir,
    )
    config.ensure_directories()
    return RiotApiClient(
        config,
        HttpCache(config.http_cache_dir),
        MatchStore(config.db_path),
        limiter=shared_rate_limiter(
            config.requests_per_second, config.requests_per_two_minutes
        ),
    )


def _verify_players_exist(
    players: list[dict[str, str]],
    region: str,
    output_dir: Path,
) -> None:
    """Resolve each Riot ID via account-v1 before enqueueing.

    Raises:
        ValueError: When one or more Riot IDs are unknown for the region.
        RiotApiError: When the Riot API fails for a non-404 reason.
    """
    client = _build_precheck_client(region, output_dir)
    missing: list[str] = []
    for player in players:
        label = f"{player['riot_id']}#{player['tagline']}"
        try:
            client.resolve_puuid(player["riot_id"], player["tagline"])
        except RiotApiError as exc:
            message = str(exc)
            if "404" in message and "by-riot-id" in message:
                missing.append(label)
                continue
            raise
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
    """Copy profile icon + solo rank onto ``primary`` from matching sources."""
    from league_stats.core.models import solo_rank_fields

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
            rank = solo_rank_fields(player)
            if rank:
                ranks[key] = rank
    merged: list[dict[str, Any]] = []
    for player in primary:
        entry: dict[str, Any] = {
            "riot_id": str(player["riot_id"]),
            "tagline": str(player["tagline"]),
        }
        raw = player.get("profile_icon_id")
        if raw is not None:
            try:
                entry["profile_icon_id"] = int(raw)
            except (TypeError, ValueError):
                pass
        elif (
            icon := icons.get(
                (entry["riot_id"].casefold(), entry["tagline"].casefold())
            )
        ) is not None:
            entry["profile_icon_id"] = icon
        own_rank = solo_rank_fields(player)
        if own_rank:
            entry.update(own_rank)
        elif rank := ranks.get(
            (entry["riot_id"].casefold(), entry["tagline"].casefold())
        ):
            entry.update(rank)
        merged.append(entry)
    return merged


def _players_from_meta(meta: dict[str, Any]) -> list[dict[str, Any]]:
    """Recover tracked players from on-disk report metadata.

    Prefers the structured ``players`` list. Older CLI group reports only stored
    a comma-separated ``player`` label plus the primary ``riot_id``/``tagline``;
    parse the label so refresh/regenerate keep every account in the group.
    """
    from league_stats.core.models import solo_rank_fields

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
            entry.update(solo_rank_fields(item))
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
) -> list[dict[str, Any]]:
    """Pick the best tracked-player list for a report slug.

    Prefers candidates whose ``players_group_slug`` matches ``slug`` (so a stale
    single-player registry row cannot override a multi-player on-disk group).
    """
    candidates: list[list[dict[str, Any]]] = []
    if meta is not None:
        from_meta = _players_from_meta(meta)
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


def _web_asset_href(output_dir: Path, *parts: str) -> str | None:
    """Absolute ``/out/...`` URL when the asset exists on disk."""
    path = output_dir.joinpath(*parts)
    if not path.is_file():
        return None
    return "/out/" + "/".join(parts)


def _shaped_players(
    output_dir: Path, tracked: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """API/template shape for group members (label + optional icon/rank)."""
    from league_stats.core.models import format_solo_rank_label, solo_rank_fields
    from league_stats.infra.ddragon_assets import fetch_rank_emblem

    shaped: list[dict[str, Any]] = []
    for player in tracked:
        riot_id = str(player["riot_id"])
        tagline = str(player["tagline"])
        icon_href = None
        raw_icon = player.get("profile_icon_id")
        if raw_icon is not None:
            try:
                icon_href = _web_asset_href(
                    output_dir, "assets", "profile_icons", f"{int(raw_icon)}.png"
                )
            except (TypeError, ValueError):
                icon_href = None
        entry: dict[str, Any] = {
            "riot_id": riot_id,
            "tagline": tagline,
            "label": f"{riot_id}#{tagline}",
            "profile_icon": icon_href,
        }
        rank = solo_rank_fields(player)
        if rank:
            tier = str(rank["solo_tier"])
            entry["solo_rank_label"] = format_solo_rank_label(
                tier,
                str(rank.get("solo_rank") or ""),
                rank.get("solo_lp"),
            )
            emblem = fetch_rank_emblem(output_dir / "assets" / "ranks", tier)
            if emblem is not None:
                entry["solo_rank_icon"] = _web_asset_href(
                    output_dir, "assets", "ranks", emblem.name
                )
        shaped.append(entry)
    return shaped


def _profile_icon_hrefs(
    output_dir: Path,
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
        href = _web_asset_href(
            output_dir, "assets", "profile_icons", f"{icon_id}.png"
        )
        if href:
            hrefs.append(href)
            seen.add(icon_id)
    if not hrefs and primary_icon_id is not None:
        href = _web_asset_href(
            output_dir, "assets", "profile_icons", f"{int(primary_icon_id)}.png"
        )
        if href:
            hrefs.append(href)
    return hrefs


def _brand_page_context(output_dir: Path) -> dict[str, Any]:
    """Shared template fields for logo, favicon, and app title."""
    ensure_brand_assets(output_dir)
    return {
        "app_title": APP_TITLE,
        "logo_href": _web_asset_href(output_dir, "assets", "brand", LOGO_FILENAME),
        "favicon_href": _web_asset_href(output_dir, "assets", "brand", FAVICON_FILENAME),
    }


def _player_builds(output_dir: Path, slug: str) -> list[dict[str, Any]]:
    """On-disk builds for one player, with web hrefs and icon URLs."""
    builds = discover_player_builds(output_dir / "reports" / slug)
    shaped: list[dict[str, Any]] = []
    for build in builds:
        build_slug = str(build.get("href", "")).split("/", 1)[0]
        champion_id = str(build.get("champion", ""))
        role = str(build.get("role", ""))
        report_dir = output_dir / "reports" / slug / build_slug
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
                "peers_ready": _build_peers_ready(build, report_dir),
                "href": f"/out/reports/{slug}/{build.get('href', '')}",
                "champion_icon": _web_asset_href(
                    output_dir,
                    "assets",
                    "champions",
                    f"{champion_icon_id(champion_id)}.png",
                ),
                "role_icon": _web_asset_href(
                    output_dir, "assets", "roles", f"{role}.png"
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
    reports_dir: Path, store: JobStore | None = None
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
            shaped = _shaped_players(output_dir, tracked)
            if not shaped and label:
                shaped = [{"label": label, "profile_icon": None}]
            elif not shaped and primary_icon_id is not None:
                icons = _profile_icon_hrefs(
                    output_dir, None, primary_icon_id=primary_icon_id
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
            shaped = _shaped_players(output_dir, tracked)
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


def create_app(
    web_config: WebConfig | None = None, *, start_worker: bool = True
) -> FastAPI:
    """Build the FastAPI application (worker optional, for tests)."""
    setup_logging(False)
    config = web_config or load_web_config()
    config.output_dir.mkdir(parents=True, exist_ok=True)
    config.reports_dir.mkdir(parents=True, exist_ok=True)
    ensure_brand_assets(config.output_dir)
    # Refresh every saved report's CSS so skipped builds don't keep stale
    # button styles after a template deploy / local edit.
    ensure_report_static_assets(config.output_dir, DEFAULT_TEMPLATE_DIR, sync_existing=True)
    refresh_saved_report_branding(config.output_dir)
    brand = _brand_page_context(config.output_dir)

    store = JobStore(config.app_db_path)
    worker = AnalysisWorker(store, config)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        store.recover_orphans()
        if start_worker:
            worker.start()
        yield
        if start_worker:
            worker.stop()
        store.close()

    app = FastAPI(title=APP_TITLE, lifespan=lifespan)
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    app.state.job_store = store
    app.state.web_config = config

    @app.middleware("http")
    async def revalidate_report_stylesheets(request: Request, call_next):  # type: ignore[no-untyped-def]
        """Force CSS under /out to revalidate so HTML/CSS versions cannot skew."""
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/out/") and path.endswith(".css"):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response

    app.mount("/out", StaticFiles(directory=str(config.output_dir), html=True), name="out")

    # ------------------------------------------------------------------ pages

    @app.get("/", response_class=HTMLResponse)
    def landing(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "landing.html",
            {
                **brand,
                "groups": _report_groups(config.reports_dir, store),
                "region_choices": REGION_CHOICES,
                "min_games_choices": MIN_GAMES_CHOICES,
                "default_min_games": DEFAULT_MIN_GAMES,
            },
        )

    @app.get("/players/{slug}", response_class=HTMLResponse)
    def player_page(request: Request, slug: str) -> HTMLResponse:
        player = store.get_player(slug)
        builds = _player_builds(config.output_dir, slug)
        if player is None and not builds:
            raise HTTPException(status_code=404, detail="Unknown player")
        meta_builds = discover_player_builds(config.reports_dir / slug)
        tracked = _resolve_tracked_players(
            slug,
            store_players=(player.get("players") if player else None),
            meta=meta_builds[0] if meta_builds else None,
        )
        label = _player_label_from_tracked(tracked, slug)
        if not tracked and builds:
            label = str(builds[0].get("player") or slug)
        return templates.TemplateResponse(
            request,
            "player.html",
            {
                **brand,
                "slug": slug,
                "player_label": label,
                "players": _shaped_players(config.output_dir, tracked),
            },
        )

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
            _verify_players_exist(tracked, region, config.output_dir)
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
        )
        return {
            "job": _job_public(store, job),
            "created": created,
            "player_slug": slug,
            "has_report": bool(_player_builds(config.output_dir, slug)),
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

    @app.get("/api/activity")
    def activity() -> dict[str, Any]:
        """Active jobs for the landing-page status dots."""
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
            shaped = _shaped_players(config.output_dir, tracked)
            if not shaped:
                shaped = [{"label": label, "profile_icon": None}]
            items.append(
                {
                    "slug": slug,
                    "player_label": label,
                    "players": shaped,
                    "state": job.get("state"),
                    "has_report": bool(_player_builds(config.output_dir, slug)),
                }
            )
        return {"items": items}

    @app.get("/api/players/{slug}")
    def player_status(slug: str) -> dict[str, Any]:
        player = store.get_player(slug)
        builds = _player_builds(config.output_dir, slug)
        if player is None and not builds:
            raise HTTPException(status_code=404, detail="Unknown player")
        active = store.active_job_for_player(slug)
        meta_builds = discover_player_builds(config.reports_dir / slug)
        tracked = _resolve_tracked_players(
            slug,
            store_players=(player.get("players") if player else None),
            meta=meta_builds[0] if meta_builds else None,
        )
        label = _player_label_from_tracked(tracked, slug)
        if not tracked and builds:
            label = str(builds[0].get("player") or slug)
        return {
            "slug": slug,
            "player_label": label,
            "players": _shaped_players(config.output_dir, tracked),
            "active_job": _job_public(store, active),
            "builds": builds,
            "has_report": bool(builds),
            "peer_failed": bool(player["peer_failed"]) if player else False,
            "base_completed_at": player["base_completed_at"] if player else None,
            "peer_completed_at": player["peer_completed_at"] if player else None,
        }

    @app.get("/api/players/{slug}/builds/{build_slug}")
    def build_payload(slug: str, build_slug: str) -> dict[str, Any]:
        if not (_is_report_slug(slug) and _is_report_slug(build_slug)):
            raise HTTPException(status_code=400, detail="Invalid report reference.")
        report_json_path = config.reports_dir / slug / build_slug / "report.json"
        if not report_json_path.is_file():
            raise HTTPException(status_code=404, detail="Unknown build")
        return json.loads(report_json_path.read_text(encoding="utf-8"))

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
                return json.loads(cache_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass

        player_row = store.get_player(slug)
        region = str(player_row["region"]) if player_row else "euw1"
        run_config = load_config(
            riot_id=str(tracked[0]["riot_id"]),
            tagline=str(tracked[0]["tagline"]),
            region=region,
            output_dir=config.output_dir,
            players=[
                PlayerIdentity(riot_id=str(p["riot_id"]), tagline=str(p["tagline"]))
                for p in tracked
            ],
            output_reports_slug=slug,
        )
        build_config = run_config.model_copy(update={"champion": champion, "role": role})
        http_cache = HttpCache(build_config.http_cache_dir)
        match_store = MatchStore(build_config.db_path)
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
            views = build_account_subset_views(
                build_config,
                records,
                build_config.run_graphs_dir,
                assets=assets,
                account_icons=account_icons,
                run_dir=build_dir,
            )
        finally:
            match_store.close()
            http_cache.close()

        # Same encoding as the embedded report JSON (stringifies numpy scalars).
        payload: dict[str, Any] = json.loads(json.dumps(views, default=str))
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
            text = gemini_reply(
                config.gemini_api_key,
                stats=summary,
                build_label=str(summary.get("build_label", "")),
                player_name=str(summary.get("player", "")),
                history=history,
            )
        except ChatError as exc:
            status = 404 if "not found" in str(exc) else 400
            raise HTTPException(status_code=status, detail=str(exc))
        return {"text": text}

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/riot.txt", response_class=PlainTextResponse)
    def riot_site_verification() -> PlainTextResponse:
        """Serve Riot Developer Portal domain ownership proof."""
        path = Path(__file__).resolve().parents[3] / "riot.txt"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="riot.txt missing")
        return PlainTextResponse(path.read_text(encoding="utf-8").strip() + "\n")

    return app
