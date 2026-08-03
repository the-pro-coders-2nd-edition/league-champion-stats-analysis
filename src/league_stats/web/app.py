"""FastAPI application: pages, JSON API, static report serving.

Generated reports remain plain files under ``output/`` (served at ``/out``);
the app adds a landing/search page, a player status page, the job API and
the Gemini chat proxy on top.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from league_stats.core.champions import (
    champion_display_name,
    parse_riot_id,
    players_group_slug,
)
from league_stats.core.config import (
    VALID_PLATFORMS,
    VALID_REGIONS,
    WebConfig,
    load_web_config,
)
from league_stats.presentation.brand_assets import (
    APP_TITLE,
    FAVICON_FILENAME,
    LOGO_FILENAME,
    ensure_brand_assets,
)
from league_stats.presentation.report import discover_player_builds, is_group_player_label
from league_stats.utils import setup_logging
from league_stats.web.chat import ChatError, gemini_reply, load_report_summary, validate_history
from league_stats.web.jobs import (
    JOB_KIND_ANALYZE,
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


class ChatRequest(BaseModel):
    """Body of ``POST /api/chat``."""

    report: str = Field(min_length=3, max_length=200)
    history: list[Any]


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


def _player_label_from_row(player: dict[str, Any] | None, slug: str) -> str:
    """Display label for a player/group registry row."""
    if player is None:
        return slug
    tracked = player.get("players") or decode_players(
        player.get("players_json"),
        riot_id=str(player.get("riot_id", "")),
        tagline=str(player.get("tagline", "")),
    )
    if tracked:
        return players_label(tracked)
    return f"{player['riot_id']}#{player['tagline']}"


def _players_from_meta(meta: dict[str, Any]) -> list[dict[str, str]]:
    """Recover tracked players from on-disk report metadata."""
    raw_players = meta.get("players")
    if isinstance(raw_players, list) and raw_players:
        recovered: list[dict[str, str]] = []
        for item in raw_players:
            if not isinstance(item, dict):
                continue
            name = str(item.get("riot_id", "")).strip()
            tag = str(item.get("tagline", "")).strip()
            if name and tag:
                recovered.append({"riot_id": name, "tagline": tag})
        if recovered:
            return recovered
    riot_id = str(meta.get("riot_id", "")).strip()
    tagline = str(meta.get("tagline", "")).strip()
    if riot_id and tagline:
        return [{"riot_id": riot_id, "tagline": tagline}]
    return []


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
                "href": f"/out/reports/{slug}/{build.get('href', '')}",
                "champion_icon": _web_asset_href(
                    output_dir, "assets", "champions", f"{champion_id}.png"
                ),
                "role_icon": _web_asset_href(
                    output_dir, "assets", "roles", f"{role}.png"
                ),
            }
        )
    return shaped


def _report_groups(
    reports_dir: Path, store: JobStore | None = None
) -> list[dict[str, Any]]:
    """Player cards for the landing page from on-disk report metadata.

    When ``store`` is provided, cards are marked ``busy`` if that player has an
    active (queued/running) job, and players with an active job but no report
    yet are included so queued first-time analyses appear on the home page.
    """
    groups: list[dict[str, Any]] = []
    seen: set[str] = set()
    if reports_dir.is_dir():
        for player_dir in sorted(reports_dir.iterdir()):
            if not player_dir.is_dir():
                continue
            builds = discover_player_builds(player_dir)
            if not builds:
                continue
            player = str(builds[0].get("player", player_dir.name))
            seen.add(player_dir.name)
            groups.append(
                {
                    "slug": player_dir.name,
                    "player": player,
                    "is_group": is_group_player_label(player),
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
            label = (
                players_label(tracked)
                if tracked
                else f"{job['riot_id']}#{job['tagline']}"
            )
            groups.append(
                {
                    "slug": slug,
                    "player": label,
                    "is_group": is_group_player_label(label),
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

    app = FastAPI(title="Champion Stats Analyzer", lifespan=lifespan)
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    app.state.job_store = store
    app.state.web_config = config

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
            },
        )

    @app.get("/players/{slug}", response_class=HTMLResponse)
    def player_page(request: Request, slug: str) -> HTMLResponse:
        player = store.get_player(slug)
        builds = _player_builds(config.output_dir, slug)
        if player is None and not builds:
            raise HTTPException(status_code=404, detail="Unknown player")
        label = _player_label_from_row(player, slug)
        if player is None and builds:
            label = str(builds[0].get("player") or slug)
        return templates.TemplateResponse(
            request,
            "player.html",
            {**brand, "slug": slug, "player_label": label},
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

    @app.get("/api/activity")
    def activity() -> dict[str, Any]:
        """Active jobs for the landing-page status dots."""
        items: list[dict[str, Any]] = []
        for job in store.list_active_jobs():
            slug = str(job["player_slug"])
            tracked = list(job.get("players") or [])
            label = (
                players_label(tracked)
                if tracked
                else f"{job['riot_id']}#{job['tagline']}"
            )
            items.append(
                {
                    "slug": slug,
                    "player_label": label,
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
        label = _player_label_from_row(player, slug)
        if player is None and builds:
            label = str(builds[0].get("player") or slug)
        return {
            "slug": slug,
            "player_label": label,
            "active_job": _job_public(store, active),
            "builds": builds,
            "has_report": bool(builds),
            "peer_failed": bool(player["peer_failed"]) if player else False,
            "base_completed_at": player["base_completed_at"] if player else None,
            "peer_completed_at": player["peer_completed_at"] if player else None,
        }

    @app.post("/api/players/{slug}/refresh")
    def refresh_player(slug: str) -> dict[str, Any]:
        player = store.get_player(slug)
        if player is None:
            # CLI-generated report: recover identity from on-disk metadata.
            builds = discover_player_builds(config.reports_dir / slug)
            if not builds:
                raise HTTPException(status_code=404, detail="Unknown player")
            meta = builds[0]
            tracked = _players_from_meta(meta)
            if not tracked:
                raise HTTPException(
                    status_code=409,
                    detail="This report predates the web app; submit the player again.",
                )
            primary = tracked[0]
            store.upsert_player(
                slug=slug,
                riot_id=primary["riot_id"],
                tagline=primary["tagline"],
                region="euw1",
                players=tracked,
            )
            player = store.get_player(slug)
        assert player is not None
        tracked = player.get("players") or decode_players(
            player.get("players_json"),
            riot_id=str(player.get("riot_id", "")),
            tagline=str(player.get("tagline", "")),
        )
        primary = tracked[0] if tracked else {
            "riot_id": player["riot_id"],
            "tagline": player["tagline"],
        }
        job, created = store.enqueue(
            kind=JOB_KIND_REFRESH,
            riot_id=primary["riot_id"],
            tagline=primary["tagline"],
            region=player["region"],
            player_slug=slug,
            players=tracked or [primary],
        )
        return {"job": _job_public(store, job), "created": created, "player_slug": slug}

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

    return app
