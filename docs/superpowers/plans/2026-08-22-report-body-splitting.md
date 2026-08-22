# Split `report_bodies` to fix the 16MB Mongo document limit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reports no longer fail to save (`pymongo.errors.DocumentTooLarge`) once an account has enough games that its rendered report exceeds MongoDB's 16MB per-document limit, and the light `report_builds` listing never again claims `has_report: true` for a build whose body actually failed to save.

**Architecture:** `report_bodies` stops embedding every queue×window dashboard slice (`report_views`, 9 precomputed combinations) and the full 3-queue game-review payload (`game_review`) inline. Those move into two new per-slice/per-build collections (`report_view_slices`, `report_game_review`), fetched on demand for anything other than the default combination — mirroring the existing account-subset on-demand pattern (`POST /api/players/{slug}/builds/{build_slug}/account-views`). The head `report_bodies` document keeps everything currently flattened at the top level (the default bundle's cards/figures, exactly as today) plus a small scalar manifest describing what other combinations exist.

**Tech Stack:** Python (`pymongo`, FastAPI), Svelte (frontend SPA), `mongomock` in tests.

**Spec:** `~/.claude/docs/league-champion-stats-analysis/superpowers/specs/2026-08-22-report-body-splitting-rfc.md`

## Global Constraints

- No live data migration — volumes get wiped on deploy, same as the recent ObjectId migration. Do not write migration code.
- `patch_report_peer_comparison` (`orchestrator.py`) must need **zero changes** — every field it patches (`has_peer_comparison`, `peer_comparison`, `peer_rows`, `generated_at`) is a top-level head field today and stays one under this split (confirmed in the RFC by reading the code directly).
- Do not fold the new collections into `DerivedStore` — `DerivedStore` entries are subject to LRU eviction and code-version purging; `report_view_slices`/`report_game_review` must be durable, unconditionally-present-until-explicitly-deleted storage, same as `report_bodies` today.
- No new frontend test infrastructure — this repo has no existing JS test files; don't invent one for this task.
- Run the full Python test suite (`.venv/bin/python -m pytest -q`) after every backend task's own tests pass.

---

### Task 1: `ReportStore` — add `report_view_slices`/`report_game_review`, split `save_body`

**Files:**
- Modify: `src/league_stats_common/infra/report_store.py`
- Test: `tests/test_reports.py`

**Interfaces:**
- Produces: `ReportStore.save_body(..., view_slices: dict[tuple[str, str], dict[str, Any]], game_review: dict[str, Any] | None)`, `ReportStore.get_view_slice(player_slug, build_slug, queue_key, window_key) -> dict[str, Any] | None`, `ReportStore.get_game_review(player_slug, build_slug) -> dict[str, Any] | None`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_reports.py` (reuse the file's existing `_config`/`_make_records`/`_peer` helpers):

```python
def test_save_body_splits_view_slices_and_game_review_out_of_the_head_document(
    tmp_path: Path,
) -> None:
    """Regression: `report_bodies` must never embed `report_views`/`game_review`
    inline -- that's exactly what made a 598-game report exceed MongoDB's 16MB
    per-document limit in production. `save_body` must persist each
    (queue, window) slice as its own document, and the multi-queue game-review
    payload as its own document, leaving only a scalar manifest in the head.
    """
    with open_report_store() as store:
        store.save_body(
            "player_a",
            "build_a",
            # Deliberately does NOT contain "report_views"/"game_review" keys:
            # the real contract (Task 2) requires the caller to build those
            # as the separate `view_slices`/`game_review` arguments below,
            # never nested inside `report`, so the oversized dict is never
            # assembled at all.
            report={"overview": {"winrate": 0.5}},
            summary={"stats": "ok"},
            view_slices={
                ("solo", "50"): {
                    "total_games": 10,
                    "default_window": "50",
                    "window_options": [{"key": "50", "enabled": True}],
                    "cards": ["default-solo-50"],
                },
                ("flex", "all"): {
                    "total_games": 3,
                    "default_window": "all",
                    "window_options": [{"key": "all", "enabled": True}],
                    "cards": ["flex-all"],
                },
            },
            game_review={
                "solo": {"games": [{"match_id": "EUW1_1"}]},
                "all": {"games": [{"match_id": "EUW1_1"}]},
            },
        )

        head = store.get_report("player_a", "build_a")
        assert "report_views" not in head
        assert "game_review" not in head
        assert head["overview"] == {"winrate": 0.5}
        assert head["view_manifest"]["solo"]["total_games"] == 10
        assert head["view_manifest"]["solo"]["default_window"] == "50"
        assert head["view_manifest"]["flex"]["total_games"] == 3

        solo_slice = store.get_view_slice("player_a", "build_a", "solo", "50")
        assert solo_slice["cards"] == ["default-solo-50"]
        flex_slice = store.get_view_slice("player_a", "build_a", "flex", "all")
        assert flex_slice["cards"] == ["flex-all"]
        assert store.get_view_slice("player_a", "build_a", "flex", "50") is None

        review = store.get_game_review("player_a", "build_a")
        assert review == {
            "solo": {"games": [{"match_id": "EUW1_1"}]},
            "all": {"games": [{"match_id": "EUW1_1"}]},
        }


def test_delete_player_clears_view_slices_and_game_review(tmp_path: Path) -> None:
    with open_report_store() as store:
        store.save_body(
            "player_b",
            "build_a",
            report={"overview": {}},
            summary={},
            view_slices={("solo", "50"): {"cards": []}},
            game_review={"solo": {"games": []}},
        )
        store.delete_player("player_b")
        assert store.get_view_slice("player_b", "build_a", "solo", "50") is None
        assert store.get_game_review("player_b", "build_a") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_reports.py::test_save_body_splits_view_slices_and_game_review_out_of_the_head_document tests/test_reports.py::test_delete_player_clears_view_slices_and_game_review -v`
Expected: FAIL — `save_body` doesn't accept `view_slices`/`game_review` kwargs yet, `get_view_slice`/`get_game_review` don't exist.

- [ ] **Step 3: Write the implementation**

In `src/league_stats_common/infra/report_store.py`:

1. In `__init__`, after the existing `self._bodies.create_index(...)` line, add:
   ```python
   self._view_slices = db["report_view_slices"]
   self._game_reviews = db["report_game_review"]
   self._view_slices.create_index(
       [("player_slug", 1), ("build_slug", 1), ("queue_key", 1), ("window_key", 1)],
       unique=True,
   )
   self._game_reviews.create_index([("player_slug", 1), ("build_slug", 1)], unique=True)
   ```

2. Replace `save_body` with:
   ```python
   def save_body(
       self,
       player_slug: str,
       build_slug: str,
       *,
       report: dict[str, Any],
       summary: dict[str, Any],
       progression_json: dict[str, Any] | None = None,
       progression_md: str = "",
       view_slices: dict[tuple[str, str], dict[str, Any]] | None = None,
       game_review: dict[str, Any] | None = None,
   ) -> None:
       """Upsert one build's heavy report body, split across three collections.

       `view_slices` (one entry per (queue_key, window_key) combination -- the
       old `report["report_views"][queue]["windows"][window]` shape,
       flattened) and `game_review` (the old `report["game_review"]`, the full
       multi-queue payload) are stored in their own collections instead of
       inline: MongoDB's 16MB per-document cap is per-document, not
       per-field, so keeping every queue x window combination in one BSON
       document does not bound total size as games count grows (a build with
       598 games already produced a ~4MB body in the cheapest possible case;
       `report_view_slices` split by combination keeps each individual
       document small and bounded instead). `report` must NOT contain a
       `report_views` or `game_review` key -- callers build those as
       separate `view_slices`/`game_review` arguments instead of nesting
       them into `report`, so the oversized dict is never assembled in the
       first place.

       A `view_manifest` field (scalars only: `total_games`,
       `default_window`, `window_options`, `windows` -- the list of window
       keys that have a slice, not the slices themselves) is derived from
       `view_slices` and added to the head document, so callers reading
       `get_report` still know what other combinations exist without
       fetching every slice.
       """
       view_slices = view_slices or {}
       manifest: dict[str, dict[str, Any]] = {}
       for (queue_key, window_key), bundle in view_slices.items():
           entry = manifest.setdefault(
               queue_key, {"total_games": 0, "default_window": None, "window_options": [], "windows": []}
           )
           entry["windows"].append(window_key)
           entry["total_games"] = bundle.get("total_games", entry["total_games"])
           entry["default_window"] = bundle.get("default_window", entry["default_window"])
           entry["window_options"] = bundle.get("window_options", entry["window_options"])
       doc = {
           "player_slug": player_slug,
           "build_slug": build_slug,
           "report": {**report, "view_manifest": manifest},
           "summary": summary,
           "progression_json": progression_json,
           "progression_md": progression_md,
       }
       self._bodies.replace_one(
           {"player_slug": player_slug, "build_slug": build_slug}, doc, upsert=True
       )
       for (queue_key, window_key), bundle in view_slices.items():
           self._view_slices.replace_one(
               {
                   "player_slug": player_slug,
                   "build_slug": build_slug,
                   "queue_key": queue_key,
                   "window_key": window_key,
               },
               {
                   "player_slug": player_slug,
                   "build_slug": build_slug,
                   "queue_key": queue_key,
                   "window_key": window_key,
                   "bundle": bundle,
               },
               upsert=True,
           )
       if game_review is not None:
           self._game_reviews.replace_one(
               {"player_slug": player_slug, "build_slug": build_slug},
               {
                   "player_slug": player_slug,
                   "build_slug": build_slug,
                   "game_review": game_review,
               },
               upsert=True,
           )
   ```
   Note: `save_body` deliberately does NOT strip `report_views`/`game_review` keys from `report` if a caller passes them -- the contract is that callers (Task 2) must not pass them in the first place, so the oversized dict is never assembled at all.

3. Add after `get_summary`:
   ```python
   def get_view_slice(
       self, player_slug: str, build_slug: str, queue_key: str, window_key: str
   ) -> dict[str, Any] | None:
       """One (queue, window) dashboard bundle, or `None` if never computed."""
       doc = self._view_slices.find_one(
           {
               "player_slug": player_slug,
               "build_slug": build_slug,
               "queue_key": queue_key,
               "window_key": window_key,
           },
           {"bundle": 1},
       )
       return doc.get("bundle") if doc else None

   def get_game_review(self, player_slug: str, build_slug: str) -> dict[str, Any] | None:
       """The full multi-queue game-review payload, or `None`."""
       doc = self._game_reviews.find_one(
           {"player_slug": player_slug, "build_slug": build_slug}, {"game_review": 1}
       )
       return doc.get("game_review") if doc else None
   ```

4. Update `delete_player`:
   ```python
   def delete_player(self, player_slug: str) -> None:
       """Drop every build (listing + body + view slices + game review) for a player."""
       self._builds.delete_many({"player_slug": player_slug})
       self._bodies.delete_many({"player_slug": player_slug})
       self._view_slices.delete_many({"player_slug": player_slug})
       self._game_reviews.delete_many({"player_slug": player_slug})
   ```

5. Update `row_counts`:
   ```python
   def row_counts(self) -> dict[str, int]:
       """Document counts per collection, for parity with other stores' admin tooling."""
       return {
           "report_builds": self._builds.count_documents({}),
           "report_bodies": self._bodies.count_documents({}),
           "report_view_slices": self._view_slices.count_documents({}),
           "report_game_review": self._game_reviews.count_documents({}),
       }
   ```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_reports.py -v`
Expected: all PASS, including every pre-existing test in this file (none of them pass `view_slices`/`game_review` today, so they exercise the `None`/default-empty-dict path -- confirm no regression).

- [ ] **Step 5: Commit**

```bash
git add src/league_stats_common/infra/report_store.py tests/test_reports.py
git commit -m "feat: split report_view_slices/report_game_review out of report_bodies"
```

---

### Task 2: `orchestrator.py` — stop embedding `report_views`/`game_review` in the head document

**Files:**
- Modify: `src/league_stats_runner/pipeline/orchestrator.py`
- Test: `tests/test_reports.py`, `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `ReportStore.save_body(..., view_slices=..., game_review=...)` from Task 1.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_reports.py`:

```python
def test_run_analysis_stores_view_slices_fetchable_via_report_store(tmp_path: Path) -> None:
    """End-to-end: a real `run_analysis` call must leave every (queue, window)
    combination fetchable via `get_view_slice`, and the head report must not
    embed `report_views`/`game_review` inline."""
    ranked = RankedEntry(tier="GOLD", rank="II", league_points=45, wins=80, losses=75)
    records = _make_records()
    peer = _peer(records)
    config = _config(tmp_path, champion="Viktor", role="MIDDLE")

    run_analysis(config, records, peer_comparison=peer, ranked=ranked)

    with open_report_store() as store:
        head = store.get_report(config.reports_group_slug, "viktor_middle")
        assert "report_views" not in head
        assert "game_review" not in head
        manifest = head["view_manifest"]
        default_queue = head["queue_filter_default"]
        default_window = head["game_window_default"]
        assert default_queue in manifest
        assert default_window in manifest[default_queue]["windows"]

        default_slice = store.get_view_slice(
            config.reports_group_slug, "viktor_middle", default_queue, default_window
        )
        assert default_slice is not None
        assert default_slice["overview"] == head["overview"]

        review = store.get_game_review(config.reports_group_slug, "viktor_middle")
        assert review is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_reports.py::test_run_analysis_stores_view_slices_fetchable_via_report_store -v`
Expected: FAIL — today's `run_analysis` still embeds `report_views`/`game_review` in the head and never writes `report_view_slices`/`report_game_review`.

- [ ] **Step 3: Write the implementation**

In `src/league_stats_runner/pipeline/orchestrator.py`, find where `report_payload = context_to_json(context)` is computed and `store.save_body(...)` is called (around line 931-978, right after `context.setdefault("generated_at", ...)`). Insert extraction logic between those two points:

```python
    context.setdefault("generated_at", utc_now_iso())

    player_slug = config.reports_group_slug
    build_slug = champion_slug(config.champion, config.role)
    report_payload = context_to_json(context)

    # `report_views`/`game_review` are popped out here rather than ever being
    # embedded in `report_payload` in the first place: MongoDB's 16MB
    # per-document limit is what a real 598-game build already exceeded when
    # these were nested inline (see design "Splitting report_bodies" RFC).
    # `report_views_popped` keeps queue-level scalars (total_games,
    # default_window, window_options) for the manifest and flattens
    # `windows[window_key]` into `view_slices`, keyed by (queue_key,
    # window_key) -- exactly the shape `ReportStore.save_body` expects.
    report_views_popped = report_payload.pop("report_views", {})
    game_review_popped = report_payload.pop("game_review", None)
    view_slices: dict[tuple[str, str], dict[str, Any]] = {}
    for queue_key, queue_view in report_views_popped.items():
        for window_key, bundle in (queue_view.get("windows") or {}).items():
            bundle_with_meta = {
                **bundle,
                "total_games": queue_view.get("total_games"),
                "default_window": queue_view.get("default_window"),
                "window_options": queue_view.get("window_options"),
            }
            view_slices[(queue_key, window_key)] = bundle_with_meta
```

Then, in the existing `save_build_record(...)` / `store.save_body(...)` block just below, change:
```python
    with open_report_store() as store:
        store.save_body(
            player_slug,
            build_slug,
            report=report_payload,
            summary=summary,
            progression_json=progression_json,
            progression_md=progression_md,
        )
```
to:
```python
    with open_report_store() as store:
        store.save_body(
            player_slug,
            build_slug,
            report=report_payload,
            summary=summary,
            progression_json=progression_json,
            progression_md=progression_md,
            view_slices=view_slices,
            game_review=game_review_popped,
        )
```

Note: `bundle_with_meta`'s extra `total_games`/`default_window`/`window_options` keys are redundant per-slice (they're the same for every window within one queue) but harmless and cheap (scalars, not the expensive `figures`/`cards` content) -- they let `get_view_slice`'s caller (Task 3's endpoint) build a self-contained response without a second lookup against the manifest. Do not remove them as "duplication" -- they're bytes, not megabytes, and simplify the read side.

Also check `_hub_build_fields`'s call site is unaffected: `get_report` still returns everything it read before, minus `report_views`/`game_review` -- `_last_game_at_from_report` reads `report.get("game_review")`, which will now always be absent. This is addressed in Task 3, not here; do not change `app.py` in this task.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_reports.py tests/test_pipeline.py -v`
Expected: all PASS. If any pre-existing test in `test_pipeline.py` or elsewhere asserts on `store.get_report(...)["report_views"]` or `["game_review"]` directly, update it to use `store.get_view_slice(...)`/`store.get_game_review(...)` instead -- search first: `grep -rn '\["report_views"\]\|\["game_review"\]\|get("report_views")\|get("game_review")' tests/` and fix every real hit (not comments).

- [ ] **Step 5: Commit**

```bash
git add src/league_stats_runner/pipeline/orchestrator.py tests/test_reports.py
git commit -m "feat: stop embedding report_views/game_review in the report head document"
```

---

### Task 3: `app.py` — on-demand view-slice endpoint, remove dead `game_review` fallback

**Files:**
- Modify: `src/league_stats_api_ui/app.py`
- Test: `tests/test_web_api.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_web_api.py` (reuse the file's existing test-client/fixture setup -- check its imports for however it builds a `TestClient`/seeds a report via `open_report_store()`/`run_analysis` before writing this):

```python
def test_report_view_slice_endpoint_returns_a_non_default_combination() -> None:
    """GET .../report-views/{queue}/{window} must return exactly what
    ReportStore.save_body stored for that combination, and 404 for one that
    was never computed."""
    with open_report_store() as store:
        store.save_body(
            "player_x",
            "build_x",
            report={"overview": {}},
            summary={},
            view_slices={("flex", "all"): {"total_games": 3, "cards": ["flex-all"]}},
        )
    with open_report_store() as store:
        store._builds.replace_one(
            {"player_slug": "player_x", "build_slug": "build_x"},
            {"player_slug": "player_slug", "build_slug": "build_x", "player_slug": "player_x"},
            upsert=True,
        )

    client = _client()  # reuse whatever this file's existing tests use to build a TestClient
    response = client.get("/api/players/player_x/builds/build_x/report-views/flex/all")
    assert response.status_code == 200
    assert response.json() == {"total_games": 3, "cards": ["flex-all"]}

    missing = client.get("/api/players/player_x/builds/build_x/report-views/flex/50")
    assert missing.status_code == 404
```

Adapt the `_client()` placeholder and the `report_builds` seeding line to whatever this test file's real conventions are -- read a few existing tests in `tests/test_web_api.py` first (in particular one that already calls `open_report_store()` directly, to match its exact seeding pattern rather than guessing at `report_builds`' required fields).

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_web_api.py::test_report_view_slice_endpoint_returns_a_non_default_combination -v`
Expected: FAIL — `404 Not Found` on a route that doesn't exist (route-not-found, not the deliberate "slice not found" 404).

- [ ] **Step 3: Write the implementation**

In `src/league_stats_api_ui/app.py`, add a new route near the existing `build_payload` handler (`GET /api/players/{slug}/builds/{build_slug}`, around line 1554-1562):

```python
    @app.get("/api/players/{slug}/builds/{build_slug}/report-views/{queue_key}/{window_key}")
    def build_view_slice(
        slug: str, build_slug: str, queue_key: str, window_key: str
    ) -> dict[str, Any]:
        """One (queue, window) dashboard bundle, fetched on demand.

        The default combination is already flattened into `build_payload`'s
        response for zero-latency first paint; this endpoint is only called
        by the frontend's queue/window toggle for any OTHER combination,
        mirroring the existing account-views on-demand pattern.
        """
        if not (_is_report_slug(slug) and _is_report_slug(build_slug)):
            raise HTTPException(status_code=400, detail="Invalid report reference.")
        with open_report_store() as report_store:
            bundle = report_store.get_view_slice(slug, build_slug, queue_key, window_key)
        if bundle is None:
            raise HTTPException(status_code=404, detail="Unknown queue/window combination")
        return prepare_web_report_payload(bundle)
```

Then remove the dead `game_review` fallback in `_last_game_at_from_report`/`_hub_build_fields`. Replace:
```python
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
```
with:
```python
def _last_game_at_from_report(report: dict[str, Any]) -> str:
    """Newest match timestamp for a saved report payload.

    `report["game_review"]` no longer exists (see design "Splitting
    report_bodies"): the light `report_builds` listing's own `last_game_at`
    field is always set by `save_build_record` for any build written since
    the Mongo migration, so `_hub_build_fields` only reaches this fallback
    for a listing entry missing that field entirely -- there is nothing left
    in the head report body to derive it from, so this falls straight to
    `generated_at`.
    """
    return str(report.get("generated_at") or "")
```

Do NOT remove the `_hub_build_fields` call site itself or its `needs_report`/`open_report_store()` branch -- `score`/`score_color`/`score_verdict_label` are still legitimately read from the head report body there; only the `game_review`-scanning body of `_last_game_at_from_report` is dead code.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_web_api.py -v`
Expected: all PASS. Search for any other test asserting on `_last_game_at_from_report`'s old per-game-scan behavior (`grep -rn "_last_game_at_from_report\|last_game_at" tests/test_web_api.py`) and update/remove any that specifically exercised the now-deleted scan logic.

- [ ] **Step 5: Commit**

```bash
git add src/league_stats_api_ui/app.py tests/test_web_api.py
git commit -m "feat: add on-demand report-view-slice endpoint, remove dead game_review scan"
```

---

### Task 4: Frontend — on-demand view-slice fetch + cache in `reportState.js`

**Files:**
- Modify: `frontend/src/lib/reportState.js`, `frontend/src/lib/api.js`, `frontend/src/routes/Report.svelte`

No test infra exists for the frontend in this repo -- this task has no automated test steps. Verify manually per Step 3 below instead.

- [ ] **Step 1: Add the API helper**

In `frontend/src/lib/api.js`, add, near `fetchAccountViews`:

```js
export async function fetchViewSlice(slug, buildSlug, queueKey, windowKey) {
  const response = await request(
    `/api/players/${slug}/builds/${buildSlug}/report-views/${queueKey}/${windowKey}`
  );
  if (!response.ok) throw new Error(`Failed to load view: ${response.status}`);
  return rewriteWebAssetHrefs(await response.json());
}
```

- [ ] **Step 2: Rework `reportState.js` to fetch non-default combinations on demand**

The backend no longer sends every `report_views[queue].windows[window]` bundle up front -- only the default combination (flattened at the top level of `payload`, exactly as today) plus a scalar `payload.report_views[queue]` manifest (`total_games`, `default_window`, `window_options`, `windows`: a list of window keys that exist, NOT the bundles). Non-default combinations must be fetched via `fetchViewSlice` and cached, mirroring `accountViewsCache`/`selectAccountKey` exactly.

Replace the whole file's queue/window handling. Key changes:

1. `normalizeBaseSource`/`normalizeAccountSource` no longer assume `report_views[queue].windows` exists -- the manifest only carries queue-level scalars now. Keep them as-is (they still read `payload.report_views || {}`, which is now the manifest shape) but `buildEffectiveView` can no longer assume `queueView.windows[resolvedWindow]` is already there.

2. Add a slice cache alongside `accountViewsCache`, keyed by `` `${accountKey}|${queueKey}|${windowKey}` ``:

```js
export function createReportState(payload, { fetchAccountViews, fetchViewSlice } = {}) {
  const baseSource = normalizeBaseSource(payload);
  const accountFilter = payload.account_filter || {};

  const initialCache = { all: baseSource };
  Object.entries(accountFilter.views || {}).forEach(([key, views]) => {
    initialCache[key] = normalizeAccountSource(views);
  });

  const queue = writable(payload.queue_filter_default);
  const gameWindow = writable(payload.game_window_default);
  const accountKey = writable(accountFilter.default_key || 'all');
  const accountViewsCache = writable(initialCache);
  const accountLoading = writable(false);
  const accountError = writable('');

  // The default (queue, window) combination for the CURRENT account source is
  // already available without a fetch: it's flattened at the top level of
  // `payload` (base source) or of the fetched account-views response (account
  // source) -- see `bundleFields`'s callers below. Only a non-default
  // combination needs `fetchViewSlice`.
  const sliceCache = writable({});
  const sliceLoading = writable(false);
  const sliceError = writable('');

  const activeSource = derived(
    [accountKey, accountViewsCache],
    ([$accountKey, $cache]) => $cache[$accountKey] || $cache.all
  );

  function defaultBundleFor(source, resolvedAccountKey) {
    // The default combination's bundle is whatever was flattened into
    // `payload` (base) or the account-views response (subset) -- both
    // already went through `bundleFields`-shaped keys at the top level, so
    // reconstructing here means reading those same top-level fields back
    // out, not re-deriving them.
    return resolvedAccountKey === (accountFilter.default_key || 'all')
      ? payload
      : get(accountViewsCache)[resolvedAccountKey] || payload;
  }

  const view = derived(
    [queue, gameWindow, activeSource, sliceCache],
    ([$queue, $gameWindow, $source, $slices]) => {
      const resolvedQueue = resolveQueueKey($source, $queue);
      const queueManifest = $source.report_views[resolvedQueue] || EMPTY_QUEUE_VIEW;
      const resolvedWindow = resolveWindowKey(queueManifest, $gameWindow);
      const resolvedAccountKey = get(accountKey);
      const isDefaultCombo =
        resolvedQueue === $source.queue_filter_default && resolvedWindow === queueManifest.default_window;
      const cacheKey = `${resolvedAccountKey}|${resolvedQueue}|${resolvedWindow}`;
      const bundle = isDefaultCombo
        ? defaultBundleFor($source, resolvedAccountKey)
        : $slices[cacheKey] || {};

      const progressionView = $source.progression_views[resolvedQueue];
      const presetKey = progressionView && progressionView.default_preset;
      const preset = progressionView && presetKey ? progressionView.presets[presetKey] : null;

      return {
        ...payload,
        ...bundleFields(bundle),
        ...progressionFields(preset),
        game_review: $source.game_review,
        queue_filter_default: resolvedQueue,
        game_window_default: resolvedWindow,
        game_window_total: queueManifest.total_games,
        game_window_options: queueManifest.window_options || [],
      };
    }
  );

  async function selectQueue(key) {
    await ensureSliceLoaded(key, get(gameWindow));
    queue.set(key);
  }

  async function selectWindow(key) {
    await ensureSliceLoaded(get(queue), key);
    gameWindow.set(key);
  }

  async function ensureSliceLoaded(queueKey, windowKey) {
    const source = get(activeSource);
    const resolvedQueue = resolveQueueKey(source, queueKey);
    const queueManifest = source.report_views[resolvedQueue] || EMPTY_QUEUE_VIEW;
    const resolvedWindow = resolveWindowKey(queueManifest, windowKey);
    const isDefaultCombo =
      resolvedQueue === source.queue_filter_default && resolvedWindow === queueManifest.default_window;
    if (isDefaultCombo) return;
    const resolvedAccountKey = get(accountKey);
    const cacheKey = `${resolvedAccountKey}|${resolvedQueue}|${resolvedWindow}`;
    if (get(sliceCache)[cacheKey]) return;
    if (!fetchViewSlice) {
      sliceError.set('This view is not available in this report.');
      return;
    }
    sliceLoading.set(true);
    sliceError.set('');
    try {
      const bundle = await fetchViewSlice(resolvedQueue, resolvedWindow);
      sliceCache.update((cache) => ({ ...cache, [cacheKey]: bundle }));
    } catch (err) {
      sliceError.set('Could not load this view. Try again.');
    } finally {
      sliceLoading.set(false);
    }
  }

  async function selectAccountKey(key) {
    accountError.set('');
    if (get(accountViewsCache)[key]) {
      accountKey.set(key);
      return;
    }
    if (!fetchAccountViews) {
      accountError.set('This account combination is not available in this report.');
      return;
    }
    accountLoading.set(true);
    try {
      const views = await fetchAccountViews(key.split('|'));
      accountViewsCache.update((cache) => ({ ...cache, [key]: normalizeAccountSource(views) }));
      accountKey.set(key);
    } catch (err) {
      accountError.set('Could not load this account combination. Try again.');
    } finally {
      accountLoading.set(false);
    }
  }

  return {
    queue,
    gameWindow,
    accountKey,
    accountViewsCache,
    accountLoading,
    accountError,
    sliceLoading,
    sliceError,
    activeSource,
    view,
    selectQueue,
    selectWindow,
    selectAccountKey,
  };
}
```

Remove the old synchronous `buildEffectiveView`/`resolveQueueKey`/`resolveWindowKey` exports' old bodies only where shown above -- `resolveQueueKey`/`resolveWindowKey`/`EMPTY_QUEUE_VIEW`/`bundleFields`/`progressionFields`/`normalizeBaseSource`/`normalizeAccountSource` stay exactly as they are today (they already operate on manifest-shaped `report_views[queue]` objects, which is unchanged -- only `windows` stops being present on them, and nothing in those functions reads `.windows` directly except the now-removed `buildEffectiveView`).

Note `selectQueue`/`selectWindow` becoming `async` is a breaking change for any caller that doesn't `await`/doesn't handle the returned promise -- Step 3 below covers updating `Report.svelte`'s call sites.

- [ ] **Step 3: Wire `fetchViewSlice` through `Report.svelte` and verify manually**

In `frontend/src/routes/Report.svelte`, both `createReportState(payload, { fetchAccountViews: ... })` call sites (around lines 95-96 and 289-290) need a `fetchViewSlice` option added alongside `fetchAccountViews`:

```js
report = createReportState(payload, {
  fetchAccountViews: (accounts) => fetchAccountViews(params.slug, params.buildSlug, accounts),
  fetchViewSlice: (queueKey, windowKey) =>
    fetchViewSlice(params.slug, params.buildSlug, queueKey, windowKey),
});
```

Add `fetchViewSlice` to the existing `import { fetchAccountViews, ... } from '../lib/api.js'` line near the top of the file.

Find every place in `Report.svelte` (and any child component) that calls `report.selectQueue(...)`/`report.selectWindow(...)` directly from a click handler -- these are likely already async-compatible (Svelte's `on:click` handlers can be `async` without special handling), but verify none of them assume the call returns synchronously (e.g. reads `$view` immediately after calling `selectQueue` in the same statement, expecting it to already reflect the new selection).

Manual verification (this repo has no frontend test suite, so this replaces automated tests for this task):
1. `cd frontend && npm run dev` (or whatever this repo's existing dev-server command is -- check `frontend/package.json`'s `scripts`).
2. Open a report with more than one queue (a group tracking both solo and flex ranked games) or more than one game-window option.
3. Confirm the report loads showing the default queue/window combination with data (this must never require a network fetch -- verify via browser devtools network tab that no `report-views` request fires on initial load).
4. Toggle to a non-default queue or window. Confirm a `GET .../report-views/{queue}/{window}` request fires exactly once, the view updates with that combination's real data, and toggling back and forth between already-fetched combinations does NOT re-fetch (cache hit).
5. Confirm toggling the account-subset filter (if the test report is a multi-account group) still works, and that switching queue/window after switching account correctly fetches under the NEW account's cache key, not the base source's.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/reportState.js frontend/src/lib/api.js frontend/src/routes/Report.svelte
git commit -m "feat: fetch non-default report queue/window combinations on demand"
```

---

### Task 5: Full-suite verification

**Files:** none (verification-only task).

- [ ] **Step 1: Run the full Python test suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all tests pass, zero failures.

- [ ] **Step 2: Grep for any other reader of the old inline shape**

Run: `grep -rn '"report_views"\]\|\.report_views\b\|"game_review"\]\|\.game_review\b' src/ tests/` (excluding `frontend/`, which Task 4 already covers) and confirm every remaining hit is either: `report_store.py`'s own new `view_slices`/`get_view_slice`/`get_game_review` methods, `reportState.js`-equivalent Python call sites already updated in Tasks 2-3, or a comment/docstring. Fix anything else you find.

- [ ] **Step 3: Report the rollout note**

No commit for this task. Report to the user that this ships the same way the ObjectId migration did: no live migration needed, deploy the new code, then `docker-compose down -v && docker-compose up -d` (or whatever the user's actual next deploy step is at the time) — every collection starts empty and regenerates from Riot's API / re-analysis.
