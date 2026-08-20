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
task's brief scopes (`PeerSampleStore` only). Deciding whether to wire
`RawMatchStore` in (and giving it the missing per-owner method) is left to
the controller -- see the Task 2 report for the explicit
DONE_WITH_CONCERNS writeup.

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


def _build_default_peer_store() -> PeerSampleStore:
    mongo_uri = os.environ.get("MONGO_URI", "mongodb://localhost:27017/league_stats")
    db_name = mongo_uri.rsplit("/", 1)[-1] or "league_stats"
    client: pymongo.MongoClient = pymongo.MongoClient(mongo_uri)
    return PeerSampleStore(client, db_name=db_name)


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
    """
    api_key = os.environ.get("PEERS_RIOT_API_KEY", "")
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


class PeersServicer(peers_pb2_grpc.PeersServiceServicer):
    """Implements PeersService by running `resolve_peer_baseline` unmodified.

    A same-thread call is attempted first (`ThreadPoolExecutor.submit` +
    bounded `Future.result(timeout=...)`); when it finishes inside
    `fast_path_timeout_s` (the store/static-fallback levels 0/1/3/4/5, which
    are all local reads), the response carries the resolved baseline directly
    (`cached=True`). When it doesn't -- almost certainly because it fell
    through to level 2's live Riot sampling -- `RequestBaseline` returns
    immediately with `cached=False` and a `request_id`, and lets the same
    future keep running in the background; a completion callback then calls
    back into `RunnerServiceStub.NotifyPeerBaselineReady` with the result.
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
            max_workers=4, thread_name_prefix="peers-baseline"
        )
        self._lock = threading.Lock()

    def RequestBaseline(self, request, context):
        champion = request.champion
        role = request.lane
        tier, division = _parse_rank(request.rank)
        if not champion or not role or not tier:
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details("champion, lane and a parseable rank are required")
            return peers_pb2.RequestBaselineResponse()

        # RankedEntry.league_points/wins/losses are never read anywhere in
        # resolve_peer_baseline's call graph (only `.tier`/`.label`/`.rank`
        # are) -- see analysis/peer/rank_scope.py -- and RequestBaselineRequest
        # carries no such data anyway, so they are zeroed here.
        ranked = RankedEntry(tier=tier, rank=division, league_points=0, wins=0, losses=0)
        adapter = _PeerStoreAdapter(self._peer_store)
        request_id = str(uuid.uuid4())

        # RequestBaselineRequest has no puuid field (Phase 0's proto), so there
        # is no player to exclude from their own peer average -- passing None
        # matches resolve_peer_baseline's own default.
        future: Future = self._executor.submit(
            resolve_peer_baseline,
            self._riot_client,
            adapter,
            ranked,
            champion,
            role,
        )

        try:
            baseline = future.result(timeout=self._fast_path_timeout_s)
        except FutureTimeoutError:
            future.add_done_callback(
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
        """Runs on the executor thread once a backgrounded resolution finishes."""
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
        except grpc.RpcError as exc:
            log.error("Failed to notify RUNNER for request_id=%s: %s", request_id, exc)
