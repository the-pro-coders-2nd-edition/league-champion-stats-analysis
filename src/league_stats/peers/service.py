"""PEERS' gRPC service: wraps `resolve_peer_baseline` verbatim via a duck-typed
store adapter, following the same "reuse via adapter" design Phase 1's RUNNER
used for `execute_job` (see `league_stats.runner.adapter.RunnerJobAdapter`).

Sync vs async
-------------
`resolve_peer_baseline` (`analysis/peer/baseline.py`), everything it calls
(`analysis/peer/cache.py`, `analysis/peer/benchmark_fetcher.py`,
`analysis/peer/benchmark_cache.py`), `RiotApiClient` (`infra/riot_api.py`) and
`PeerSampleStore` (`infra/peer_sample_store.py`) are all plain synchronous
Python -- no `async def`/`await` anywhere in that call graph, matching
`MatchStore`'s own synchronous `sqlite3` design. `peers_pb2_grpc.py` (Phase 0)
also declares a plain, non-`async` `PeersServiceServicer` base class. So
`PeersServicer` below is a plain `grpc.server(...)` servicer, exactly like
RUNNER's `RunnerServicer` -- NOT `grpc.aio`, which CRON-watch uses because it
polls on a genuinely async timer loop.

Known gap: PeerSampleStore does not cover everything `resolve_peer_baseline`
touches on its `store` parameter
------------------------------------------------------------------------
`resolve_peer_baseline`'s fallback ladder (levels 0/1/3) calls
`collect_peer_games_from_store` (`analysis/peer/cache.py`), which calls, on
its `store` argument: `count_peer_games`, `load_peer_games`,
`iter_unverified_puuids_for_build`/`iter_unverified_puuids` (via
`_backfill_ranks`), and `set_puuid_rank`. Level 2's live-sampling fallback
(`_try_live_baseline` -> `fetch_benchmark_from_api`,
`analysis/peer/benchmark_fetcher.py`) additionally calls, via
`_load_or_fetch_match`: `load_match` and `save_match`; and via
`_resolve_rank`: `set_puuid_rank` again. `ingest_match`
(`analysis/peer/ingest.py`), called from both of the above, calls
`upsert_peer_game`.

`PeerSampleStore` (Task 1 of this plan, `infra/peer_sample_store.py`)
deliberately mirrors ONLY the six peer-game methods
(`upsert_peer_game`, `load_peer_games`, `count_peer_games`,
`iter_unverified_puuids`, `iter_unverified_puuids_for_build`,
`set_puuid_rank` -- see its own module docstring). It does NOT implement
`iter_match_ids`, `load_match` or `save_match` -- the raw match/timeline
storage half of `MatchStore`'s surface (`infra/cache.py:184-303`).

Two of those three additionally get called by `collect_peer_games_from_store`
itself (`analysis/peer/cache.py`, lines ~181-186), *before* the six-method
surface above even runs: on the very first request for a champion+role+
platform combination (`store.count_peer_games(...) == 0`), it bootstraps by
calling `store.iter_match_ids(exclude_puuid)` then `store.load_match(match_id)`
for each id, to backfill peer rows from the tracked player's own already-
downloaded match history before doing anything else. Once at least one row
exists for that champion+role+platform, this bootstrap is skipped on every
later call (`count_peer_games(...) == 0` is false), so the gap only bites on
genuinely first-ever access to a build. `resolve_peer_baseline` on a bare
`PeerSampleStore` (with no adapter) would raise `AttributeError` on this
first call, and again on any call that falls through to level 2 (live
sampling), inside `_load_or_fetch_match`.

There IS an existing, tested Mongo store with the missing raw-match-storage
surface: `infra/raw_match_store.py`'s `RawMatchStore` (`has_match`,
`save_match`, `load_match`, `save_timeline`, `load_timeline`,
`claim_ownership`, `iter_all_match_ids`, `count`) -- built in an earlier
phase for RUNNER, currently unwired into any production code path (see
`runner/service.py`'s own module docstring), and it does NOT have a
`iter_match_ids(puuid)`-scoped method either (only the unscoped
`iter_all_match_ids()`), so wiring it in would not fully close this gap
either, and would add a second Mongo dependency to PEERS beyond what this
task's brief scopes (`PeerSampleStore` only). Review round 1 confirmed the
no-op adapter approach below is the right call for now (see
`test_resolve_peer_baseline_via_live_sampling_survives_the_noop_store_methods`
for direct proof the real fallback ladder survives it) -- wiring `RawMatchStore`
in remains a deliberate follow-up for the controller, not decided here.

`_PeerStoreAdapter` below therefore delegates the six real methods to a real
`PeerSampleStore`, and implements `iter_match_ids`/`load_match`/`save_match`
as documented no-ops (mirroring `RunnerJobAdapter`'s precedent of no-op
methods for capabilities its backing object doesn't have): the store
bootstrap step silently does nothing (falls through to "0 games", same as
an empty store), and `_load_or_fetch_match` always live-fetches from Riot
and never caches the raw match document (`ingest_match`'s
`upsert_peer_game` call still runs normally and persists the *extracted*
peer row for real, into the real `PeerSampleStore` -- only the raw match
JSON caching is skipped). This means live sampling can re-download the same
match from Riot more than once across separate requests instead of hitting
a local cache -- a real, known inefficiency, not a correctness bug.

Platform routing (review round 1, fix 1)
-----------------------------------------
`RequestBaselineRequest` originally had no `platform`/`exclude_puuid` fields.
Without a `platform` field, `_build_default_riot_client` fell back to
`AppConfig.routing_platform`'s silent default (`euw1`) whenever
`PEERS_PLATFORM` was unset -- a KR/NA request would silently get EUW peer
rows (or an empty store) with no error. The proto now carries both fields
(it had zero existing callers, so this was a free additive change).
`RequestBaseline` uses `request.platform` when present, falling back to
`PEERS_PLATFORM`, then to the shared `RiotApiClient`'s own configured
platform, in that order. Since `RiotApiClient.platform` has no public setter
(and `infra/riot_api.py` is out of this task's scope to modify), per-request
platform routing is done via `_PlatformScopedRiotClient`, a thin wrapper that
overrides `.platform`/`.platform_base` (the two attributes
`fetch_league_entries_pages`/`fetch_solo_rank`/`collect_peer_games_from_store`'s
platform filter actually read) and delegates everything else unchanged to the
shared client -- multiple concurrent requests for different platforms can
safely share one underlying `RiotApiClient` (and its rate limiter) this way.

`request.exclude_puuid` is passed straight through to
`resolve_peer_baseline`'s `exclude_puuid` keyword.

Concurrency (review round 1, fix 2)
------------------------------------
Two problems existed: (a) executor saturation -- a live sample can issue up
to `MAX_MATCH_DOWNLOADS` (400) rate-limited HTTP calls, taking minutes, while
the fast-path timeout is a few seconds; a request queued behind several such
samples would silently time out with no signal that it never even started
running; (b) no dedup -- two identical in-flight `(champion, role, platform,
tier)` requests each launched an independent, redundant live sample.

Fixed by `_get_or_submit`: in-flight resolutions are tracked in
`self._inflight`, keyed on `(champion, role, platform, tier)` (division is
excluded from the key because `RankScope` -- see
`analysis/peer/rank_scope.py` -- only ever matches on tier, never division,
so two requests differing only by division would resolve identically; this
does mean two callers with different `exclude_puuid` values deduped onto the
same in-flight resolution share one result that only excludes the *first*
caller's puuid -- a known, accepted simplification matching the review's
specified dedup key). A second caller for the same key attaches its own
`request_id`/callback to the *same* `Future` instead of submitting a new one.

For observability, each in-flight resolution tracks a `threading.Event` that
is set only once a worker thread actually starts running it (not merely
submitted to the executor's queue). When `RequestBaseline`'s fast-path wait
times out, it logs -- and increments `PEERS_FAST_PATH_TIMEOUTS_TOTAL`, labeled
`started="true"/"false"` -- whether the resolution was genuinely running
(almost certainly live sampling) or still queued behind other in-flight work
(saturated executor), instead of returning an opaque `cached=False` with no
way to tell the two apart. `PEERS_INFLIGHT_BASELINES` gauges current
concurrency. `PEERS_MAX_CONCURRENT_BASELINES` (env var, default 4) bounds the
executor.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterator

import grpc
import pymongo
from prometheus_client import Counter, Gauge
from pymongo import uri_parser as mongo_uri_parser

from league_stats.analysis.peer.baseline import PeerBaseline, resolve_peer_baseline
from league_stats.core.config import AppConfig
from league_stats.core.models import RankedEntry
from league_stats.infra.cache import HttpCache, MatchStore
from league_stats.infra.peer_sample_store import PeerSampleStore
from league_stats.infra.riot_api import RiotApiClient
from league_stats.utils import get_logger
from league_stats_rpc.v1 import peers_pb2, peers_pb2_grpc, runner_pb2, runner_pb2_grpc

log = get_logger("peers_service")

# How long RequestBaseline blocks waiting for a same-thread resolution before
# giving up and treating it as a background (live-sampling) fetch. Store-only
# resolutions (levels 0/1/3) and static-JSON fallbacks (levels 4/5) are all
# local reads and comfortably finish well inside this window; level 2 (live
# Riot sampling, up to `MAX_MATCH_DOWNLOADS` real HTTP calls) does not.
FAST_PATH_TIMEOUT_S: float = 3.0

PEERS_INFLIGHT_BASELINES = Gauge(
    "peers_inflight_baselines",
    "resolve_peer_baseline calls currently running on PEERS' executor "
    "(does not count requests still queued behind them).",
)
PEERS_FAST_PATH_TIMEOUTS_TOTAL = Counter(
    "peers_fast_path_timeouts_total",
    "RequestBaseline calls that exceeded the fast-path timeout, labeled by "
    "whether the underlying resolution had actually started running yet "
    "('true' = likely live sampling, 'false' = still queued behind other work).",
    ["started"],
)
PEERS_DEDUPED_REQUESTS_TOTAL = Counter(
    "peers_deduped_requests_total",
    "RequestBaseline calls that attached to an already in-flight resolution "
    "for the same (champion, role, platform, tier) instead of starting a new one.",
)


class _PeerStoreAdapter:
    """`MatchStore`-shaped adapter around `PeerSampleStore` for `resolve_peer_baseline`.

    See this module's docstring for the exact method inventory and the
    documented gap (`iter_match_ids`/`load_match`/`save_match` are no-ops).
    """

    def __init__(self, store: PeerSampleStore) -> None:
        self._store = store

    def upsert_peer_game(self, row: dict[str, Any]) -> bool:
        return self._store.upsert_peer_game(row)

    def load_peer_games(self, *, champion: str, role: str, platform: str) -> list[dict[str, Any]]:
        return self._store.load_peer_games(champion=champion, role=role, platform=platform)

    def count_peer_games(self, *, champion: str, role: str, platform: str) -> int:
        return self._store.count_peer_games(champion=champion, role=role, platform=platform)

    def iter_unverified_puuids(self, limit: int = 100) -> list[str]:
        return self._store.iter_unverified_puuids(limit)

    def iter_unverified_puuids_for_build(
        self, champion: str, role: str, platform: str, limit: int = 200
    ) -> list[str]:
        return self._store.iter_unverified_puuids_for_build(champion, role, platform, limit)

    def set_puuid_rank(self, puuid: str, tier: str, rank: str) -> int:
        return self._store.set_puuid_rank(puuid, tier, rank)

    def iter_match_ids(self, puuid: str) -> Iterator[str]:
        """No-op: PEERS keeps no per-player raw match history (see module docstring)."""
        return iter(())

    def load_match(self, match_id: str) -> dict[str, Any] | None:
        """No-op: PEERS keeps no raw match cache (see module docstring)."""
        return None

    def save_match(self, match_id: str, puuid: str, match: dict[str, Any]) -> None:
        """No-op: PEERS persists only extracted peer rows, not raw match JSON."""
        return None


class _PlatformScopedRiotClient:
    """Wraps a shared `RiotApiClient`, overriding `.platform`/`.platform_base`
    for one request's routing.

    `RiotApiClient.platform` has no public setter and its rate limiter/session
    are meant to be shared across calls, so this wraps rather than mutates or
    reconstructs the underlying client. Delegates every other attribute
    (`fetch_league_entries_pages`, `fetch_match_ids`, `fetch_match`,
    `fetch_solo_rank`, ...) straight through via `__getattr__`.
    """

    def __init__(self, base: RiotApiClient, platform: str) -> None:
        self._base = base
        self._platform = platform

    @property
    def platform(self) -> str:
        return self._platform

    @property
    def platform_base(self) -> str:
        return f"https://{self._platform}.api.riotgames.com"

    def __getattr__(self, name: str) -> Any:
        return getattr(self._base, name)


def _parse_rank(raw: str) -> tuple[str, str]:
    """Split a rank string (``"GOLD II"``, ``"GOLD_II"``, or bare ``"CHALLENGER"``)
    into ``(tier, division)``. ``RequestBaselineRequest.rank`` (Phase 0's proto)
    is a single free-form string, not separate tier/division fields, so this
    is PEERS' own parsing convention -- there is no established format to
    match yet, since no real caller exists before this task. Division is
    ``""`` for apex tiers (MASTER/GRANDMASTER/CHALLENGER) or an unparseable
    input.
    """
    parts = raw.replace("_", " ").split()
    if not parts:
        return "", ""
    tier = parts[0].upper()
    rank = parts[1].upper() if len(parts) > 1 else ""
    return tier, rank


def _encode_baseline(baseline: PeerBaseline | None) -> str:
    if baseline is None:
        return ""
    return json.dumps(asdict(baseline))


def _db_name_from_uri(mongo_uri: str) -> str:
    """Extract the database name from a Mongo connection URI.

    `rsplit("/", 1)[-1]` (the original implementation) breaks on query params
    (`?retryWrites=true`) or a bare host with no db path -- use pymongo's own
    URI parser instead, which handles both correctly.
    """
    return mongo_uri_parser.parse_uri(mongo_uri).get("database") or "league_stats"


def _build_default_peer_store() -> PeerSampleStore:
    mongo_uri = os.environ.get("MONGO_URI", "mongodb://localhost:27017/league_stats")
    client: pymongo.MongoClient = pymongo.MongoClient(mongo_uri)
    return PeerSampleStore(client, db_name=_db_name_from_uri(mongo_uri))


def _build_default_riot_client() -> RiotApiClient:
    """Build PEERS' own `RiotApiClient`, using `PEERS_RIOT_API_KEY` (its own key,
    per the task brief -- kept distinct from the monolith/RUNNER's `RIOT_API_KEY`
    so PEERS' live-sampling traffic is rate-limited and billed separately).

    The `MatchStore` passed here is required by `RiotApiClient.__init__`'s type
    hint, but none of the methods `resolve_peer_baseline`/`fetch_benchmark_from_api`
    call on a `RiotApiClient` (`fetch_league_entries_pages`, `fetch_match_ids`,
    `fetch_match`, `fetch_solo_rank`) ever touch `self._store` -- only
    `download_matches` does, which is not part of this call graph. It is a real,
    local, ephemeral SQLite cache (consistent with the rest of this codebase's
    "`.cache` is ephemeral" convention), not the Mongo `PeerSampleStore`.

    `PEERS_PLATFORM`/`PEERS_REGION` here are only the *default* platform/region
    used when a `RequestBaseline` call carries no `platform` field -- per-request
    routing overrides this via `_PlatformScopedRiotClient` (see module docstring).
    """
    api_key = os.environ.get("PEERS_RIOT_API_KEY", "")
    if not api_key:
        # AppConfig's own validator would raise here too, but its message names
        # RIOT_API_KEY -- the wrong variable for PEERS. Raise with the right one.
        raise RuntimeError(
            "Missing Riot API key for PEERS. Set PEERS_RIOT_API_KEY in the "
            "environment or a .env file (get one at https://developer.riotgames.com)."
        )
    config = AppConfig(
        riot_id="peers",
        tagline="peers",
        api_key=api_key,
        region=os.environ.get("PEERS_REGION", "europe"),
        platform=os.environ.get("PEERS_PLATFORM"),
    )
    cache_dir = Path(os.environ.get("PEERS_CACHE_DIR", ".cache/peers"))
    http_cache = HttpCache(cache_dir / "http")
    unused_match_store = MatchStore(cache_dir / "matches.sqlite")
    return RiotApiClient(config, http_cache, unused_match_store)


class _InFlightResolution:
    """One shared `resolve_peer_baseline` call, possibly awaited by several callers."""

    __slots__ = ("future", "started")

    def __init__(self, future: "Future[PeerBaseline | None]", started: threading.Event) -> None:
        self.future = future
        self.started = started


class PeersServicer(peers_pb2_grpc.PeersServiceServicer):
    """Implements PeersService by running `resolve_peer_baseline` unmodified.

    A same-thread call is attempted first (`ThreadPoolExecutor.submit` +
    bounded `Future.result(timeout=...)`); when it finishes inside
    `fast_path_timeout_s` (the store/static-fallback levels 0/1/3/4/5, which
    are all local reads), the response carries the resolved baseline directly
    (`cached=True`). When it doesn't -- almost certainly because it fell
    through to level 2's live Riot sampling, or is queued behind other
    in-flight work -- `RequestBaseline` returns immediately with `cached=False`
    and a `request_id`, and lets the same future keep running in the
    background; a completion callback then calls back into
    `RunnerServiceStub.NotifyPeerBaselineReady` with the result. Identical
    concurrent requests (same champion/role/platform/tier) share one
    in-flight resolution instead of each launching their own (see module
    docstring, "Concurrency").
    """

    def __init__(
        self,
        peer_store: PeerSampleStore | None = None,
        riot_client: RiotApiClient | None = None,
        runner_target: str | None = None,
        fast_path_timeout_s: float = FAST_PATH_TIMEOUT_S,
        executor: ThreadPoolExecutor | None = None,
    ) -> None:
        self._peer_store = peer_store if peer_store is not None else _build_default_peer_store()
        self._riot_client = riot_client if riot_client is not None else _build_default_riot_client()
        self._runner_target = runner_target or os.environ.get("RUNNER_GRPC_TARGET", "localhost:50051")
        self._fast_path_timeout_s = fast_path_timeout_s
        self._executor = executor or ThreadPoolExecutor(
            max_workers=int(os.environ.get("PEERS_MAX_CONCURRENT_BASELINES", "4")),
            thread_name_prefix="peers-baseline",
        )
        self._inflight: dict[tuple[str, str, str, str], _InFlightResolution] = {}
        # RLock, not Lock: _get_or_submit calls future.add_done_callback(_cleanup)
        # while still holding this lock. If the resolution is fast enough to
        # already be done by that point (store hits, static fallback -- both
        # near-instant), concurrent.futures invokes the callback SYNCHRONOUSLY,
        # on this same thread, before add_done_callback returns -- and _cleanup
        # itself acquires this lock. A plain Lock deadlocks on that reentrant
        # acquisition; a real, timing-dependent deadlock, not test flakiness --
        # it only shows up when the executor thread wins the race often enough
        # (reliably reproduced under pytest-xdist's parallel workers, rare but
        # possible in production under load too).
        self._inflight_lock = threading.RLock()

    def _get_or_submit(
        self,
        key: tuple[str, str, str, str],
        riot_client: Any,
        adapter: _PeerStoreAdapter,
        ranked: RankedEntry,
        champion: str,
        role: str,
        exclude_puuid: str | None,
    ) -> _InFlightResolution:
        """Return the in-flight resolution for `key`, submitting a new one if needed."""
        with self._inflight_lock:
            existing = self._inflight.get(key)
            if existing is not None and not existing.future.done():
                PEERS_DEDUPED_REQUESTS_TOTAL.inc()
                return existing

            started = threading.Event()

            def _run() -> PeerBaseline | None:
                started.set()
                PEERS_INFLIGHT_BASELINES.inc()
                try:
                    return resolve_peer_baseline(
                        riot_client,
                        adapter,
                        ranked,
                        champion,
                        role,
                        exclude_puuid=exclude_puuid,
                    )
                finally:
                    PEERS_INFLIGHT_BASELINES.dec()

            future = self._executor.submit(_run)
            record = _InFlightResolution(future=future, started=started)
            self._inflight[key] = record

            def _cleanup(_future: "Future[PeerBaseline | None]") -> None:
                with self._inflight_lock:
                    if self._inflight.get(key) is record:
                        del self._inflight[key]

            future.add_done_callback(_cleanup)
            return record

    def RequestBaseline(self, request, context):
        champion = request.champion
        role = request.lane
        tier, division = _parse_rank(request.rank)
        if not champion or not role or not tier:
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details("champion, lane and a parseable rank are required")
            return peers_pb2.RequestBaselineResponse()

        # Request field wins when present; PEERS_PLATFORM is a last-resort
        # default (e.g. for a caller that hasn't been updated yet), then the
        # shared client's own configured platform. See module docstring,
        # "Platform routing" -- silently defaulting to euw1 with no signal was
        # the review round 1 finding this replaces.
        platform = request.platform or os.environ.get("PEERS_PLATFORM") or self._riot_client.platform
        exclude_puuid = request.exclude_puuid or None

        # RankedEntry.league_points/wins/losses are never read anywhere in
        # resolve_peer_baseline's call graph (only `.tier`/`.label`/`.rank`
        # are) -- see analysis/peer/rank_scope.py.
        ranked = RankedEntry(tier=tier, rank=division, league_points=0, wins=0, losses=0)
        adapter = _PeerStoreAdapter(self._peer_store)
        scoped_client = _PlatformScopedRiotClient(self._riot_client, platform)
        request_id = str(uuid.uuid4())

        dedup_key = (champion.lower(), role.upper(), platform.lower(), tier.upper())
        record = self._get_or_submit(
            dedup_key, scoped_client, adapter, ranked, champion, role, exclude_puuid
        )

        try:
            baseline = record.future.result(timeout=self._fast_path_timeout_s)
        except FutureTimeoutError:
            started = record.started.is_set()
            PEERS_FAST_PATH_TIMEOUTS_TOTAL.labels(started=str(started)).inc()
            log.info(
                "RequestBaseline fast path timed out for %s %s (%s), request_id=%s: %s",
                champion,
                role,
                platform,
                request_id,
                "already running (likely live sampling)"
                if started
                else "still queued behind other in-flight work",
            )
            record.future.add_done_callback(
                lambda f: self._on_resolved(f, request_id, champion, role, request.rank)
            )
            return peers_pb2.RequestBaselineResponse(request_id=request_id, cached=False)
        except Exception as exc:  # noqa: BLE001 -- report as a request-level failure
            log.exception("resolve_peer_baseline failed for %s %s", champion, role)
            return peers_pb2.RequestBaselineResponse(request_id=request_id, error=str(exc))

        if baseline is None:
            return peers_pb2.RequestBaselineResponse(
                request_id=request_id,
                cached=True,
                error=f"no peer baseline available for {champion} {role} at {request.rank!r}",
            )
        return peers_pb2.RequestBaselineResponse(
            request_id=request_id,
            cached=True,
            baseline_json=_encode_baseline(baseline),
        )

    def _on_resolved(
        self, future: "Future[PeerBaseline | None]", request_id: str, champion: str, role: str, rank: str
    ) -> None:
        """Runs once a backgrounded resolution finishes (possibly shared by several callers)."""
        try:
            baseline = future.result()
        except Exception as exc:  # noqa: BLE001 -- must still notify RUNNER
            log.exception("Background peer baseline resolution failed for %s %s", champion, role)
            self._notify_runner(request_id, champion, role, rank, baseline_json="", error=str(exc))
            return
        if baseline is None:
            self._notify_runner(
                request_id,
                champion,
                role,
                rank,
                baseline_json="",
                error=f"no peer baseline available for {champion} {role} at {rank!r}",
            )
            return
        self._notify_runner(
            request_id, champion, role, rank, baseline_json=_encode_baseline(baseline), error=""
        )

    def _notify_runner(
        self,
        request_id: str,
        champion: str,
        role: str,
        rank: str,
        *,
        baseline_json: str,
        error: str,
    ) -> None:
        try:
            with grpc.insecure_channel(self._runner_target) as channel:
                stub = runner_pb2_grpc.RunnerServiceStub(channel)
                stub.NotifyPeerBaselineReady(
                    runner_pb2.PeerBaselineReadyRequest(
                        request_id=request_id,
                        champion=champion,
                        lane=role,
                        rank=rank,
                        baseline_json=baseline_json,
                        error=error,
                    )
                )
        except Exception as exc:  # noqa: BLE001 -- a done-callback must never raise silently
            log.error("Failed to notify RUNNER for request_id=%s: %s", request_id, exc)
