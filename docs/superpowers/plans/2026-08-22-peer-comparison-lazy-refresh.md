# Lazy peer-comparison refresh on report read Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a report is viewed and its stored peer comparison isn't already maximally confident, check whether a better snapshot has since become available (from the same background `SamplingTask` continuing to refine, per today's earlier fix) and patch the report in place before returning it — so reports served between watch-triggered refreshes still benefit from ongoing background sampling, without any push/notification machinery.

**Architecture:** PEERS gains one new, genuinely read-only RPC (`PeekBaseline`) that only reads its existing live cache — it never enqueues a `SamplingTask`. api-ui, which today has zero PEERS client code (RUNNER is the only existing caller), gets a small gRPC client wired to it. `GET /api/players/{slug}/builds/{build_slug}` calls it only when the stored peer comparison isn't already a high-confidence store hit, and patches via a refactored, RUNNER-independent version of the existing `patch_report_peer_comparison`.

**Tech Stack:** Python, gRPC/protobuf, `pymongo`, FastAPI.

**Spec:** `~/.claude/docs/league-champion-stats-analysis/superpowers/specs/2026-08-22-peers-scheduling-and-cleanup-rfc.md` (the lazy-refresh section)

## Global Constraints

- `PeekBaseline` must never enqueue a `SamplingTask` or otherwise trigger new Riot API work — it only reads `live_benchmark_cache` via the existing `read_live_cache` function. If nothing is cached, it returns "not found," never falls through to live sampling.
- The lazy check must be skipped entirely for a report whose stored peer comparison already came from a maximally-confident source (`confidence == "high"` and `fallback_level <= 1`) — those come from the persistent store, not a `SamplingTask`, and cannot improve via this mechanism.
- Regenerate protos with `scripts/gen_protos.sh` after editing `peers.proto` — never hand-edit `*_pb2.py`/`*_pb2.pyi`.
- Run the full test suite (`.venv/bin/python -m pytest -q`) after every task's own tests pass.

---

### Task 1: `PeerComparisonResult` carries `platform`/`patch`

**Files:**
- Modify: `src/league_stats_common/core/models.py`
- Modify: wherever `PeerComparisonResult` is constructed in PEERS (find via `grep -rn "PeerComparisonResult(" src/league_stats_peers/`)
- Test: `tests/test_peer_baseline.py` or wherever the construction site's existing tests live (check via the grep above which module builds it)

**Interfaces:**
- Produces: `PeerComparisonResult.platform: str`, `PeerComparisonResult.patch: str` — populated fields, not defaults-only, on every real construction site.

- [ ] **Step 1: Write the failing test**

First run `grep -rn "PeerComparisonResult(" src/league_stats_peers/` to find every construction site (there may be more than one — e.g. a "no report yet"/awaiting-peers placeholder construction that legitimately has no real platform/patch to report, vs. the real comparison-building path that does). For the real comparison-building site, add a test in whichever test file already covers it asserting the built `PeerComparisonResult` carries the `platform`/`patch` values that were used to resolve it — e.g. if it's built from a `PeerBaseline` and a `RiotApiClient`, assert `result.platform == client.platform` and `result.patch == baseline_patch` (adapt exact variable names to what you find).

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest <the test file>::<the new test> -v`
Expected: FAIL — `AttributeError` or a default-value mismatch, since the fields don't exist yet / aren't populated.

- [ ] **Step 3: Write the implementation**

1. In `src/league_stats_common/core/models.py`, add to `PeerComparisonResult` (after `fallback_level: int = 0`):
   ```python
   platform: str = ""
   patch: str = ""
   ```
2. At the real comparison-building construction site found above, pass `platform=<the resolved platform>, patch=<the resolved patch>` explicitly into the `PeerComparisonResult(...)` call, using whatever variables that function already has in scope (it necessarily already knows the platform/patch it resolved the baseline against — this is populating fields from data already present, not adding new lookups).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_peer_baseline.py tests/test_peers_service.py tests/test_peer_blend.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/league_stats_common/core/models.py <the modified construction-site file> <the test file>
git commit -m "feat: carry platform/patch on PeerComparisonResult"
```

---

### Task 2: PEERS `PeekBaseline` RPC — read-only live-cache lookup

**Files:**
- Modify: `protos/league_stats_rpc/v1/peers.proto`
- Modify: `src/league_stats_peers/service.py`
- Test: `tests/test_peers_service.py`

**Interfaces:**
- Produces: `PeersServicer.PeekBaseline(request, context) -> PeekBaselineResponse` (gRPC).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_peers_service.py` (reuse this file's existing `PeersServicer` construction/fixture pattern and `write_live_cache`/cache-seeding helpers — check how existing tests in this file seed `live_benchmark_cache` before reading these conventions rather than guessing):

```python
def test_peek_baseline_returns_cached_snapshot_without_enqueuing_a_task() -> None:
    """PeekBaseline must be genuinely read-only: a cache miss returns
    found=False and must NOT start a SamplingTask (confirmed by asserting
    the scheduler's task count is unchanged)."""
    # ... seed a live cache entry for (platform="euw1", tier="EMERALD",
    # champion="Aatrox", role="TOP", patch="16.16") via write_live_cache,
    # matching whatever helper/fixture this file's existing cache-hit tests
    # already use.
    servicer = ...  # build via this file's existing PeersServicer fixture
    response = servicer.PeekBaseline(
        peers_pb2.PeekBaselineRequest(
            champion="Aatrox", lane="TOP", rank="EMERALD III",
            platform="euw1", patch="16.16",
        ),
        context=MagicMock(),
    )
    assert response.found is True
    assert response.baseline_json
    assert response.still_refining in (True, False)


def test_peek_baseline_reports_not_found_without_enqueuing_a_task() -> None:
    """A genuine cache miss must return found=False, never fall through to
    live sampling -- confirmed by checking no SamplingTask was created for
    the key (via the scheduler's own task-count/is_active, matching this
    file's existing convention for asserting scheduler state)."""
    servicer = ...
    response = servicer.PeekBaseline(
        peers_pb2.PeekBaselineRequest(
            champion="Nonexistent", lane="TOP", rank="EMERALD III",
            platform="euw1", patch="16.16",
        ),
        context=MagicMock(),
    )
    assert response.found is False
    # assert the scheduler has no task for this key -- adapt to however
    # this file already inspects scheduler state (e.g. `servicer._scheduler.is_active(key)`).
```

Adapt both tests' setup to this file's real, existing fixture/helper names — read a handful of existing tests in `tests/test_peers_service.py` first.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_peers_service.py::test_peek_baseline_returns_cached_snapshot_without_enqueuing_a_task tests/test_peers_service.py::test_peek_baseline_reports_not_found_without_enqueuing_a_task -v`
Expected: FAIL — `peers_pb2.PeekBaselineRequest` doesn't exist yet, and `PeersServicer.PeekBaseline` doesn't exist yet.

- [ ] **Step 3: Write the implementation**

1. In `protos/league_stats_rpc/v1/peers.proto`, add:
   ```protobuf
   service PeersService {
     rpc RequestBaseline(RequestBaselineRequest) returns (RequestBaselineResponse);
     // Read-only: returns whatever is currently cached for this key, or
     // found=false. Never enqueues a SamplingTask -- callers that want PEERS
     // to actually go fetch data must use RequestBaseline instead. Meant for
     // a cheap "has this improved since I last saved it" check on report
     // read, not for resolving a baseline from scratch.
     rpc PeekBaseline(PeekBaselineRequest) returns (PeekBaselineResponse);
   }

   message PeekBaselineRequest {
     string champion = 1;
     string lane = 2;
     string rank = 3;
     string platform = 4;
     string patch = 5;
   }

   message PeekBaselineResponse {
     bool found = 1;
     string baseline_json = 2;
     bool still_refining = 3;
   }
   ```
2. Run `bash scripts/gen_protos.sh` to regenerate `src/league_stats_rpc/v1/peers_pb2.py`/`peers_pb2_grpc.py`/`peers_pb2.pyi`.
3. In `src/league_stats_peers/service.py`, add a new method to `PeersServicer` (near `RequestBaseline`, reusing its own `_parse_rank`/`VALID_PLATFORMS` validation pattern and the `_encode_baseline` helper already used by `RequestBaseline`/`_on_resolved`):
   ```python
   def PeekBaseline(self, request, context):
       champion = request.champion
       role = request.lane
       tier, _division = _parse_rank(request.rank)
       if not champion or not role or not tier:
           context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
           context.set_details("champion, lane and a parseable rank are required")
           return peers_pb2.PeekBaselineResponse()

       requested_platform = request.platform.strip().lower() if request.platform else ""
       if requested_platform and requested_platform not in VALID_PLATFORMS:
           context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
           context.set_details(
               f"unknown platform {request.platform!r}; must be one of {sorted(VALID_PLATFORMS)}"
           )
           return peers_pb2.PeekBaselineResponse()
       env_platform = os.environ.get("PEERS_PLATFORM", "").strip().lower()
       platform = requested_platform or env_platform or self._default_platform

       snapshot = read_live_cache(platform, tier, champion, role, patch=request.patch)
       if snapshot is None:
           return peers_pb2.PeekBaselineResponse(found=False)
       baseline = _baseline_from_snapshot(snapshot, champion, role, level=2)
       return peers_pb2.PeekBaselineResponse(
           found=True,
           baseline_json=_encode_baseline(baseline),
           still_refining=snapshot.still_refining,
       )
   ```
   Add `read_live_cache`/`_baseline_from_snapshot` to this file's existing imports from `analysis.peer.baseline`/`analysis.peer.benchmark_cache` if not already imported (check the top of `service.py` first — `_baseline_from_snapshot` may already be imported for `_on_resolved`'s use, in which case reuse it rather than re-importing).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_peers_service.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add protos/league_stats_rpc/v1/peers.proto src/league_stats_rpc/v1/peers_pb2.py src/league_stats_rpc/v1/peers_pb2_grpc.py src/league_stats_rpc/v1/peers_pb2.pyi src/league_stats_peers/service.py tests/test_peers_service.py
git commit -m "feat: add read-only PeekBaseline RPC to PEERS"
```

---

### Task 3: Make `patch_report_peer_comparison` callable without RUNNER's `AppConfig`/`BuildPool`

**Files:**
- Modify: `src/league_stats_runner/pipeline/orchestrator.py`
- Modify: `src/league_stats_runner/worker.py` (the one existing caller)
- Test: `tests/test_reports.py`

**Interfaces:**
- Produces: `patch_report_peer_comparison(player_slug: str, build_slug: str, peer_comparison: PeerComparisonResult) -> bool` (signature change — `config: AppConfig, pool: BuildPool` params replaced by the two strings they were only ever used to derive).

- [ ] **Step 1: Write the failing test**

Update the existing tests in `tests/test_reports.py` for this function (`test_patch_report_peer_comparison_updates_peer_fields_and_generated_at`, `test_patch_report_peer_comparison_is_a_noop_without_an_existing_report`) to call it with the new signature:

```python
patched = patch_report_peer_comparison(
    config.reports_group_slug, build_slug, interim_peer
)
```
and
```python
assert patch_report_peer_comparison("zed_slug_placeholder", "zed_middle", _peer(records)) is False
```
(adapt the second test's exact slug to whatever it already used to build `config`/`pool` from — the assertion is the same "no existing report → False", just passing the two strings directly instead of `config`/`pool`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_reports.py::test_patch_report_peer_comparison_updates_peer_fields_and_generated_at tests/test_reports.py::test_patch_report_peer_comparison_is_a_noop_without_an_existing_report -v`
Expected: FAIL — `TypeError` (wrong argument types/count) against the current `(config, pool, peer_comparison)` signature.

- [ ] **Step 3: Write the implementation**

In `src/league_stats_runner/pipeline/orchestrator.py`, change `patch_report_peer_comparison`'s signature and body:
```python
def patch_report_peer_comparison(
    player_slug: str, build_slug: str, peer_comparison: PeerComparisonResult
) -> bool:
    """Cheaply rewrite an already-rendered report's peer-comparison fields in
    place, without re-running the analysis pipeline.

    Takes `player_slug`/`build_slug` directly (not `AppConfig`/`BuildPool`)
    so this is callable from any process with a `ReportStore` -- not just
    RUNNER, which is the only thing that ever had an `AppConfig`/`BuildPool`
    in scope. api-ui's lazy peer-comparison refresh (report read path) calls
    this directly with no RUNNER-specific context at all.

    ...(keep the rest of the existing docstring unchanged)...
    """
    peer_rows = [
        peer_row_display(row.model_dump()) for row in peer_comparison.comparisons
    ]
    serialized_peer_comparison = context_to_json({"peer_comparison": peer_comparison})[
        "peer_comparison"
    ]
    generated_at = utc_now_iso()

    with open_report_store() as store:
        patched_body = store.patch_report_fields(
            player_slug,
            build_slug,
            {
                "has_peer_comparison": True,
                "peer_comparison": serialized_peer_comparison,
                "peer_rows": peer_rows,
                "generated_at": generated_at,
            },
        )
        if not patched_body:
            return False
        store.patch_build_fields(
            player_slug,
            build_slug,
            {"has_peer_comparison": True, "generated_at": generated_at},
        )
        return True
```
(Remove the now-unused `config`/`pool` parameter references; `player_slug`/`build_slug` are no longer derived internally via `config.reports_group_slug`/`champion_slug(pool.champion, pool.role)` -- they're passed in directly.)

In `src/league_stats_runner/worker.py`, find the one call site (in `_run_stage_b`, inside `_on_peer_update`) and change:
```python
patched = patch_report_peer_comparison(services.config, pool, peer_comparison)
```
to:
```python
patched = patch_report_peer_comparison(
    services.config.reports_group_slug, champion_slug(pool.champion, pool.role), peer_comparison
)
```
(`champion_slug` should already be imported in `worker.py` — check; if not, add the import from wherever `orchestrator.py` imports it from.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_reports.py tests/test_web_worker.py -v`
Expected: all PASS. Search for any other call site: `grep -rn "patch_report_peer_comparison(" src/ tests/` and update any you missed.

- [ ] **Step 5: Commit**

```bash
git add src/league_stats_runner/pipeline/orchestrator.py src/league_stats_runner/worker.py tests/test_reports.py
git commit -m "refactor: patch_report_peer_comparison takes player_slug/build_slug directly"
```

---

### Task 4: api-ui — lazy refresh on report read

**Files:**
- Modify: `src/league_stats_api_ui/app.py`
- Test: `tests/test_web_api.py`

**Interfaces:**
- Consumes: `PeersServiceStub.PeekBaseline` (Task 2), `patch_report_peer_comparison(player_slug, build_slug, peer_comparison)` (Task 3).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_web_api.py` (reuse this file's existing test-client/report-seeding conventions, and its existing pattern for mocking a gRPC stub -- check `tests/test_web_worker.py`'s `RunnerServiceStub`-mocking tests for how this codebase already fakes a gRPC stub without a real server, since api-ui has no precedent of its own to copy yet):

```python
def test_report_read_patches_a_stale_peer_comparison_from_peek_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A report whose stored peer comparison is not already maximally
    confident must be refreshed from PEEK-ed live-cache data on read, if
    PEERS reports something better is now available."""
    with open_report_store() as store:
        # seed a build whose report has a low-confidence, still-refining
        # peer_comparison (confidence != "high", fallback_level > 1) --
        # mirror this file's existing report-seeding helper/fixture.
        ...

    class FakeStub:
        def PeekBaseline(self, request, timeout=None):
            return peers_pb2.PeekBaselineResponse(
                found=True,
                baseline_json=json.dumps({
                    "rank_label": "Emerald III", "tier": "EMERALD", "rank_badge": "III",
                    "champion": "Aatrox", "role": "TOP", "build_label": "Aatrox top",
                    "source": "improved", "peer_games": 70, "peer_players": 40,
                    "confidence": "full", "fallback_level": 2, "comparisons": [],
                    "strengths": [], "weaknesses": [], "platform": "euw1", "patch": "16.16",
                }),
                still_refining=True,
            )
    monkeypatch.setattr(web_app, "_peers_stub", lambda: FakeStub())

    client = _client()  # this file's existing TestClient helper
    response = client.get("/api/players/<seeded-slug>/builds/<seeded-build-slug>")
    assert response.status_code == 200
    body = response.json()
    assert body["peer_comparison"]["peer_games"] == 70


def test_report_read_skips_peek_for_already_high_confidence_peer_comparison(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A report whose peer comparison is already confidence=high,
    fallback_level<=1 must never call PeekBaseline at all -- it came from
    the persistent store, not a SamplingTask, and cannot improve this way."""
    with open_report_store() as store:
        # seed a build with confidence="high", fallback_level=0
        ...

    called = []
    class FakeStub:
        def PeekBaseline(self, request, timeout=None):
            called.append(request)
            raise AssertionError("PeekBaseline must not be called for a high-confidence report")
    monkeypatch.setattr(web_app, "_peers_stub", lambda: FakeStub())

    client = _client()
    response = client.get("/api/players/<seeded-slug>/builds/<seeded-build-slug>")
    assert response.status_code == 200
    assert called == []
```

Adapt the seeding helpers and `_client()` placeholder to this file's real conventions -- read a few existing tests first rather than guessing at fixture shapes.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_web_api.py::test_report_read_patches_a_stale_peer_comparison_from_peek_baseline tests/test_web_api.py::test_report_read_skips_peek_for_already_high_confidence_peer_comparison -v`
Expected: FAIL — `web_app._peers_stub` doesn't exist yet, and `build_payload` doesn't call it.

- [ ] **Step 3: Write the implementation**

In `src/league_stats_api_ui/app.py`:

1. Add a module-level PEERS gRPC client builder, mirroring how `worker.py` builds its own `RunnerServiceStub`/`PeersServiceStub` channel (check `worker.py`'s exact channel-construction pattern -- likely `grpc.insecure_channel(target)` wrapped with `TraceClientInterceptor`, reuse that exact shape rather than inventing a different one):
   ```python
   def _peers_stub() -> "peers_pb2_grpc.PeersServiceStub":
       channel = grpc.intercept_channel(
           grpc.insecure_channel(config.peers_grpc_target), TraceClientInterceptor()
       )
       return peers_pb2_grpc.PeersServiceStub(channel)
   ```
   Add `peers_pb2`/`peers_pb2_grpc`/`grpc`/`TraceClientInterceptor` imports at the top of the file if not already present (check first -- `grpc` may already be imported for something else).

2. Add a helper that does the gated peek-and-patch:
   ```python
   _PEEK_TIMEOUT_S = 5.0

   def _maybe_refresh_peer_comparison(
       player_slug: str, build_slug: str, report: dict[str, Any]
   ) -> dict[str, Any]:
       """Check PEERS for a better peer-comparison snapshot and patch in
       place if one exists, before returning the (possibly updated) report.

       Skipped entirely for an already-maximally-confident comparison
       (confidence == "high" and fallback_level <= 1) -- those come from the
       persistent peer_games store, not a SamplingTask, and cannot improve
       via this mechanism. See design "PEERS priority scheduling..." RFC's
       lazy-refresh section.
       """
       peer = report.get("peer_comparison")
       if not isinstance(peer, dict):
           return report
       if peer.get("confidence") == "high" and int(peer.get("fallback_level", 0)) <= 1:
           return report
       champion = str(peer.get("champion", ""))
       role = str(peer.get("role", ""))
       platform = str(peer.get("platform", ""))
       patch = str(peer.get("patch", ""))
       tier = str(peer.get("tier", ""))
       if not (champion and role and platform and tier):
           return report
       try:
           response = _peers_stub().PeekBaseline(
               peers_pb2.PeekBaselineRequest(
                   champion=champion, lane=role, rank=tier, platform=platform, patch=patch,
               ),
               timeout=_PEEK_TIMEOUT_S,
           )
       except grpc.RpcError:
           return report
       if not response.found:
           return report
       try:
           parsed = json.loads(response.baseline_json)
       except (TypeError, ValueError, json.JSONDecodeError):
           return report
       if int(parsed.get("peer_games", 0)) <= int(peer.get("peer_games", 0)):
           return report
       updated = PeerComparisonResult.model_validate(parsed)
       if not patch_report_peer_comparison(player_slug, build_slug, updated):
           return report
       with open_report_store() as store:
           refreshed = store.get_report(player_slug, build_slug)
       return refreshed if refreshed is not None else report
   ```
   Add `PeerComparisonResult`/`patch_report_peer_comparison` imports if not already present (check -- `patch_report_peer_comparison` may already be imported for a different route).

3. In `build_payload` (the `GET /api/players/{slug}/builds/{build_slug}` handler, around line 1554-1562), call it before returning:
   ```python
   @app.get("/api/players/{slug}/builds/{build_slug}")
   def build_payload(slug: str, build_slug: str) -> dict[str, Any]:
       if not (_is_report_slug(slug) and _is_report_slug(build_slug)):
           raise HTTPException(status_code=400, detail="Invalid report reference.")
       with open_report_store() as report_store:
           payload = report_store.get_report(slug, build_slug)
       if payload is None:
           raise HTTPException(status_code=404, detail="Unknown build")
       payload = _maybe_refresh_peer_comparison(slug, build_slug, payload)
       return prepare_web_report_payload(payload)
   ```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_web_api.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/league_stats_api_ui/app.py tests/test_web_api.py
git commit -m "feat: lazily refresh a report's peer comparison from PEEK-ed live-cache data on read"
```

---

### Task 5: Full-suite verification

**Files:** none (verification-only task).

- [ ] **Step 1: Run the full test suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all tests pass, zero failures.

- [ ] **Step 2: Confirm docker-compose already wires api-ui to PEERS**

Run: `grep -n "PEERS_GRPC_TARGET" docker-compose.yml`. Expected: `api-ui`'s service block already sets this env var (confirmed present before this plan started) -- no docker-compose change needed for this feature. If it's missing, add `- PEERS_GRPC_TARGET=peers:50053` to `api-ui`'s `environment:` block, matching `runner`'s existing entry exactly, and note this as a deviation in your final report.

- [ ] **Step 3: Report the rollout note**

No commit for this task. Report to the user that this ships as a normal code deploy — no new volumes, no schema migration, nothing to wipe. `docker-compose up -d` (rebuild + restart api-ui and peers) is sufficient once this merges.
