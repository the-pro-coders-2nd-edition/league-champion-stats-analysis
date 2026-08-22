# Migrate every Mongo store to real ObjectId `_id`s Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every MongoDB collection in this app stops using a synthetic/natural string (or int) as `_id` and instead lets MongoDB assign a real `ObjectId`, so mongo-express's document-detail view (which crashes on any non-ObjectId `_id`) stops crashing on normal admin browsing.

**Architecture:** For each of the 8 store modules, the natural key that used to be baked into `_id` moves into its own real field(s) backed by a (compound) unique index, preserving the exact idempotent-upsert / duplicate-rejection semantics each store relies on today. `jobs.py` is the one module where `_id` is also an ordering primitive (FIFO queue position, duration stats), not just an identifier — its new `job_id` field takes over that role. No live data migration: volumes are wiped on deploy (`docker-compose down -v`), so there is no in-place `_id` rewrite step.

**Tech Stack:** Python, `pymongo` 4.17.0 (pinned), `mongomock` 4.3.0 (pinned, used in all tests), FastAPI (api-ui).

**Spec:** `~/.claude/docs/league-champion-stats-analysis/superpowers/specs/2026-08-22-mongo-objectid-ids-design.md`

## Global Constraints

- Never set `"_id"` in an inserted/replaced document. Let MongoDB assign a real `ObjectId` automatically.
- `replace_one(filter, doc, upsert=True)` and `update_one(filter, update, upsert=True)` both auto-populate the new document's fields from the filter's equality conditions when no `$set` overrides them — this is the exact mechanism that makes today's `_id`-based upserts work, and is relied on identically after migration (no new risk).
- Every former `_id`-based filter, sort, or comparison switches to the new natural-key field(s), backed by a `unique=True` index (compound where the key has multiple parts, `partialFilterExpression` where a store already relies on one).
- `mongomock` 4.3.0 supports unique indexes and raises `pymongo.errors.DuplicateKeyError` the same way real MongoDB does — already relied on today by `jobs.py`'s partial unique index and `peer_sample_store.py`'s dedup behavior, so no new test-infra risk.
- This repo's pinned `pymongo`/`mongomock` combination is incompatible with `bulk_write(UpdateOne(...))` — irrelevant to this migration since no task introduces `bulk_write`.
- Run the full suite (`.venv/bin/python -m pytest -q`) after every task's own tests pass, to catch cross-file regressions early.

---

### Task 1: Defensive ObjectId JSON encoder in api-ui

**Files:**
- Modify: `src/league_stats_api_ui/app.py` (near the top, after imports)
- Test: `tests/test_web_api.py`

**Interfaces:**
- Produces: nothing new consumed by later tasks — this is a standalone defensive addition.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_web_api.py` (check the existing imports at the top of that file and reuse whatever test-client fixture it already has — e.g. a `client` fixture built from `create_app`):

```python
def test_objectid_is_json_serializable_via_fastapi_encoders() -> None:
    """Defensive: any future route that accidentally forwards a raw Mongo
    document (containing a real ObjectId `_id`, post-migration) must not
    500 on JSON serialization. FastAPI's jsonable_encoder has no built-in
    support for ObjectId; app.py must register one explicitly."""
    from bson import ObjectId
    from fastapi.encoders import jsonable_encoder

    encoded = jsonable_encoder({"_id": ObjectId(), "name": "test"})
    assert isinstance(encoded["_id"], str)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_web_api.py::test_objectid_is_json_serializable_via_fastapi_encoders -v`
Expected: FAIL — `encoded["_id"]` is not a string (jsonable_encoder either raises or returns the raw ObjectId depending on FastAPI version; either way the assertion fails).

- [ ] **Step 3: Write minimal implementation**

In `src/league_stats_api_ui/app.py`, near the top, after the existing `from fastapi import ...` imports:

```python
from bson import ObjectId
from fastapi.encoders import ENCODERS_BY_TYPE

ENCODERS_BY_TYPE[ObjectId] = str
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_web_api.py::test_objectid_is_json_serializable_via_fastapi_encoders -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/league_stats_api_ui/app.py tests/test_web_api.py
git commit -m "feat: register ObjectId JSON encoder defensively in api-ui"
```

---

### Task 2: `derived.py` — switch `derived` collection to real ObjectId `_id`

**Files:**
- Modify: `src/league_stats_runner/infra/derived.py`
- Test: `tests/test_derived_store.py`

**Interfaces:**
- Produces: compound unique index on `(kind, key, code_version)` on the `derived` collection. `_doc_id` static method is removed.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_derived_store.py` (reuse whatever store-construction helper the file already has, e.g. a `mongomock.MongoClient()`-backed fixture):

```python
def test_derived_id_is_a_real_objectid_not_the_composite_key() -> None:
    """mongo-express crashes opening a document whose `_id` isn't a real
    ObjectId. Every derived document's `_id` must be Mongo-assigned."""
    from bson import ObjectId

    store = DerivedStore(mongomock.MongoClient())
    store.put(KIND_RECORD, "EUW1_1", {"cs": 188})
    doc = store._derived.find_one({"kind": KIND_RECORD, "key": "EUW1_1"})
    assert isinstance(doc["_id"], ObjectId)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_derived_store.py::test_derived_id_is_a_real_objectid_not_the_composite_key -v`
Expected: FAIL — `doc["_id"]` is currently a composite string, not an `ObjectId`.

- [ ] **Step 3: Write minimal implementation**

In `src/league_stats_runner/infra/derived.py`:

1. Delete the `_doc_id` static method (lines 180-182).
2. In `__init__`, after the existing `self._derived.create_index("hit_at")` line, add:
   ```python
   self._derived.create_index(
       [("kind", 1), ("key", 1), ("code_version", 1)], unique=True
   )
   ```
3. In `get`, replace:
   ```python
   doc = self._derived.find_one({"_id": self._doc_id(kind, key, code_version(kind))})
   ```
   with:
   ```python
   doc = self._derived.find_one(
       {"kind": kind, "key": key, "code_version": code_version(kind)}
   )
   ```
4. In `get_many`, replace:
   ```python
   ids = [self._doc_id(kind, key, version) for key in batch]
   for doc in self._derived.find({"_id": {"$in": ids}}):
       found[doc["key"]] = doc["payload"]
   ```
   with:
   ```python
   for doc in self._derived.find(
       {"kind": kind, "key": {"$in": batch}, "code_version": version}
   ):
       found[doc["key"]] = doc["payload"]
   ```
5. In `put`, replace:
   ```python
   self._derived.update_one(
       {"_id": self._doc_id(kind, key, version)},
       {
   ```
   with:
   ```python
   self._derived.update_one(
       {"kind": kind, "key": key, "code_version": version},
       {
   ```
   (the `"$set"` block inside is unchanged — it already sets `kind`/`key`/`code_version` explicitly.)
6. Apply the identical filter change to `put_many` (same shape as `put`).
7. In `_touch`, replace:
   ```python
   self._derived.update_one(
       {"_id": self._doc_id(kind, key, code_version(kind))},
       {"$set": {"hit_at": time.time()}},
   )
   ```
   with:
   ```python
   self._derived.update_one(
       {"kind": kind, "key": key, "code_version": code_version(kind)},
       {"$set": {"hit_at": time.time()}},
   )
   ```
8. In `_touch_many`, replace:
   ```python
   self._derived.update_one(
       {"_id": self._doc_id(kind, key, version)}, {"$set": {"hit_at": now}}
   )
   ```
   with:
   ```python
   self._derived.update_one(
       {"kind": kind, "key": key, "code_version": version}, {"$set": {"hit_at": now}}
   )
   ```
9. `evict_to_budget` needs no change — it reads whatever `_id` a document has (`{"_id": 1}` projection) and deletes by that same value; it never assumes a shape for `_id`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_derived_store.py -v`
Expected: all PASS. Two pre-existing tests reference `DerivedStore._doc_id` directly (`test_created_at_is_stamped_once_not_on_every_overwrite`, and the stale-version test around line 116) — update them:
- Replace `doc_id = DerivedStore._doc_id(KIND_RECORD, "a", code_version(KIND_RECORD))` / `store._derived.find_one({"_id": doc_id})` with `store._derived.find_one({"kind": KIND_RECORD, "key": "a", "code_version": code_version(KIND_RECORD)})`.
- Replace the manually-inserted doc's `"_id": DerivedStore._doc_id(KIND_RECORD, "a", "old-version")` key with `"kind": KIND_RECORD, "key": "a", "code_version": "old-version"` (the doc already sets `kind`/`key`/`code_version` as separate fields elsewhere in that test — check for duplicates and remove the old `_id` line entirely).

- [ ] **Step 5: Commit**

```bash
git add src/league_stats_runner/infra/derived.py tests/test_derived_store.py
git commit -m "feat: use real ObjectId _id for the derived-artifact cache"
```

---

### Task 3: `peer_match_sample_store.py` — switch `peer_match_samples` to real ObjectId `_id`

**Files:**
- Modify: `src/league_stats_peers/infra/peer_match_sample_store.py`
- Test: `tests/test_peer_match_sample_store.py`

- [ ] **Step 1: Write the failing test**

```python
def test_peer_match_sample_id_is_a_real_objectid() -> None:
    from bson import ObjectId

    store = PeerMatchSampleStore(mongomock.MongoClient())
    store.upsert_rows(
        "EUW1_1", "15.1", "euw1", [{"puuid": "p1", "champion": "Viktor", "role": "MIDDLE"}]
    )
    doc = store._samples.find_one({"match_id": "EUW1_1", "puuid": "p1"})
    assert isinstance(doc["_id"], ObjectId)
```

(Adapt the exact `upsert_rows` row shape to whatever the file's existing tests already use — check `tests/test_peer_match_sample_store.py`'s existing fixtures/rows for the real minimal row shape before writing this.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_peer_match_sample_store.py::test_peer_match_sample_id_is_a_real_objectid -v`
Expected: FAIL.

- [ ] **Step 3: Write minimal implementation**

In `src/league_stats_peers/infra/peer_match_sample_store.py`:

1. Delete the `_doc_id` static method.
2. In `__init__`, after the existing `create_index([("platform", 1), ...])` line, add:
   ```python
   self._samples.create_index([("match_id", 1), ("puuid", 1)], unique=True)
   ```
3. In `upsert_rows`, replace:
   ```python
   doc = {
       "_id": self._doc_id(match_id, puuid),
       "match_id": match_id,
       ...
   }
   self._samples.replace_one({"_id": doc["_id"]}, doc, upsert=True)
   ```
   with:
   ```python
   doc = {
       "match_id": match_id,
       ...
   }
   self._samples.replace_one(
       {"match_id": match_id, "puuid": puuid}, doc, upsert=True
   )
   ```
   (drop the `"_id": self._doc_id(match_id, puuid),` line from the `doc` dict entirely; every other key in `doc` is unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_peer_match_sample_store.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/league_stats_peers/infra/peer_match_sample_store.py tests/test_peer_match_sample_store.py
git commit -m "feat: use real ObjectId _id for peer_match_samples"
```

---

### Task 4: `report_store.py` — switch `report_builds`/`report_bodies` to real ObjectId `_id`

**Files:**
- Modify: `src/league_stats_common/infra/report_store.py`
- Test: `tests/test_reports.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_reports.py`:

```python
def test_report_build_id_is_a_real_objectid(tmp_path: Path) -> None:
    from bson import ObjectId

    ranked = RankedEntry(tier="GOLD", rank="II", league_points=45, wins=80, losses=75)
    records = _make_records()
    peer = _peer(records)
    config = _config(tmp_path, champion="Viktor", role="MIDDLE")
    run_analysis(config, records, peer_comparison=peer, ranked=ranked)

    with open_report_store() as store:
        build_doc = store._builds.find_one(
            {"player_slug": config.reports_group_slug, "build_slug": "viktor_middle"}
        )
        body_doc = store._bodies.find_one(
            {"player_slug": config.reports_group_slug, "build_slug": "viktor_middle"}
        )
    assert isinstance(build_doc["_id"], ObjectId)
    assert isinstance(body_doc["_id"], ObjectId)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_reports.py::test_report_build_id_is_a_real_objectid -v`
Expected: FAIL.

- [ ] **Step 3: Write minimal implementation**

In `src/league_stats_common/infra/report_store.py`:

1. Delete the `build_id` module-level function.
2. In `__init__`, replace:
   ```python
   self._builds.create_index("player_slug")
   ```
   with:
   ```python
   self._builds.create_index("player_slug")
   self._builds.create_index([("player_slug", 1), ("build_slug", 1)], unique=True)
   self._bodies.create_index([("player_slug", 1), ("build_slug", 1)], unique=True)
   ```
3. `save_build`: replace
   ```python
   doc = {**meta, "_id": build_id(player_slug, build_slug), "player_slug": player_slug,
          "build_slug": build_slug, "match_ids": sorted(set(match_ids))}
   self._builds.replace_one({"_id": doc["_id"]}, doc, upsert=True)
   ```
   with
   ```python
   doc = {**meta, "player_slug": player_slug,
          "build_slug": build_slug, "match_ids": sorted(set(match_ids))}
   self._builds.replace_one(
       {"player_slug": player_slug, "build_slug": build_slug}, doc, upsert=True
   )
   ```
4. `get_build`: replace `{"_id": build_id(player_slug, build_slug)}` with `{"player_slug": player_slug, "build_slug": build_slug}`.
5. `has_build`: same filter swap.
6. `match_ids_for_build`: same filter swap.
7. `patch_build_fields`: same filter swap (used by `update_one`).
8. `save_body`: replace
   ```python
   doc = {
       "_id": build_id(player_slug, build_slug),
       "player_slug": player_slug,
       "build_slug": build_slug,
       "report": report,
       "summary": summary,
       "progression_json": progression_json,
       "progression_md": progression_md,
   }
   self._bodies.replace_one({"_id": doc["_id"]}, doc, upsert=True)
   ```
   with
   ```python
   doc = {
       "player_slug": player_slug,
       "build_slug": build_slug,
       "report": report,
       "summary": summary,
       "progression_json": progression_json,
       "progression_md": progression_md,
   }
   self._bodies.replace_one(
       {"player_slug": player_slug, "build_slug": build_slug}, doc, upsert=True
   )
   ```
9. `patch_report_fields`, `get_report`, `get_summary`: same filter swap (`{"player_slug": ..., "build_slug": ...}` instead of `{"_id": build_id(...)}`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_reports.py -v`
Expected: all PASS (no other test in this file references `build_id` or `_id` directly — verified during design research).

- [ ] **Step 5: Commit**

```bash
git add src/league_stats_common/infra/report_store.py tests/test_reports.py
git commit -m "feat: use real ObjectId _id for report_builds/report_bodies"
```

---

### Task 5: `raw_match_store.py` — switch `matches`/`timelines` to real ObjectId `_id`

**Files:**
- Modify: `src/league_stats_runner/infra/raw_match_store.py`
- Test: `tests/test_raw_match_store.py`

- [ ] **Step 1: Write the failing test**

```python
def test_raw_match_id_is_a_real_objectid() -> None:
    from bson import ObjectId

    store = RawMatchStore(mongomock.MongoClient())
    store.save_match("EUW1_1", "puuid-a", {"payload": "data"})
    store.save_timeline("EUW1_1", {"frames": []})
    match_doc = store._matches.find_one({"match_id": "EUW1_1"})
    timeline_doc = store._timelines.find_one({"match_id": "EUW1_1"})
    assert isinstance(match_doc["_id"], ObjectId)
    assert isinstance(timeline_doc["_id"], ObjectId)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_raw_match_store.py::test_raw_match_id_is_a_real_objectid -v`
Expected: FAIL.

- [ ] **Step 3: Write minimal implementation**

In `src/league_stats_runner/infra/raw_match_store.py`:

1. In `__init__`, after `self._timelines = db["timelines"]`, add:
   ```python
   self._matches.create_index("match_id", unique=True)
   self._timelines.create_index("match_id", unique=True)
   ```
2. `has_match`: replace
   ```python
   if self._matches.find_one({"_id": match_id}, {"_id": 1}) is None:
       return False
   return self._timelines.find_one({"_id": match_id}, {"_id": 1}) is not None
   ```
   with
   ```python
   if self._matches.find_one({"match_id": match_id}, {"_id": 1}) is None:
       return False
   return self._timelines.find_one({"match_id": match_id}, {"_id": 1}) is not None
   ```
3. `save_match`: replace
   ```python
   self._matches.update_one(
       {"_id": match_id},
       {"$set": {"payload": match}, "$addToSet": {"owners": puuid}},
       upsert=True,
   )
   ```
   with
   ```python
   self._matches.update_one(
       {"match_id": match_id},
       {"$set": {"payload": match}, "$addToSet": {"owners": puuid}},
       upsert=True,
   )
   ```
4. `save_timeline`: replace `{"_id": match_id}` with `{"match_id": match_id}`.
5. `load_match`: replace `{"_id": match_id}` with `{"match_id": match_id}`.
6. `load_timeline`: replace `{"_id": match_id}` with `{"match_id": match_id}`.
7. `claim_ownership`: replace
   ```python
   result = self._matches.update_one(
       {"_id": match_id}, {"$addToSet": {"owners": puuid}}
   )
   ```
   with
   ```python
   result = self._matches.update_one(
       {"match_id": match_id}, {"$addToSet": {"owners": puuid}}
   )
   ```
8. `iter_all_match_ids`: replace
   ```python
   for doc in self._matches.find({}, {"_id": 1}):
       yield doc["_id"]
   ```
   with
   ```python
   for doc in self._matches.find({}, {"match_id": 1}):
       yield doc["match_id"]
   ```
9. `count`: replace
   ```python
   match_ids = {doc["_id"] for doc in self._matches.find({}, {"_id": 1})}
   timeline_ids = {doc["_id"] for doc in self._timelines.find({}, {"_id": 1})}
   ```
   with
   ```python
   match_ids = {doc["match_id"] for doc in self._matches.find({}, {"match_id": 1})}
   timeline_ids = {doc["match_id"] for doc in self._timelines.find({}, {"match_id": 1})}
   ```
10. `iter_match_ids`: replace
    ```python
    for doc in self._matches.find({"owners": puuid}, {"_id": 1}):
        yield doc["_id"]
    ```
    with
    ```python
    for doc in self._matches.find({"owners": puuid}, {"match_id": 1}):
        yield doc["match_id"]
    ```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_raw_match_store.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/league_stats_runner/infra/raw_match_store.py tests/test_raw_match_store.py
git commit -m "feat: use real ObjectId _id for raw matches/timelines"
```

---

### Task 6: `peer_sample_store.py` — switch `peer_games` to real ObjectId `_id`

**Files:**
- Modify: `src/league_stats_peers/infra/peer_sample_store.py`
- Test: `tests/test_peer_sample_store.py`

- [ ] **Step 1: Write the failing test**

Check `tests/test_peer_sample_store.py`'s existing `_row(...)` helper (used throughout the file, e.g. `_row(puuid=..., champion=..., match_id=...)`) and reuse it:

```python
def test_peer_game_id_is_a_real_objectid() -> None:
    from bson import ObjectId

    store = PeerSampleStore(mongomock.MongoClient())
    store.upsert_peer_game(_row())
    doc = store._peer_games.find_one({})
    assert isinstance(doc["_id"], ObjectId)


def test_upsert_peer_game_still_dedups_on_the_same_tuple() -> None:
    store = PeerSampleStore(mongomock.MongoClient())
    row = _row()
    assert store.upsert_peer_game(row) is True
    assert store.upsert_peer_game(row) is False
```

(The second test guards the duplicate-rejection behavior that used to come from `_id` collision, now from the new unique index — likely already covered by an existing test in this file; add it only if an equivalent doesn't already exist after checking the file.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_peer_sample_store.py::test_peer_game_id_is_a_real_objectid -v`
Expected: FAIL.

- [ ] **Step 3: Write minimal implementation**

In `src/league_stats_peers/infra/peer_sample_store.py`:

1. Delete the `_dedup_key` static method.
2. In `__init__`, after the existing two `create_index` calls, add:
   ```python
   self._peer_games.create_index(
       [("match_id", 1), ("puuid", 1), ("champion", 1), ("role", 1)], unique=True
   )
   ```
3. In `upsert_peer_game`, replace:
   ```python
   doc = {
       "_id": self._dedup_key(match_id, puuid, champion, role),
       "match_id": match_id,
       ...
   }
   ```
   with:
   ```python
   doc = {
       "match_id": match_id,
       ...
   }
   ```
   (drop only the `"_id": ...,` line; the `try: self._peer_games.insert_one(doc) except DuplicateKeyError: return False` block below is unchanged — `insert_one` now raises `DuplicateKeyError` off the new compound unique index instead of an `_id` collision, same caller-visible behavior.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_peer_sample_store.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/league_stats_peers/infra/peer_sample_store.py tests/test_peer_sample_store.py
git commit -m "feat: use real ObjectId _id for peer_games"
```

---

### Task 7: `live_benchmark_cache_store.py` — switch `live_benchmark_cache` to real ObjectId `_id`

**Files:**
- Modify: `src/league_stats_peers/infra/live_benchmark_cache_store.py`
- Test: create `tests/test_live_benchmark_cache_store.py` (no dedicated test file exists today — the store is only exercised indirectly today via `tests/test_peer_cache_invalidation.py`/`tests/test_peer_blend.py`, per the design research; a dedicated unit test file is warranted here since this task changes the store's own `_id` contract directly).

- [ ] **Step 1: Write the failing test**

Create `tests/test_live_benchmark_cache_store.py`:

```python
"""Tests for the PEERS live-benchmark cache's Mongo document shape."""

from __future__ import annotations

import mongomock
from bson import ObjectId

from league_stats_peers.infra.live_benchmark_cache_store import LiveBenchmarkCacheStore


def test_write_uses_a_real_objectid_not_the_cache_key() -> None:
    store = LiveBenchmarkCacheStore(mongomock.MongoClient())
    store.write("GOLD|Viktor|MIDDLE", {"win": 0.5})
    doc = store._cache.find_one({"cache_key": "GOLD|Viktor|MIDDLE"})
    assert isinstance(doc["_id"], ObjectId)


def test_read_round_trips_without_leaking_internal_fields() -> None:
    store = LiveBenchmarkCacheStore(mongomock.MongoClient())
    store.write("GOLD|Viktor|MIDDLE", {"win": 0.5, "kda": 2.4})
    result = store.read("GOLD|Viktor|MIDDLE")
    assert result == {"win": 0.5, "kda": 2.4}


def test_read_missing_key_returns_none() -> None:
    store = LiveBenchmarkCacheStore(mongomock.MongoClient())
    assert store.read("nothing-here") is None


def test_write_is_idempotent_upsert() -> None:
    store = LiveBenchmarkCacheStore(mongomock.MongoClient())
    store.write("k", {"win": 0.4})
    store.write("k", {"win": 0.6})
    assert store.read("k") == {"win": 0.6}
    assert store._cache.count_documents({"cache_key": "k"}) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_live_benchmark_cache_store.py -v`
Expected: FAIL — `test_write_uses_a_real_objectid_not_the_cache_key` fails because there is no `"cache_key"` field today (it's `_id` directly); `test_read_round_trips_without_leaking_internal_fields` fails because `read()` doesn't yet strip a `cache_key` field.

- [ ] **Step 3: Write minimal implementation**

In `src/league_stats_peers/infra/live_benchmark_cache_store.py`:

1. In `__init__`, after `self._cache = db["live_benchmark_cache"]`, add:
   ```python
   self._cache.create_index("cache_key", unique=True)
   ```
2. `read`: replace
   ```python
   doc = self._cache.find_one({"_id": key})
   if doc is None:
       return None
   return {k: v for k, v in doc.items() if k not in ("_id", "fetched_at_dt")}
   ```
   with
   ```python
   doc = self._cache.find_one({"cache_key": key})
   if doc is None:
       return None
   return {k: v for k, v in doc.items() if k not in ("_id", "cache_key", "fetched_at_dt")}
   ```
3. `write`: replace
   ```python
   doc = {"_id": key, **data, "fetched_at_dt": datetime.datetime.now(datetime.timezone.utc)}
   self._cache.replace_one({"_id": key}, doc, upsert=True)
   ```
   with
   ```python
   doc = {"cache_key": key, **data, "fetched_at_dt": datetime.datetime.now(datetime.timezone.utc)}
   self._cache.replace_one({"cache_key": key}, doc, upsert=True)
   ```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_live_benchmark_cache_store.py -v`
Expected: all PASS.

Then run the two indirect callers to confirm no regression:

Run: `.venv/bin/python -m pytest tests/test_peer_cache_invalidation.py tests/test_peer_blend.py -v`
Expected: all PASS (these tests construct their own `LiveBenchmarkCacheStore`/mongomock client per the `conftest.py` fixture pattern and never assert on `_id` directly, per design research — if any assertion does reference `_id` or bypasses `read()`/`write()` to hit `_cache` directly with an `_id` filter, update it to use `cache_key` the same way).

- [ ] **Step 5: Commit**

```bash
git add src/league_stats_peers/infra/live_benchmark_cache_store.py tests/test_live_benchmark_cache_store.py
git commit -m "feat: use real ObjectId _id for the PEERS live-benchmark cache"
```

---

### Task 8: `career_store.py` — switch all 3 collections to real ObjectId `_id`

**Files:**
- Modify: `src/league_stats_common/infra/career_store.py`
- Test: `tests/test_career_store.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_career_store.py` (reuse the file's existing `_store()`/`_rungs()` helpers and `build_key` import):

```python
def test_goal_id_is_a_real_objectid() -> None:
    from bson import ObjectId

    key = build_key("p", "Viktor", "MIDDLE")
    with _store() as store:
        store.write_slot(key, 0, "map_presence", _rungs("a"), ["In progress"] * 3)
        doc = store._goals.find_one({"build_key": key, "slot": 0, "goal_index": 0})
    assert isinstance(doc["_id"], ObjectId)


def test_used_track_id_is_a_real_objectid() -> None:
    from bson import ObjectId

    key = build_key("p", "Viktor", "MIDDLE")
    with _store() as store:
        store.record_used_track(key, "map_presence")
        doc = store._used_tracks.find_one({"build_key": key, "track_key": "map_presence"})
    assert isinstance(doc["_id"], ObjectId)


def test_career_flag_id_is_a_real_objectid() -> None:
    from bson import ObjectId

    key = build_key("p", "Viktor", "MIDDLE")
    with _store() as store:
        store.set_pending_congrats(key, "map_presence")
        doc = store._flags.find_one({"build_key": key})
    assert isinstance(doc["_id"], ObjectId)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_career_store.py::test_goal_id_is_a_real_objectid tests/test_career_store.py::test_used_track_id_is_a_real_objectid tests/test_career_store.py::test_career_flag_id_is_a_real_objectid -v`
Expected: FAIL — all three currently have a composite string `_id`, and `career_flags` has no `build_key` field at all yet (so the third test's `find_one` returns `None` and the assertion errors on `None["_id"]`).

- [ ] **Step 3: Write minimal implementation**

In `src/league_stats_common/infra/career_store.py`:

1. Delete `_goal_id` and `_used_track_id` static methods.
2. In `__init__`, replace:
   ```python
   self._goals.create_index("build_key")
   self._used_tracks.create_index("build_key")
   ```
   with:
   ```python
   self._goals.create_index("build_key")
   self._goals.create_index(
       [("build_key", 1), ("slot", 1), ("goal_index", 1)], unique=True
   )
   self._used_tracks.create_index("build_key")
   self._used_tracks.create_index(
       [("build_key", 1), ("track_key", 1), ("cleared_at", 1)], unique=True
   )
   self._flags.create_index("build_key", unique=True)
   ```
3. `write_slot`: replace
   ```python
   "_id": self._goal_id(key, slot, index),
   "build_key": key,
   ```
   with
   ```python
   "build_key": key,
   ```
   (drop the `_id` line only; every other key in the inserted dict is unchanged.)
4. `save_goal_states`: replace
   ```python
   self._goals.update_one(
       {"_id": self._goal_id(key, slot, index)}, {"$set": {"state": state}}
   )
   ```
   with
   ```python
   self._goals.update_one(
       {"build_key": key, "slot": slot, "goal_index": index}, {"$set": {"state": state}}
   )
   ```
5. `move_slot`: the `_id`-embeds-`slot` constraint that forced the delete-and-reinsert workaround no longer applies (slot lives in its own field, and it is not part of any collection's `_id` anymore). Replace the whole method body from `docs = list(self._goals.find(...))` onward:
   ```python
   def move_slot(self, key: str, src: int, dst: int, *, since_ms: int | None = None) -> None:
       """Shift a slot's goals left, replacing whatever sat at the destination.

       ``since_ms`` re-stamps the start line, which matters on promotion to the
       live slot: a queued block must not inherit credit from the games that
       cleared the block ahead of it.
       """
       self.delete_slot(key, dst)
       sets: dict[str, Any] = {"slot": dst}
       if since_ms is not None:
           sets["since_ms"] = int(since_ms)
       for doc in self._goals.find({"build_key": key, "slot": src}):
           self._goals.update_one(
               {"build_key": key, "slot": src, "goal_index": doc["goal_index"]},
               {"$set": sets},
           )
   ```
   Add `from typing import Any` to the file's imports if not already present (check the existing `from typing import Sequence` line — extend it to `from typing import Any, Sequence`).
6. `record_used_track`: replace
   ```python
   self._used_tracks.update_one(
       {"_id": self._used_track_id(key, track_key, cleared_at)},
       {
           "$setOnInsert": {
               "build_key": key,
               "track_key": track_key,
               "cleared_at": cleared_at,
           }
       },
       upsert=True,
   )
   ```
   with
   ```python
   self._used_tracks.update_one(
       {"build_key": key, "track_key": track_key, "cleared_at": cleared_at},
       {"$setOnInsert": {"build_key": key}},
       upsert=True,
   )
   ```
   (the filter's three equality fields are auto-populated into the new document on upsert, same mechanism as before; MongoDB rejects an update document with an empty operator body like `{"$setOnInsert": {}}`, so `build_key` is set explicitly here — redundant with the filter, but required to keep the update non-empty.)
7. `set_pending_congrats`, `peek_pending_congrats`, `clear_pending_congrats`, `peek_recap_ack`, `ack_recap`, `request_drop`, `peek_pending_drop`, `clear_pending_drop`: every one of these currently does `{"_id": key}` — replace each with `{"build_key": key}`. For the three `update_one(..., upsert=True)` calls among them (`set_pending_congrats`, `clear_pending_congrats`, `ack_recap`, `request_drop`), the `build_key` field is auto-populated on insert by the same upsert-filter mechanism, so no `$set`/`$setOnInsert` addition is needed.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_career_store.py -v`
Expected: all PASS. `test_a_goal_document_missing_since_ms_defaults_to_zero_on_load` (the manually-inserted-legacy-doc test) needs no change — it filters `load_goals` by `build_key` only, never by `_id`, so it is unaffected by this migration; verify it still passes as-is.

- [ ] **Step 5: Commit**

```bash
git add src/league_stats_common/infra/career_store.py tests/test_career_store.py
git commit -m "feat: use real ObjectId _id for career_goals/career_used_tracks/career_flags"
```

---

### Task 9: `jobs.py` — switch `jobs`/`players`/`counters` to real ObjectId `_id`

The most invasive task: `_id` on `jobs` is currently a monotonically increasing int used for FIFO ordering (`queue_position`, `claim_next`, `average_duration_s`), not just an identifier. It becomes a `job_id` field that takes over the exact same ordering role.

**Files:**
- Modify: `src/league_stats_common/infra/jobs.py`
- Test: `tests/test_web_jobs.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_web_jobs.py`:

```python
def test_job_and_player_ids_are_real_objectids(store: JobStore) -> None:
    from bson import ObjectId

    job = _enqueue(store)
    job_doc = store._jobs.find_one({"job_id": int(job["id"])})
    assert isinstance(job_doc["_id"], ObjectId)

    store.upsert_player(slug="test_euw", riot_id="Test", tagline="EUW", region="euw1")
    player_doc = store._players.find_one({"slug": "test_euw"})
    assert isinstance(player_doc["_id"], ObjectId)


def test_counter_id_is_a_real_objectid(store: JobStore) -> None:
    from bson import ObjectId

    _enqueue(store)
    counter_doc = store._counters.find_one({"name": "jobs"})
    assert isinstance(counter_doc["_id"], ObjectId)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_web_jobs.py::test_job_and_player_ids_are_real_objectids tests/test_web_jobs.py::test_counter_id_is_a_real_objectid -v`
Expected: FAIL — none of the three collections have these new fields yet.

- [ ] **Step 3: Write minimal implementation**

In `src/league_stats_common/infra/jobs.py`:

1. In `__init__`, replace:
   ```python
   self._jobs.create_index("state")
   self._jobs.create_index(
       "player_slug",
       unique=True,
       partialFilterExpression={"state": {"$in": list(ACTIVE_STATES)}},
   )
   ```
   with:
   ```python
   self._jobs.create_index("state")
   self._jobs.create_index("job_id", unique=True)
   self._jobs.create_index(
       "player_slug",
       unique=True,
       partialFilterExpression={"state": {"$in": list(ACTIVE_STATES)}},
   )
   self._players.create_index("slug", unique=True)
   self._counters.create_index("name", unique=True)
   ```
2. `_doc_to_job`: replace
   ```python
   data = dict(doc)
   data["id"] = data.pop("_id")
   ```
   with
   ```python
   data = dict(doc)
   data.pop("_id", None)
   data["id"] = data.pop("job_id")
   ```
3. `_doc_to_player`: replace
   ```python
   data = dict(doc)
   data["slug"] = data.pop("_id")
   ```
   with
   ```python
   data = dict(doc)
   data.pop("_id", None)
   ```
   (the doc already carries its own `"slug"` field once Step 6 below stores it via the upsert-filter mechanism, so there is nothing left to rename.)
4. `_next_job_id`: replace
   ```python
   doc = self._counters.find_one_and_update(
       {"_id": "jobs"},
       {"$inc": {"value": 1}},
       upsert=True,
       return_document=ReturnDocument.AFTER,
   )
   ```
   with
   ```python
   doc = self._counters.find_one_and_update(
       {"name": "jobs"},
       {"$inc": {"value": 1}},
       upsert=True,
       return_document=ReturnDocument.AFTER,
   )
   ```
5. `enqueue`: replace
   ```python
   doc = {
       "_id": new_id,
       "kind": kind,
   ```
   with
   ```python
   doc = {
       "job_id": new_id,
       "kind": kind,
   ```
   and replace
   ```python
   self._players.update_one(
       {"_id": player_slug}, {"$set": {"last_job_id": new_id}}
   )
   ```
   with
   ```python
   self._players.update_one(
       {"slug": player_slug}, {"$set": {"last_job_id": new_id}}
   )
   ```
6. `get`: replace `doc = self._jobs.find_one({"_id": job_id})` with `doc = self._jobs.find_one({"job_id": job_id})`.
7. `_get`: same filter swap.
8. `_active_job_for`: replace `.sort("_id", -1)` with `.sort("job_id", -1)`.
9. `list_active_jobs`: replace `.sort("_id", -1)` with `.sort("job_id", -1)`.
10. `claim_next`: replace `sort=[("_id", 1)]` with `sort=[("job_id", 1)]`.
11. `set_state`: replace both `{"_id": job_id}` occurrences (the read and the `update_one`) with `{"job_id": job_id}`.
12. `update_progress`: replace `{"_id": job_id, "state": {"$ne": CANCELLED}}` with `{"job_id": job_id, "state": {"$ne": CANCELLED}}`.
13. `is_cancelled`: replace `{"_id": job_id}` with `{"job_id": job_id}`.
14. `cancel`: replace `{"_id": job_id, "state": {"$in": list(ACTIVE_STATES)}}` with `{"job_id": job_id, "state": {"$in": list(ACTIVE_STATES)}}`.
15. `queue_position`: replace
    ```python
    doc = self._jobs.find_one({"_id": job_id}, {"state": 1})
    if doc is None or doc["state"] != QUEUED:
        return None
    ahead = self._jobs.count_documents({"state": QUEUED, "_id": {"$lt": job_id}})
    ```
    with
    ```python
    doc = self._jobs.find_one({"job_id": job_id}, {"state": 1})
    if doc is None or doc["state"] != QUEUED:
        return None
    ahead = self._jobs.count_documents({"state": QUEUED, "job_id": {"$lt": job_id}})
    ```
16. `average_duration_s`: replace `.sort("_id", -1)` with `.sort("job_id", -1)`.
17. `upsert_player`: replace `{"_id": slug}` with `{"slug": slug}`.
18. `get_player`: replace `{"_id": slug}` with `{"slug": slug}`.
19. `set_watch`: replace both `{"_id": slug}` occurrences with `{"slug": slug}` (keep the `{"_id": 1}` projection in the existence-check `find_one` as-is, or change it to `{"slug": 1}` — either works since it's a pure existence check; use `{"slug": 1}` for clarity).
20. `list_watched_players`: replace `.sort("_id", 1)` with `.sort("slug", 1)`.
21. `record_watch_tick`, `mark_player_base_complete`, `mark_player_peer_complete`, `mark_player_peer_failed`: replace each `{"_id": slug}` with `{"slug": slug}`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_web_jobs.py -v`
Expected: all PASS, including the pre-existing "defensive defaults" tests (`test_get_defaults_missing_job_fields`, `test_get_player_defaults_missing_player_fields`, `test_list_watched_players_defaults_missing_watch_seen`) — these manually insert legacy-shaped documents and must be updated to match the new schema:

- `test_get_defaults_missing_job_fields`: replace `"_id": 1,` with `"job_id": 1,` in the inserted doc, and call `store.get(1)` as before (the public `get(job_id)` signature is unchanged, only the underlying filter field changed).
- `test_get_player_defaults_missing_player_fields`: replace `"_id": "legacy_euw",` with `"slug": "legacy_euw",` in the inserted doc.
- `test_list_watched_players_defaults_missing_watch_seen`: replace `"_id": "legacy_euw",` with `"slug": "legacy_euw",` in the inserted doc.

Then run the two atomicity tests explicitly, since they are the most timing-sensitive in this file:

Run: `.venv/bin/python -m pytest tests/test_web_jobs.py::test_enqueue_is_atomic_under_concurrent_writers tests/test_web_jobs.py::test_claim_next_is_atomic_under_concurrent_claimers -v`
Expected: both PASS (the `player_slug` partial unique index and the in-process `self._lock` are both untouched by this task — only the ordering field's name changed, not the atomicity mechanism).

- [ ] **Step 5: Commit**

```bash
git add src/league_stats_common/infra/jobs.py tests/test_web_jobs.py
git commit -m "feat: use real ObjectId _id for jobs/players/counters"
```

---

### Task 10: Full-suite verification and rollout note

**Files:** none (verification-only task).

- [ ] **Step 1: Run the full test suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all tests pass, zero failures, zero new warnings about `_id`.

- [ ] **Step 2: Grep the diff for any remaining `_id`-as-natural-key usage**

Run: `git diff main --stat` to see every file this plan touched, then:
```bash
grep -rn '"_id"' src/league_stats_common/infra/ src/league_stats_runner/infra/ src/league_stats_peers/infra/
```
Expected: the only remaining `"_id"` references are the ones intentionally left untouched (`derived.py`'s `evict_to_budget`'s `{"_id": 1}` projection/`{"_id": doc["_id"]}` delete, which is correct as-is — see Task 2 Step 3.9 — and any `{"_id": 1}`-style existence-check projections in `jobs.py`'s `set_watch` if left unchanged per Task 9 Step 19's note). Anything else filtering/sorting on `_id` as if it were still a natural key is a bug — fix it before proceeding.

- [ ] **Step 3: Confirm the ObjectId encoder test still passes standalone**

Run: `.venv/bin/python -m pytest tests/test_web_api.py -v`
Expected: PASS.

- [ ] **Step 4: Tell the user the rollout command**

No commit needed for this task (verification only). Report to the user that deployment is:
```
cd ~/league-champion-stats-analysis
git pull
docker-compose down -v
# update .env if needed
docker-compose up -d
```
Every collection starts empty and regenerates from Riot's API / re-analysis, per the design doc's "Rollout" section.
