"""Persistent file cache for live-sampled peer benchmarks.

Cache files live under ``data/benchmarks/live/`` keyed by
``{platform}_{tier}_{champion_slug}.json``. A cached entry is reused only when
all three of these hold:

* the game patch it was sampled on matches the patch being analysed,
* the tracked player is still in the same tier (encoded in the filename), and
* it was fetched less than ``CACHE_TTL_S`` ago.

Patch is the one that matters. Peer metrics move with gameplay patches, and a
cold sample costs up to ~750 Riot requests per build, so the alternative to
patch-keying is either serving stale peers or paying that cost on a timer.

Division and LP are deliberately *not* part of the key: ``build_exact_scope``
accepts every division inside a tier and ``rank_matches`` never looks at LP, so
moving from Gold IV to Gold I does not change who your peers are.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Final

from league_stats.analysis.peer.benchmark_fetcher import BenchmarkSnapshot, MIN_BENCHMARK_GAMES
from league_stats.core.champions import champion_slug

CACHE_TTL_S: Final[float] = 3 * 24 * 3600  # 3 days

_LIVE_CACHE_DIR: Final[Path] = (
    Path(__file__).resolve().parents[2] / "data" / "benchmarks" / "live"
)


def _cache_path(platform: str, tier: str, champion: str, role: str) -> Path:
    slug = champion_slug(champion, role)
    key = f"{platform.lower()}_{tier.upper()}_{slug}"
    return _LIVE_CACHE_DIR / f"{key}.json"


def _patch_is_stale(stored: str, wanted: str) -> bool:
    """Whether a cached sample's patch disqualifies it.

    An unknown *wanted* patch (no records to read one from) falls back to the TTL
    alone rather than discarding a usable sample. An unknown *stored* patch means
    the entry predates patch tracking, so it is discarded as soon as we do know
    which patch we want -- fail closed, at the cost of one re-sample after upgrade.
    """
    if not wanted:
        return False
    return stored != wanted


def read_live_cache(
    platform: str,
    tier: str,
    champion: str,
    role: str,
    *,
    patch: str = "",
) -> BenchmarkSnapshot | None:
    """Return a cached benchmark snapshot if it is still valid."""
    path = _cache_path(platform, tier, champion, role)
    if not path.is_file():
        return None
    try:
        with path.open(encoding="utf-8") as fh:
            data: dict[str, Any] = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None

    if _patch_is_stale(str(data.get("patch", "")), patch):
        return None

    fetched_at = float(data.get("fetched_at", 0))
    if time.time() - fetched_at > CACHE_TTL_S:
        return None

    games = int(data.get("games", 0))
    if games < MIN_BENCHMARK_GAMES:
        return None

    return BenchmarkSnapshot(
        metrics=dict(data.get("metrics", {})),
        games_sampled=games,
        players_sampled=int(data.get("players", 0)),
        from_cache=True,
        platform=platform,
    )


def write_live_cache(
    platform: str,
    tier: str,
    champion: str,
    role: str,
    snapshot: BenchmarkSnapshot,
    *,
    patch: str = "",
) -> None:
    """Persist a live-sampled benchmark snapshot to the file cache."""
    _LIVE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(platform, tier, champion, role)
    data = {
        "metrics": snapshot.metrics,
        "games": snapshot.games_sampled,
        "players": snapshot.players_sampled,
        "fetched_at": time.time(),
        "tier": tier.upper(),
        "platform": platform.lower(),
        "patch": patch,
    }
    try:
        with path.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
    except OSError:
        pass
