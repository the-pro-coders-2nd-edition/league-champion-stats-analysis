# Svelte SPA Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retire all server-side HTML rendering (Jinja `report.html`/`player.html`/`landing.html` + the `generated/*` partial-compile toolchain) and replace it with a Vite+Svelte4 client-side SPA that consumes a new JSON API. The pipeline never writes an HTML report again.

**Architecture:** The pipeline (`orchestrator.py`) already assembles one large `context` dict that today gets handed to Jinja. That dict — plus the queue/window "views" JSON blobs it already pre-serializes for the *existing* client-side re-render JS (`report_views_json`, `progression_views_json`, `game_review_json`, `account_filter_json`, `game_review_tooltips_json`) — becomes the single source of truth for a new `report.json` written per build. A new FastAPI endpoint serves it. A new Svelte SPA (evolving the existing `frontend/` component library, which today only compiles to static Jinja partials) fetches it and renders every section client-side, reusing the same JSON shapes the current vanilla JS in `report.html` already consumes for its "queue/window switch" re-renders — so most of the porting work is translating existing imperative `render*` JS functions into declarative Svelte components against data contracts that already exist and are stable.

**Tech Stack:** Python/FastAPI (existing), Vite + Svelte 4 + svelte-preprocess (existing devDeps in `frontend/`), a lightweight client router (`svelte-spa-router`), pytest (existing).

**Spec:** `~/.claude/docs/league-champion-stats-analysis/superpowers/specs/2026-08-18-svelte-spa-migration-design.md`

**Corrections vs. the spec (discovered during exploration, spec is otherwise authoritative):**
- The spec assumed per-build data lives in on-disk `summary.json`/`matches.csv`/etc. It does not: the pipeline builds one in-memory `context` dict in `orchestrator.py` and hands it straight to `ReportBuilder.render()` (Jinja). There are no intermediate CSV/JSON files to parse. The new JSON endpoint therefore serves a serialized form of that same `context`, not a re-derivation from CSVs.
- `report.html` is not a thin shell around the `generated/*` component includes — it also contains ~2,500 lines of hand-rolled vanilla JS (`renderScore`, `renderPeer`, `renderMatchupRows`, `renderGameReview*`, a key-moment map/scrubber, chatbot, account filter, Plotly wiring) that already re-renders sections client-side from the JSON blobs above when the user switches queue/window/account filters. Porting this logic to Svelte is the bulk of the work, not an afterthought.

## Global Constraints

- No file at any layer (pipeline, web app) writes HTML for a report ever again once cutover (Task 16) lands. Before that, additive changes (new JSON output) must not break the existing Jinja path — `report.html` keeps rendering until cutover.
- Svelte 4 (matches `frontend/package.json`'s existing `"svelte": "^4.2.20"` devDependency — do not upgrade to 5 mid-migration).
- No SSR/prerendering for the SPA — plain client-side rendering only (confirmed in spec).
- One full JSON payload per build, no split-by-section endpoints (confirmed in spec).
- Every new/rewritten Svelte component keeps the scoped CSS and markup structure of its existing `.svelte` source where one exists (28 components in `frontend/src/components/`) — only props change from literal Jinja-token strings to real data.

---

### Task 1: JSON-safe context serialization helper

**Files:**
- Create: `src/league_stats/presentation/report_json.py`
- Test: `tests/test_report_json.py`

**Interfaces:**
- Consumes: a `context: dict[str, Any]` shaped like the dict built in `pipeline/orchestrator.py` (roughly lines 590–660), which mixes plain primitives, dicts/lists, pre-serialized JSON *strings* (`report_views_json`, `progression_views_json`, `game_review_json`, `game_review_tooltips_json`, `account_filter_json`), and possibly dataclass instances (e.g. `ScoreComponent` from `presentation/report.py:43`, `Recommendation` — check `analysis/coach.py` for its shape) reached via keys added by `bundle_to_template_context` (`pipeline/bundles.py:381`), `progression_to_template_context` (`pipeline/progression.py:180`), and `game_review_to_template_context` (`analysis/game_review/views.py:107`).
- Produces: `def context_to_json(context: dict[str, Any]) -> dict[str, Any]` — returns a plain, `json.dumps`-safe dict where:
  - Any value that is already a JSON string ending in `_json` (e.g. `report_views_json`) is `json.loads`'d back into a nested object under the key with the `_json` suffix stripped (e.g. `report_views`), so the API payload has real nested JSON, not double-encoded strings.
  - Any dataclass instance is converted via `dataclasses.asdict` (import `dataclasses`; use `dataclasses.is_dataclass(value)` to detect).
  - `Path` instances become `str(value)`.
  - Everything else passes through unchanged, then the whole result is validated with `json.dumps(result)` before returning (raise `TypeError` with the offending key name if it fails, so silent `default=str` stringification — which loses structure — never happens here, unlike `serialize_report_views_json`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_report_json.py
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from league_stats.presentation.report_json import context_to_json


@dataclass
class _FakeScoreComponent:
    label: str
    value: float


def test_context_to_json_unwraps_pre_serialized_json_strings() -> None:
    context = {
        "champion": "Viktor",
        "report_views_json": json.dumps({"solo": {"total_games": 42}}),
    }

    result = context_to_json(context)

    assert result["champion"] == "Viktor"
    assert "report_views_json" not in result
    assert result["report_views"] == {"solo": {"total_games": 42}}


def test_context_to_json_converts_dataclasses_to_dicts() -> None:
    context = {"score_components": [_FakeScoreComponent(label="cs", value=0.8)]}

    result = context_to_json(context)

    assert result["score_components"] == [{"label": "cs", "value": 0.8}]


def test_context_to_json_converts_paths_to_strings() -> None:
    context = {"stylesheet": Path("static/report.css")}

    result = context_to_json(context)

    assert result["stylesheet"] == "static/report.css"


def test_context_to_json_raises_on_unserializable_value() -> None:
    context = {"bad": object()}

    with pytest.raises(TypeError, match="bad"):
        context_to_json(context)


def test_context_to_json_output_is_actually_json_dumpable() -> None:
    context = {
        "champion": "Viktor",
        "game_review_json": json.dumps({"available": True}),
        "score_components": [_FakeScoreComponent(label="cs", value=0.8)],
    }

    result = context_to_json(context)

    json.dumps(result)  # must not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/brice.parent/perso/league-champion-stats-analysis-svelte-spa && .venv/bin/pytest tests/test_report_json.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'league_stats.presentation.report_json'`

- [ ] **Step 3: Implement**

```python
# src/league_stats/presentation/report_json.py
"""Serialize a report render context (built for Jinja) into a clean JSON payload."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any


def _convert(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {k: _convert(v) for k, v in dataclasses.asdict(value).items()}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {k: _convert(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_convert(v) for v in value]
    return value


def context_to_json(context: dict[str, Any]) -> dict[str, Any]:
    """Turn a Jinja render context into a plain, JSON-serializable dict.

    Keys ending in ``_json`` are assumed to already hold a JSON-encoded string
    (as produced by ``serialize_report_views_json``); they are decoded back
    into real nested objects under the key with the suffix stripped, so the
    API returns structured data instead of a double-encoded string.
    """
    result: dict[str, Any] = {}
    for key, value in context.items():
        if key.endswith("_json") and isinstance(value, str):
            result[key[: -len("_json")]] = json.loads(value) if value else None
            continue
        result[key] = _convert(value)

    try:
        json.dumps(result)
    except TypeError as exc:
        bad_keys = [k for k, v in result.items() if not _is_json_safe(v)]
        raise TypeError(f"context_to_json: unserializable value(s) at key(s) {bad_keys}") from exc

    return result


def _is_json_safe(value: Any) -> bool:
    try:
        json.dumps(value)
    except TypeError:
        return False
    return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/brice.parent/perso/league-champion-stats-analysis-svelte-spa && .venv/bin/pytest tests/test_report_json.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Verify against the real orchestrator context**

Read `src/league_stats/pipeline/orchestrator.py` in full (the function building `context`, roughly lines 400–700) and cross-check every key added to `context` (including via `bundle_to_template_context`, `progression_to_template_context`, `game_review_to_template_context`, `build_player_builds_nav`, `report_stylesheet_hrefs`, `brand_context`) against `_convert`. If any key holds a type `_convert` doesn't handle (e.g. an enum, a `datetime`, a custom non-dataclass object with Jinja-friendly attribute access), add a branch to `_convert` for it and a corresponding test in `test_report_json.py` using the real type (import it, don't fake it) before moving on.

- [ ] **Step 6: Commit**

```bash
git add src/league_stats/presentation/report_json.py tests/test_report_json.py
git commit -m "feat: add JSON-safe serializer for the report render context"
```

---

### Task 2: Write `report.json` alongside `report.html` in the pipeline

**Files:**
- Modify: `src/league_stats/pipeline/orchestrator.py` (the block around line 676: `builder = ReportBuilder(...); report_path = builder.render(run_dir / "report.html", context)`)
- Test: `tests/test_pipeline.py` (add to existing file — check its fixtures/helpers first via `Read`)

**Interfaces:**
- Consumes: `context_to_json` from Task 1 (`league_stats.presentation.report_json`).
- Produces: every build directory (`run_dir`) gets a `report.json` file alongside `report.html`, written the same write-then-rename way `ReportBuilder.render` does (see `presentation/report.py:117-127` for the `.html.tmp` → `os.replace` pattern) so partial writes are never served.

- [ ] **Step 1: Read the existing pipeline test for a build fixture to extend**

Run: `grep -n "def test_\|run_dir\|tmp_path" tests/test_pipeline.py | head -40` and read the matching test function(s) in full so the new test reuses the same fixture/config setup instead of duplicating it.

- [ ] **Step 2: Write the failing test** (adapt the exact fixture/config names found in Step 1 — do not guess them)

```python
def test_pipeline_writes_report_json_alongside_report_html(tmp_path, <existing_fixture_args>):
    # Reuse whatever config/records fixtures the existing report-building
    # test in this file uses, pointed at tmp_path as the output dir.
    run_pipeline(<...>)  # call the same entry point the existing test calls

    run_dir = <the same run_dir the existing test asserts report.html under>
    assert (run_dir / "report.html").exists()
    report_json_path = run_dir / "report.json"
    assert report_json_path.exists()

    import json
    payload = json.loads(report_json_path.read_text(encoding="utf-8"))
    assert payload["champion"] == <expected champion from fixture>
    assert "report_views" in payload
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_pipeline.py -k report_json -v`
Expected: FAIL — `report.json` does not exist

- [ ] **Step 4: Implement**

In `orchestrator.py`, right after the existing line `report_path = builder.render(run_dir / "report.html", context)`, add:

```python
    from league_stats.presentation.report_json import context_to_json

    report_json_path = run_dir / "report.json"
    tmp_json_path = report_json_path.with_suffix(".json.tmp")
    tmp_json_path.write_text(
        json.dumps(context_to_json(context)), encoding="utf-8"
    )
    os.replace(tmp_json_path, report_json_path)
```

(Move the `from league_stats.presentation.report_json import context_to_json` to the top-level imports of `orchestrator.py` instead of inline — check whether `os` and `json` are already imported at the top of the file; add them if not.)

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_pipeline.py -k report_json -v`
Expected: PASS

- [ ] **Step 6: Run the full pipeline test suite to check for regressions**

Run: `.venv/bin/pytest tests/test_pipeline.py tests/test_build_preview_report.py tests/test_reports.py -v`
Expected: all PASS (no existing test should break — this task is additive)

- [ ] **Step 7: Commit**

```bash
git add src/league_stats/pipeline/orchestrator.py tests/test_pipeline.py
git commit -m "feat: write report.json alongside report.html in the pipeline"
```

---

### Task 3: `web/worker.py` re-render path also writes `report.json`

**Files:**
- Modify: `src/league_stats/web/worker.py` (peer-comparison re-render path — grep `render`/`report\.` in this file, read the matching function in full before editing)
- Test: `tests/test_web_worker.py`

**Interfaces:**
- Consumes: same `context_to_json` from Task 1.
- Produces: same guarantee as Task 2 (`report.json` present and current) but for the code path that re-renders a report after peer analysis completes asynchronously, not just the initial pipeline run.

- [ ] **Step 1: Read `web/worker.py` in full** to find where it calls into `ReportBuilder`/`orchestrator` re-render logic (per the earlier grep, comment at line ~277: "Build peer comparisons and re-render each report as they land"). Identify the exact function and whether it goes through the same `orchestrator.py` code path from Task 2 (in which case this task may be a no-op — confirm before writing new code) or a separate path.

- [ ] **Step 2: If a separate path exists**, write a failing test in `tests/test_web_worker.py` mirroring Task 2's test shape, asserting `report.json` exists and is fresh (its `generated_at`/mtime advances) after the worker's re-render runs. If no separate path exists (the worker calls the same `orchestrator.py` function from Task 2), skip to Step 5 and note in the commit message that this task was a no-op confirmation.

- [ ] **Step 3: Run test to verify it fails** (if applicable)

Run: `.venv/bin/pytest tests/test_web_worker.py -k report_json -v`

- [ ] **Step 4: Implement** the equivalent of Task 2's Step 4 in the worker's re-render function (if it's a distinct code path).

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_web_worker.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/league_stats/web/worker.py tests/test_web_worker.py
git commit -m "fix: ensure peer-comparison re-render refreshes report.json"
```

---

### Task 4: `GET /api/players/{slug}/builds/{build_slug}` endpoint

**Files:**
- Modify: `src/league_stats/web/app.py` (add near the other `/api/players/{slug}/...` routes, e.g. after `player_status` at line ~862)
- Test: `tests/test_web_api.py`

**Interfaces:**
- Consumes: `report.json` written by Task 2/3, discovered the same way `_player_builds(config.output_dir, slug)` (app.py:511) already locates build directories.
- Produces: `GET /api/players/{slug}/builds/{build_slug}` → 200 with the parsed `report.json` contents as the response body, or 404 if the slug/build doesn't exist or has no `report.json` yet.

- [ ] **Step 1: Read `_player_builds` (app.py:511) and `discover_player_builds` (presentation/report.py:246) in full** to find the exact directory-naming convention for `build_slug` (it should match the `{champion}_{role}` folder name already used, e.g. `viktor_middle` from the earlier exploration) and reuse it rather than inventing a new slug format.

- [ ] **Step 2: Write the failing test**

```python
def test_get_build_payload_returns_report_json(tmp_path, monkeypatch):
    # Follow the existing pattern in this file for constructing a WebConfig /
    # TestClient against a tmp_path output dir (check the top of
    # tests/test_web_api.py for the fixture used by other /api/players tests).
    build_dir = tmp_path / "output" / "reports" / "demo_slug" / "viktor_middle"
    build_dir.mkdir(parents=True)
    (build_dir / "report.json").write_text('{"champion": "Viktor"}', encoding="utf-8")

    response = client.get("/api/players/demo_slug/builds/viktor_middle")

    assert response.status_code == 200
    assert response.json() == {"champion": "Viktor"}


def test_get_build_payload_404_when_missing(tmp_path):
    response = client.get("/api/players/demo_slug/builds/nonexistent")

    assert response.status_code == 404
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_web_api.py -k build_payload -v`
Expected: FAIL with 404 (route doesn't exist) on the first test

- [ ] **Step 4: Implement** (adjust the exact `config.output_dir` / `config.reports_dir` variable names to match what's already used elsewhere in `app.py` — confirmed via Step 1's read)

```python
    @app.get("/api/players/{slug}/builds/{build_slug}")
    def build_payload(slug: str, build_slug: str) -> dict[str, Any]:
        report_json_path = config.reports_dir / slug / build_slug / "report.json"
        if not report_json_path.exists():
            raise HTTPException(status_code=404, detail="Unknown build")
        return json.loads(report_json_path.read_text(encoding="utf-8"))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_web_api.py -k build_payload -v`
Expected: PASS

- [ ] **Step 6: Run the full web API test suite**

Run: `.venv/bin/pytest tests/test_web_api.py tests/test_web_account_filter.py -v`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add src/league_stats/web/app.py tests/test_web_api.py
git commit -m "feat: add JSON build-payload API endpoint"
```

---

### Task 5: Frontend scaffold — Vite + Svelte SPA shell

**Files:**
- Modify: `frontend/package.json` (add `vite`, `@sveltejs/vite-plugin-svelte`, `svelte-spa-router` as devDependencies/dependencies; replace the `"generate"` script — keep it for now, Task 15 removes it — add `"dev"` and `"build"` scripts)
- Create: `frontend/vite.config.js`
- Create: `frontend/index.html`
- Create: `frontend/src/main.js`
- Create: `frontend/src/App.svelte`
- Create: `frontend/src/routes/Landing.svelte` (stub: `<h1>Landing</h1>` for now, filled in Task 13)
- Create: `frontend/src/routes/PlayerHub.svelte` (stub, filled in Task 14)
- Create: `frontend/src/routes/Report.svelte` (stub, filled in Task 6)
- Create: `frontend/src/lib/api.js`

**Interfaces:**
- Produces: `frontend/src/lib/api.js` exports `async function fetchBuild(slug, buildSlug)` → `fetch('/api/players/${slug}/builds/${buildSlug}')` returning parsed JSON, and `async function fetchPlayerStatus(slug)` → `fetch('/api/players/${slug}')` returning parsed JSON. These are the functions every later Report/PlayerHub task imports — later tasks must not redefine fetch logic inline.

- [ ] **Step 1: Install dependencies**

```bash
cd /Users/brice.parent/perso/league-champion-stats-analysis-svelte-spa/frontend
npm install --save-dev vite @sveltejs/vite-plugin-svelte
npm install svelte-spa-router
```

- [ ] **Step 2: Write `vite.config.js`**

```javascript
import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

export default defineConfig({
  plugins: [svelte()],
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      '/out': 'http://localhost:8000',
    },
  },
  build: {
    outDir: '../src/league_stats/web/spa_dist',
    emptyOutDir: true,
  },
});
```

- [ ] **Step 3: Write `index.html`**

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>League Champion Stats</title>
  </head>
  <body>
    <div id="app"></div>
    <script type="module" src="/src/main.js"></script>
  </body>
</html>
```

- [ ] **Step 4: Write `src/main.js`**

```javascript
import App from './App.svelte';

const app = new App({
  target: document.getElementById('app'),
});

export default app;
```

- [ ] **Step 5: Write `src/App.svelte`**

```svelte
<script>
  import Router from 'svelte-spa-router';
  import Landing from './routes/Landing.svelte';
  import PlayerHub from './routes/PlayerHub.svelte';
  import Report from './routes/Report.svelte';

  const routes = {
    '/': Landing,
    '/players/:slug': PlayerHub,
    '/players/:slug/:buildSlug': Report,
  };
</script>

<Router {routes} />
```

- [ ] **Step 6: Write `src/lib/api.js`**

```javascript
export async function fetchBuild(slug, buildSlug) {
  const response = await fetch(`/api/players/${slug}/builds/${buildSlug}`);
  if (!response.ok) throw new Error(`Failed to load build: ${response.status}`);
  return response.json();
}

export async function fetchPlayerStatus(slug) {
  const response = await fetch(`/api/players/${slug}`);
  if (!response.ok) throw new Error(`Failed to load player: ${response.status}`);
  return response.json();
}
```

- [ ] **Step 7: Write the three route stubs**

```svelte
<!-- src/routes/Landing.svelte -->
<h1>Landing</h1>
```

```svelte
<!-- src/routes/PlayerHub.svelte -->
<script>
  export let params = {};
</script>
<h1>Player hub: {params.slug}</h1>
```

```svelte
<!-- src/routes/Report.svelte -->
<script>
  export let params = {};
</script>
<h1>Report: {params.slug} / {params.buildSlug}</h1>
```

- [ ] **Step 8: Verify the dev build runs**

Run: `cd frontend && npm run dev -- --port 5173 &` then `curl -s http://localhost:5173/ | grep -q '<div id="app">' && echo OK`
Expected: `OK`. Kill the dev server afterward (`kill %1` or find the process by port).

- [ ] **Step 9: Verify the production build runs**

Run: `cd frontend && npx vite build`
Expected: exits 0, produces files under `../src/league_stats/web/spa_dist/`

- [ ] **Step 10: Add `spa_dist/` to `.gitignore`** (it's a build artifact, not source)

- [ ] **Step 11: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/vite.config.js frontend/index.html frontend/src/main.js frontend/src/App.svelte frontend/src/routes frontend/src/lib .gitignore
git commit -m "feat: scaffold Vite + Svelte SPA shell with client-side routing"
```

---

### Task 6: Rewrite the 28 existing components' props from literal Jinja tokens to real data

**Files:**
- Modify (all 28): every file under `frontend/src/components/*.svelte`
- Reference (read-only, do not modify yet): `frontend/manifest.json` — this is the exact, authoritative list of every prop each component currently receives as a literal Jinja-token string; use it as the checklist of what to rewrite, not as a guess.

**Interfaces:**
- Consumes: nothing external yet (this task only changes prop *types/semantics* within each component, not their call sites — Report.svelte wiring happens in Tasks 7–12).
- Produces: each component's `export let <propName>` list stays the same *names*, but any internal logic that assumed the prop was a literal string to be printed verbatim (there shouldn't be much, since Jinja did the token substitution before the component ever saw it) is reviewed for any leftover Jinja-specific assumption (e.g. a prop like `tone` in `HeroChip` was always exactly one of `good`/`bad`/`stat` because the Jinja token `{% if %}` resolved it — confirm the component doesn't also embed a raw `{{ }}` token anywhere in its own markup/CSS, only in props passed from outside).

- [ ] **Step 1: Read `frontend/manifest.json` in full.** For each of the 28 top-level keys, note every distinct prop name used across all its `outputs` entries.

- [ ] **Step 2: For each component file, read it and confirm** its `export let` prop names exactly match what `manifest.json` lists for it (they should — the manifest was generated to feed these exact components). Record any mismatch as a finding, not a fix (a mismatch this early would mean the current Jinja pipeline is already broken — flag it in the commit message if found, don't silently "fix" behavior nobody asked you to touch).

- [ ] **Step 3: For each component, check for values baked in as string literals that are actually booleans/numbers/enums wearing a string costume** (e.g. `dot: false` in the `pill_scope_chip` manifest entry is already a real boolean — good; but something like a prop documented as `"true"`/`"false"` string in the manifest should become a real Svelte boolean prop with `export let flag = false;` and `{#if flag}`, not string comparison `flag === "true"`). Fix each one found; there is no fixed count to enumerate here — this step's exit condition is "grep every component for `=== '` or `== \"` comparisons against boolean-shaped values and resolve each."

Run: `grep -rn "=== '\|== \"" frontend/src/components/*.svelte`

- [ ] **Step 4: Run the existing generate.js pipeline to confirm no regression yet** (it still exists until Task 15):

Run: `cd frontend && node scripts/generate.js`
Expected: exits 0, same file count as before your changes (`git diff --stat src/league_stats/presentation/templates/generated/` should show no unexpected changes, since Task 6 only touches type handling, not the literal-token props fed in by the manifest at generate time)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/
git commit -m "refactor: normalize component prop types ahead of SPA data binding"
```

---

### Task 7: Report page data loading + Overview/Hero/Score section

**Files:**
- Modify: `frontend/src/routes/Report.svelte`
- Create: `frontend/src/sections/Overview.svelte`
- Reference: `src/league_stats/presentation/templates/report.html` lines 290–357 (markup) and the JS functions `uiChip` (1003), `renderScoreCard`/`renderScoreCardRow` (1014, 1038), `renderHeroChips` (1412), `renderHeroActions` (1430), `renderScore` (1164) — read all of these in full before writing the Svelte version.
- Reference: existing components `HeroChip.svelte`, `HeroAction.svelte`, `ScoreSetItem.svelte`, `ReportPlayerChip.svelte`.

**Interfaces:**
- Consumes: `fetchBuild` from `frontend/src/lib/api.js` (Task 5); payload fields `champion`, `champion_icon`, `role_icon_href`, `role_display`, `player_name`, `report_players`, `overview_cards` (feeds `HeroChip` — check the real field name in the payload once Task 2 is live; the Jinja variable was `overview_cards`, confirm it survives `context_to_json` unchanged since it's a plain list of dicts, not a `_json`-suffixed key), `top_tips` (feeds `HeroAction`), `score_components` (feeds `ScoreSetItem`).
- Produces: `frontend/src/routes/Report.svelte` exports a `buildPayload` writable/prop pattern that Tasks 8–12 also consume — establish this contract now: `Report.svelte` does one `fetchBuild(params.slug, params.buildSlug)` in an `onMount`/reactive block, stores the result in a local `let payload = null;`, and passes `payload` down as a prop to each section component (`<Overview data={payload} />`, later `<Coaching data={payload} />`, etc.) once loaded. Show a loading state while `payload` is null and an error state if the fetch throws.

- [ ] **Step 1: Read the reference files listed above in full.**

- [ ] **Step 2: Write `Report.svelte`'s data-loading shell**

```svelte
<script>
  import { fetchBuild } from '../lib/api.js';
  import Overview from '../sections/Overview.svelte';

  export let params = {};

  let payload = null;
  let error = null;

  $: fetchBuild(params.slug, params.buildSlug)
    .then((result) => { payload = result; })
    .catch((err) => { error = err; });
</script>

{#if error}
  <p class="report-error">Failed to load this report.</p>
{:else if payload === null}
  <p class="report-loading">Loading…</p>
{:else}
  <Overview data={payload} />
{/if}
```

- [ ] **Step 3: Write `sections/Overview.svelte`**, porting the markup at report.html:290-357 and the logic in `renderHeroChips`/`renderHeroActions`/`renderScore` into Svelte `{#each}` blocks over `data.overview_cards`, `data.top_tips`, `data.score_components`, rendering `<HeroChip>`, `<HeroAction>`, `<ScoreSetItem>` respectively with real props instead of the manifest's literal tokens. Preserve every CSS class name from report.html's markup exactly (the existing `report.css`/`components.css` selectors depend on them).

- [ ] **Step 4: Manual verification** — run the FastAPI app (`cd .. && .venv/bin/uvicorn league_stats.web.app:app --reload` from repo root, using whatever entry point `web/__main__.py` uses — check it first) and the Vite dev server together against a real build directory (use the `preview_euw/viktor_middle` fixture data referenced in earlier exploration, or generate one via the pipeline test fixtures/CLI), navigate to `/players/<slug>/<build_slug>`, and visually compare the Overview section against the same build's current `report.html` output side by side. This is the acceptance check for this task — there is no automated visual-diff tool here, so this step cannot be skipped or replaced with "looks right in the code."

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/Report.svelte frontend/src/sections/Overview.svelte
git commit -m "feat: SPA report page loads build JSON and renders overview section"
```

---

### Task 8: Coaching/recommendations section

**Files:**
- Create: `frontend/src/sections/Coaching.svelte`
- Modify: `frontend/src/routes/Report.svelte` (add `<Coaching data={payload} />`)
- Reference: report.html lines 358–404 (markup), `renderRecommendations`/`renderRec`/`extendButton` (JS lines 1514–1565), existing `RecCardHead.svelte`, `RecEvidenceSummary.svelte`, `RecExtendButton.svelte`.

**Interfaces:**
- Consumes: `data.positive_recommendations`, `data.negative_recommendations`, `data.recommendation_visible_count` from the payload.
- Produces: a `<Coaching>` component taking a `data` prop, matching the pattern established in Task 7.

- [ ] **Step 1: Read the reference files in full.**
- [ ] **Step 2: Write `Coaching.svelte`**, porting the "show more" behavior (currently `extendButton`/`renderRec` string-building) into local Svelte state (`let showAllPositive = false;` etc.) toggled by `<RecExtendButton>`'s click, slicing `data.positive_recommendations`/`negative_recommendations` reactively instead of DOM `innerHTML` swaps.
- [ ] **Step 3: Wire into `Report.svelte`.**
- [ ] **Step 4: Manual verification** against the same real build, comparing to current `report.html`'s coaching section, including clicking "show more" in both to confirm the same recommendations appear.
- [ ] **Step 5: Commit**

```bash
git add frontend/src/sections/Coaching.svelte frontend/src/routes/Report.svelte
git commit -m "feat: port coaching/recommendations section to Svelte"
```

---

### Task 9: Form tracker section (queue/window tabs + movers)

**Files:**
- Create: `frontend/src/sections/FormTracker.svelte`
- Modify: `frontend/src/routes/Report.svelte`
- Reference: report.html lines 405–486 (markup), `renderFormProgression`/`formWrBridgeHtml`/`formMoverRowHtml`/`formStoryHtml` and the `.form-tab` click handler (JS lines 1609–1758), existing `FormWrBridge.svelte`, `MoverRow.svelte`, `FormStoryHead.svelte`, `FormStoryLine.svelte`, `UiChipBadge.svelte`, `TabBar.svelte`.

**Interfaces:**
- Consumes: `data.progression` (unwrapped from `progression_views_json` by `context_to_json`), `data.progression_default`, `data.form_stories`, `data.form_top_improved`, `data.form_top_regressed`.
- Produces: `<FormTracker data={payload} />`, internal reactive state for the active tab (replacing the `.form-tab`/`.form-panel` DOM class toggling with an `activeTab` Svelte variable and `{#if activeTab === '...'}` blocks).

- [ ] **Step 1: Read the reference files in full**, paying particular attention to how `renderFormProgression` picks a preset from `data.progression_views_json` keyed by queue — this keying scheme must be preserved exactly since it's also used by Task 11 (rank/peers) for the same queue-switch UI.
- [ ] **Step 2: Write `FormTracker.svelte`.**
- [ ] **Step 3: Wire into `Report.svelte`.**
- [ ] **Step 4: Manual verification** against a real build: switch queue/window tabs in both the SPA and the current `report.html`, confirm the mover rows and trend badges match.
- [ ] **Step 5: Commit**

```bash
git add frontend/src/sections/FormTracker.svelte frontend/src/routes/Report.svelte
git commit -m "feat: port form-tracker section to Svelte"
```

---

### Task 10: Rank/peers section

**Files:**
- Create: `frontend/src/sections/RankPeers.svelte`
- Modify: `frontend/src/routes/Report.svelte`
- Reference: report.html lines 487–561 (markup, including the `data-peer-pending` fallback state at line 548), `renderPeer`/`peerDriverRowHtml` (JS lines 1199–1271), existing `PeerRankValue.svelte`, `PeerBalanceChip.svelte`, `PeerMetaChip.svelte`, `PeerDriverRow.svelte`.

**Interfaces:**
- Consumes: `data.peer_above`, `data.peer_below` (or their equivalents once you confirm the exact payload keys from `bundle_to_template_context`'s output — read that function in full, don't assume the Jinja loop variable names `peer_above`/`peer_below` are literally top-level context keys versus nested under another key).
- Produces: `<RankPeers data={payload} />`. Must render the "pending" empty state (report.html:548) when peer data isn't available yet — check the payload for whatever boolean/null signals this (likely tied to `peer_completed_at`/`peer_failed` surfaced elsewhere in the app, or a field within this same payload — confirm via `bundle_to_template_context`).

- [ ] **Step 1: Read `pipeline/bundles.py`'s `bundle_to_template_context` in full** to get the exact real key names before writing any prop bindings.
- [ ] **Step 2: Read the remaining reference files in full.**
- [ ] **Step 3: Write `RankPeers.svelte`**, including the pending/empty state.
- [ ] **Step 4: Wire into `Report.svelte`.**
- [ ] **Step 5: Manual verification** against a real build with peer data present, and (if a fixture without peer data is available) one without, to confirm the pending state renders.
- [ ] **Step 6: Commit**

```bash
git add frontend/src/sections/RankPeers.svelte frontend/src/routes/Report.svelte
git commit -m "feat: port rank/peers section to Svelte"
```

---

### Task 11: Matchups table (sortable)

**Files:**
- Create: `frontend/src/sections/Matchups.svelte`
- Modify: `frontend/src/routes/Report.svelte`
- Reference: report.html lines 614–649, `renderMatchupRow`/`renderMatchupRows`/`sortRows`/`sortValue`/`updateMatchupSortHeaders` (JS lines 1301–1405), existing `DataTableHead.svelte`, `DataTableRow.svelte`.

**Interfaces:**
- Consumes: `data.matchup_rows`.
- Produces: `<Matchups data={payload} />` with local `sortKey`/`sortDir` state driving a derived, sorted copy of `data.matchup_rows` (replacing `sortRows`'s imperative array sort + `renderTableBody` DOM rebuild with a Svelte reactive `$:` derived array).

- [ ] **Step 1: Read the reference files in full**, specifically `sortValue`'s type-coercion rules (numeric vs. string columns) — this logic must be preserved exactly or sort order will silently differ from today's report.
- [ ] **Step 2: Write `Matchups.svelte`.**
- [ ] **Step 3: Wire into `Report.svelte`.**
- [ ] **Step 4: Manual verification**: click every sortable column header in both the SPA and current `report.html`, confirm identical row order each time.
- [ ] **Step 5: Commit**

```bash
git add frontend/src/sections/Matchups.svelte frontend/src/routes/Report.svelte
git commit -m "feat: port sortable matchups table to Svelte"
```

---

### Task 12: Champion deep-dive sections (items, runes, lane, objectives, deaths, vision, economy, teamfights, positioning)

**Files:**
- Create: `frontend/src/sections/ChampionDeepDive.svelte` (or split further if, after reading the reference material, one file would exceed ~300 lines — use judgment per the file-structure guidance in the writing-plans skill; if split, keep each split file's section boundary matching one `<section id="...">` from report.html)
- Modify: `frontend/src/routes/Report.svelte`
- Reference: report.html lines 595–756 (all of the `items`/`runes`/`lane`/`objectives`/`deaths`/`vision`/`economy`/`teamfights`/`positioning` sections), corresponding JS: `renderObjectiveRow`/`renderObjectiveDetails`/`objectiveOutcome` (863–996), `renderPositioningHints` (1566), existing `PositioningHint.svelte`, `DataTableHead.svelte`, `DataTableRow.svelte`, `MetricValue.svelte`, `MetricBenchmark.svelte`, `MetricLabelSpan.svelte`, `MetricTooltip.svelte`.

**Interfaces:**
- Consumes: whatever the payload's equivalents of `matches.csv`/`deaths.csv`/`objectives.csv`/`runes.csv`/`items.csv`/`vision.csv`/`teamfights.csv` turn out to be once you read `bundle_to_template_context` in full (Task 10's Step 1 already required reading this function — reuse that knowledge here rather than re-deriving it).
- Produces: one or more section components rendering each of these 9 report sections against real data.

- [ ] **Step 1: Read all reference files in full.**
- [ ] **Step 2: Decide file split** based on actual complexity found in Step 1; document the decision in the commit message.
- [ ] **Step 3: Write the component(s).**
- [ ] **Step 4: Wire into `Report.svelte`.**
- [ ] **Step 5: Manual verification** against a real build for each of the 9 sub-sections, comparing to current `report.html`.
- [ ] **Step 6: Commit**

```bash
git add frontend/src/sections/
git commit -m "feat: port champion deep-dive sections to Svelte"
```

---

### Task 13: Game review section (score dimensions, runes, skill progression, key-moment map)

**Files:**
- Create: `frontend/src/sections/GameReview.svelte`
- Create: `frontend/src/sections/GameReviewKeyMoments.svelte`
- Modify: `frontend/src/routes/Report.svelte`
- Reference: report.html lines 562–594 (markup) plus the entire `renderGameReview*` cluster (JS lines 1851–2225: `renderGameReviewScoreDim`, `bindGameReviewScoreList`, `renderGameReviewRunes`, `renderRuneIconRow`, `renderGameReviewRunePage`, `renderGameReviewSummoners`, `renderGameReviewSkillProgression`, `renderGameReviewComparisonTable`, `mapCoordToPct`, `participantIsAlly`, `championPinHtml`, `objectivePinHtml`, `buildKeyMomentMapShell`, `applyKeyMomentFrame`, `showKeyMomentFrame`, `bindKeyMomentScrubber`, `keyMomentReason`, `renderGameReviewKeyMoments`, `primaryBehaviorSignal`, `renderVerdictCallouts`), existing `TabBar.svelte`.

**Interfaces:**
- Consumes: `data.game_review` (unwrapped from `game_review_json`), `data.game_review_tooltips` (unwrapped from `game_review_tooltips_json`).
- Produces: `<GameReview data={payload} />`; the key-moment map/scrubber becomes its own child component `<GameReviewKeyMoments game={selectedGame} />` with the scrubber's `oninput` handler replaced by Svelte reactive state (`let frameIndex = 0;`) driving which frame's champion/objective pins render — this is the most stateful single piece of UI in the whole app, budget real attention to it, don't skim.

- [ ] **Step 1: Read every JS function listed above in full**, in order, tracing one complete data flow (a single game's key-moment scrubbing) from `renderGameReviewKeyMoments` → `bindKeyMomentScrubber` → `showKeyMomentFrame` → `applyKeyMomentFrame` → `championPinHtml`/`objectivePinHtml` before writing any Svelte code, so the port preserves the actual interaction model rather than a guessed one.
- [ ] **Step 2: Write `GameReview.svelte`** for the score dimensions/runes/skill-progression/comparison-table parts.
- [ ] **Step 3: Write `GameReviewKeyMoments.svelte`** for the map/scrubber, using absolute-positioned pins (`style="left: {pct}%; top: {pct}%"` from `mapCoordToPct`) bound reactively to `frameIndex`, and an `<input type="range">` for the scrubber bound to the same variable.
- [ ] **Step 4: Wire both into `Report.svelte`.**
- [ ] **Step 5: Manual verification**: for at least one real game, scrub through every key moment in both the SPA and current `report.html`, confirming pin positions and the verdict callouts match at each frame.
- [ ] **Step 6: Commit**

```bash
git add frontend/src/sections/GameReview.svelte frontend/src/sections/GameReviewKeyMoments.svelte frontend/src/routes/Report.svelte
git commit -m "feat: port game review section including key-moment map to Svelte"
```

---

### Task 14: Graphs section (Plotly figures)

**Files:**
- Create: `frontend/src/sections/Graphs.svelte`
- Create: `frontend/src/lib/PlotlyFigure.svelte`
- Modify: `frontend/src/routes/Report.svelte`, `frontend/package.json` (add `plotly.js-dist-min` or confirm the CDN `<script>` approach — check report.html:17 for the exact Plotly version pin, `2.35.2`, and preserve it)
- Reference: report.html:757+ (`graphs` section markup), `setFigure`/`resizePlotlyIn`/`resizePlotlySoon` (JS lines 1129–1163).

**Interfaces:**
- Consumes: whatever figure JSON the payload carries for this section (check `bundle_to_template_context`'s figure-related keys, already read in Task 10).
- Produces: `<PlotlyFigure figure={figureJson} />` wrapping `Plotly.newPlot`/`Plotly.react` in a Svelte `onMount`/`afterUpdate` lifecycle (replacing `setFigure`'s manual `innerHTML` + script-execution hack, which only exists because Jinja-rendered HTML strings couldn't otherwise run embedded `<script>` tags — that hack is unnecessary once Plotly is called directly from Svelte lifecycle hooks).

- [ ] **Step 1: Read the reference files in full**, and decide (per the Modify note) whether to keep the Plotly CDN `<script>` tag in `index.html` or switch to an npm dependency — prefer keeping the CDN script tag matching the pinned version unless it conflicts with the Vite build, since that's the smaller change.
- [ ] **Step 2: Write `PlotlyFigure.svelte`.**
- [ ] **Step 3: Write `Graphs.svelte`**, iterating over each figure in the payload.
- [ ] **Step 4: Wire into `Report.svelte`.**
- [ ] **Step 5: Manual verification**: confirm each Plotly chart renders and resizes correctly (resize the browser window) matching current `report.html` behavior.
- [ ] **Step 6: Commit**

```bash
git add frontend/src/sections/Graphs.svelte frontend/src/lib/PlotlyFigure.svelte frontend/src/routes/Report.svelte
git commit -m "feat: port Plotly graphs section to Svelte"
```

---

### Task 15: Account filter + chatbot panel

**Files:**
- Create: `frontend/src/sections/AccountFilter.svelte`
- Create: `frontend/src/sections/Chatbot.svelte`
- Modify: `frontend/src/routes/Report.svelte`, `frontend/src/lib/api.js` (add `async function sendChatMessage(reportRef, message)` calling `POST /api/chat` — read `web/app.py`'s `/api/chat` handler at line ~1137 in full first to get the exact request/response body shape)
- Reference: report.html's account-filter markup/JS (grep `account-filter-data`/`account_filter` in report.html for the exact lines — these weren't enumerated in prior exploration, read them fresh), and the chatbot markup/JS at the tail of report.html (lines ~3540 to EOF).

**Interfaces:**
- Consumes: `data.account_filter` (unwrapped from `account_filter_json`), `data.chatbot_stats`, `data.chat_report_ref`, `data.chat_endpoint`.
- Produces: `<AccountFilter data={payload} on:accountChange={...} />` whose selection re-scopes which account-subset view the other sections read from (this is a cross-cutting concern — coordinate with how Tasks 7–14's sections read their data; if this surfaces a need to lift shared "current account filter" state up into `Report.svelte` as a store, do that refactor now rather than duplicating filter logic per section). `<Chatbot data={payload} sendMessage={sendChatMessage} />`.

- [ ] **Step 1: Read all reference files (report.html's account-filter block, the chatbot block, and `web/app.py`'s `/api/chat` handler) in full.**
- [ ] **Step 2: Decide and implement the shared account-filter state approach** (Svelte store in `frontend/src/lib/stores.js`, or prop drilling from `Report.svelte` — pick based on how many sections from Tasks 7–14 actually need to react to it; if it turns out only Task 10 (rank/peers) and Task 9 (form tracker) need it per the `_json` blobs' `views`/subset structure, prop-drilling from `Report.svelte` is simpler than a store — use judgment).
- [ ] **Step 3: Write `AccountFilter.svelte` and `Chatbot.svelte`.**
- [ ] **Step 4: Wire into `Report.svelte`**, including retrofitting the account-filter selection into whichever earlier sections need it (this may require revisiting Tasks 9/10's components to accept a `selectedAccounts` prop — do that here rather than leaving it undone).
- [ ] **Step 5: Manual verification**: switch account filters and confirm dependent sections update; send a chat message and confirm a response renders, both matching current `report.html` behavior.
- [ ] **Step 6: Commit**

```bash
git add frontend/src/sections/AccountFilter.svelte frontend/src/sections/Chatbot.svelte frontend/src/lib/api.js frontend/src/routes/Report.svelte
git commit -m "feat: port account filter and chatbot to Svelte"
```

---

### Task 16: Landing page (submit analysis, job progress, groups list)

**Files:**
- Modify: `frontend/src/routes/Landing.svelte`
- Modify: `frontend/src/lib/api.js` (add `submitAnalysis`, `fetchJob`, `fetchActivity`, `cancelJob` wrapping `POST /api/analyses`, `GET /api/jobs/{id}`, `GET /api/activity`, `POST /api/jobs/{id}/cancel` — read each handler in `web/app.py` in full for exact request/response shapes before writing the wrappers)
- Reference: `web/templates/landing.html` (371 lines) — read in full; this is the actual current source of truth for this page's markup/behavior, not report.html.

**Interfaces:**
- Consumes: the new `api.js` functions above.
- Produces: `Landing.svelte` with a submit form, a job-progress banner (polling `fetchJob` on an interval while a job is active, matching whatever poll interval `landing.html`'s current JS uses), and a list of existing groups/players linking to `/players/:slug`.

- [ ] **Step 1: Read `web/templates/landing.html` in full**, including any inline `<script>` it has, to understand current polling/submission behavior exactly.
- [ ] **Step 2: Read the four API handlers in `web/app.py` in full.**
- [ ] **Step 3: Add the four wrapper functions to `api.js`.**
- [ ] **Step 4: Write `Landing.svelte`**, porting the form, groups list, and polling logic.
- [ ] **Step 5: Manual verification**: submit a real analysis job through the SPA landing page and confirm progress updates and eventual completion link to the report, matching current `landing.html` behavior.
- [ ] **Step 6: Commit**

```bash
git add frontend/src/routes/Landing.svelte frontend/src/lib/api.js
git commit -m "feat: port landing page to Svelte"
```

---

### Task 17: Player hub page

**Files:**
- Modify: `frontend/src/routes/PlayerHub.svelte`
- Reference: `web/templates/player.html` (415 lines) — read in full.

**Interfaces:**
- Consumes: `fetchPlayerStatus` from `api.js` (already defined in Task 5).
- Produces: `PlayerHub.svelte` listing every build for the player/group with links to `/players/:slug/:buildSlug`, matching `player.html`'s current layout and any refresh/regenerate buttons it exposes (check `web/app.py`'s `/api/players/{slug}/refresh` and `/regenerate`/`/account-views` endpoints, already read in earlier exploration this session, and wire them the same way Task 16 wired the analysis-submission endpoints).

- [ ] **Step 1: Read `web/templates/player.html` in full.**
- [ ] **Step 2: Write `PlayerHub.svelte`.**
- [ ] **Step 3: Manual verification** against a real player/group with multiple builds.
- [ ] **Step 4: Commit**

```bash
git add frontend/src/routes/PlayerHub.svelte
git commit -m "feat: port player hub page to Svelte"
```

---

### Task 18: Cutover — mount the SPA, delete Jinja everywhere

**Files:**
- Modify: `src/league_stats/web/app.py` (remove `Jinja2Templates` import/usage, the `landing`/`player_page` HTML routes, mount `StaticFiles(directory=..., html=True)` at `/` pointing at `frontend/dist` — the Vite build output — with a catch-all route serving `index.html` for any unmatched path so client-side routing works on refresh)
- Modify: `src/league_stats/pipeline/orchestrator.py` (remove the `ReportBuilder`/`builder.render(... "report.html" ...)` call entirely — keep only the `report.json` write from Task 2)
- Modify: `src/league_stats/web/worker.py` (remove the equivalent HTML render call if Task 3 found a distinct path)
- Delete: `src/league_stats/web/templates/` (entire directory)
- Delete: `src/league_stats/presentation/templates/` (entire directory: `report.html`, `player_hub.html`, `_macros.html`, all of `generated/`, `static/`)
- Modify or delete: `src/league_stats/presentation/report.py` (remove `ReportBuilder` class entirely, remove `render_player_hub`; keep `discover_player_builds`, `build_manifest_entry`, `write_report_meta`, `discover_reports` etc. if they're still used for build discovery — check each remaining function's callers before deleting)
- Modify or delete: `src/league_stats/presentation/report_static.py` (this exists to copy CSS for the old `report.html` — check whether the SPA's own Vite build handles its own CSS bundling, in which case this whole file is dead)
- Delete: `frontend/scripts/generate.js`, `frontend/scripts/ssr-compile.js`, `frontend/manifest.json`
- Modify: `frontend/package.json` (remove the `"generate"` script and any deps only used by it, e.g. check if `svelte/compiler`'s SSR usage was a separate import path)
- Modify: `.github` CI workflow / `deploy/build_preview.sh` / `netlify.toml` if they invoke `npm run generate` or expect `report.html` to exist anywhere (grep for these before assuming — this was flagged in the spec's "Out of scope" section as needing an audit)

**Interfaces:**
- Produces: FastAPI serves the SPA's static build at `/`, the JSON API at `/api/*`, and generated build data (still, per Task 2, at `run_dir` — but now that directory contains `report.json`, not `report.html`) at whatever static path the app already uses for build artifacts (check `/out` mount still makes sense — it may now only need to serve non-HTML assets like exported CSVs, if any remain; confirm this doesn't silently break something).

- [ ] **Step 1: Grep the whole repo for `report.html`, `player_hub.html`, `landing.html`, `Jinja2Templates`, `generate.js` to build a complete list of every reference before deleting anything.**

Run: `grep -rln "report\.html\|player_hub\.html\|Jinja2Templates\|generate\.js\|ssr-compile" --include="*.py" --include="*.js" --include="*.sh" --include="*.toml" --include="*.yml" --include="*.yaml" . | grep -v node_modules | grep -v .venv`

- [ ] **Step 2: For each file found, read it and either update or confirm it's being deleted in this task.** Do not leave a dangling reference.

- [ ] **Step 3: Build the SPA for real** (`cd frontend && npx vite build`) so `web/app.py`'s static mount has something real to point at.

- [ ] **Step 4: Implement the `app.py` changes** (Jinja removal, static SPA mount with catch-all fallback route — FastAPI pattern: mount `/assets` etc. from the Vite build's asset subfolder explicitly, then a final `@app.get("/{full_path:path}")` catch-all returning `FileResponse(spa_dist / "index.html")` for anything not matched by an earlier API/static route — register it *last* so it doesn't shadow `/api/*`).

- [ ] **Step 5: Implement the pipeline/worker/presentation deletions from the Files list above.**

- [ ] **Step 6: Run the full test suite**

Run: `.venv/bin/pytest tests/ -v`
Expected: every test passes, or fails only because it asserted `report.html` exists (fix those tests to assert `report.json` instead — this is expected fallout, not a regression to avoid, but each one must be actually fixed, not skipped/xfailed)

- [ ] **Step 7: Manual end-to-end verification**: `.venv/bin/uvicorn league_stats.web.app:app` (or whatever the real entry point is per `web/__main__.py`), visit `/`, submit an analysis, navigate to the player hub and a report, confirm every section still renders — this is the final acceptance gate for the entire migration, run it for real, don't skip it because "the pieces were verified individually."

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat: cut over to the Svelte SPA, retire Jinja/report.html rendering entirely"
```

---

### Task 19: Update remaining tests for the JSON-only world

**Files:**
- Modify: any test found still failing after Task 18's Step 6 that isn't already covered by Tasks 1–4's new tests. Based on the test inventory gathered during exploration, check at minimum: `tests/test_reports.py`, `tests/test_report_static.py`, `tests/test_build_preview_report.py`, `tests/test_index_refresh.py`, `tests/test_skip_unchanged_build.py`, `tests/test_web_jobs.py`.

**Interfaces:**
- Consumes: nothing new.
- Produces: a fully green test suite with no test asserting `report.html`/`player_hub.html`/Jinja-rendered content exists anywhere.

- [ ] **Step 1: Re-run the full suite and list every remaining failure.**

Run: `.venv/bin/pytest tests/ -v 2>&1 | grep FAILED`

- [ ] **Step 2: For each failure, read the test in full, understand what it was actually verifying (build succeeded / file discovery works / manifest correctness), and rewrite its assertions against `report.json`/the new API instead of the deleted HTML** — do not delete a test's coverage, port it.
- [ ] **Step 3: Re-run until green.**

Run: `.venv/bin/pytest tests/ -v`
Expected: all PASS

- [ ] **Step 4: Commit**

```bash
git add tests/
git commit -m "test: update remaining tests for JSON-only report pipeline"
```

---

### Task 20: Open the PR

**Files:** none (process step)

- [ ] **Step 1: Push the branch**

```bash
cd /Users/brice.parent/perso/league-champion-stats-analysis-svelte-spa
git push -u origin feat/svelte-spa-migration
```

- [ ] **Step 2: Open the PR against `feat/svelte-design-system-pipeline`, not `main`**

```bash
GH_TOKEN=$(gh auth token --user Flowtter) gh pr create \
  --base feat/svelte-design-system-pipeline \
  --head feat/svelte-spa-migration \
  --title "Migrate report rendering from Jinja to a Svelte SPA + JSON API" \
  --body "$(cat <<'EOF'
## Summary
- Pipeline writes report.json instead of report.html; report.html generation is fully removed.
- New GET /api/players/{slug}/builds/{build_slug} endpoint serves the JSON payload.
- frontend/ is now a Vite + Svelte 4 SPA (client-side routed) instead of a Jinja-partial compiler; every report section, the landing page, and the player hub are ported.
- web/templates/, presentation/templates/, and the generate.js/ssr-compile.js toolchain are deleted.

## Test plan
- [ ] pytest tests/ passes in full
- [ ] Manual: submit an analysis via the SPA landing page end to end
- [ ] Manual: every report section (overview, coaching, form tracker, rank/peers, matchups, champion deep-dive, game review incl. key-moment map, graphs, account filter, chatbot) verified against a real build
- [ ] Manual: player hub lists builds and links correctly
EOF
)"
```

- [ ] **Step 3: If any task above was not fully completed and verified, mark the PR draft instead** (`gh pr create --draft ...`) and edit the body's Test plan checklist to accurately reflect what's actually done vs. remaining — the PR description must match reality, not the plan's aspiration.
