# AGENTS.md — AI navigation guide

League Champion Analyser: downloads ranked LoL matches via Riot Match-V5, parses timelines,
runs coaching analytics, and renders interactive HTML dashboards plus CSV/JSON exports.

All user-facing workflows go through the **web UI**. There is no analysis CLI.

## Layer map (where to put code)

| Layer | Path | Rule |
|-------|------|------|
| Core | `src/league_stats/core/` | Config, Pydantic models, champion/role helpers — no I/O |
| Infra | `src/league_stats/infra/` | HTTP, SQLite cache, DDragon assets |
| Ingest | `src/league_stats/ingest/` | Raw JSON → `MatchRecord` |
| Pipeline | `src/league_stats/pipeline/` | Orchestration, frames, bundles, view models |
| Analysis | `src/league_stats/analysis/` | Pure stats on records/DataFrames |
| Presentation | `src/league_stats/presentation/` | HTML, charts, exports, icons |
| Web | `src/league_stats/web/` | FastAPI app, job queue (`jobs.py`), worker (`worker.py`), chat proxy; entry via `__main__.py` |

## Naming glossary

- **role** = Riot `teamPosition` = UI "lane" (`TOP`, `MIDDLE`, `JUNGLE`, …)
- **build** = champion + role pair (e.g. Viktor mid → `viktor_middle`)
- **reports_group_slug** = filesystem slug for one player or a pooled multi-player group

## Add a new metric (recipe)

1. Timeline field? Add `extract_*` in `analysis/<domain>.py` and wire in `ingest/parser.py`.
2. Column on matches/deaths table? Add to `pipeline/frames.py` → `build_analysis_frames()`.
3. Dashboard card? Add label to `presentation/ui_icons.py` `METRIC_ICONS` and a card row in `pipeline/bundles.py`.
4. Coaching tip? Add a rule in `analysis/coach/engine.py`.
5. Chatbot should know? Extend `pipeline/summaries.py` → `build_export_summary()`.

## Form Tracker (progression diff)

Compares **recent form** (default last 20 games) vs a **personal baseline** (default games 21–100, non-overlapping).

1. Metric / delta logic? `analysis/progression/metrics.py`, `diff.py`, `stats.py`
2. Behavioral shifts? `analysis/progression/shifts.py`
3. Diff coaching tips? `analysis/progression/coach.py`
4. Pipeline wiring? `pipeline/progression.py` → `build_progression_views()`; orchestrator embeds `progression_views_json`
5. Dashboard section? `presentation/templates/report.html` `#form-tracker` (Performance tab) + `renderFormProgression()` JS
6. Charts? `presentation/graphs.py` → `form_rolling_wr`, `form_metric_delta_bar`
7. Config? `core/config.py` `[progression]` table in `config.toml` or `progression_*` on `AppConfig`

Form Tracker is **orthogonal** to the game-window toggle (Last 50/100/All) — it always slices from the full queue-filtered record list.

## Game Review (per-match deep dive)

Last **10 games** per queue filter (rail shows 5, with expand for 5 more) with personal-baseline game scores, behavior bullets, and event tabs.

1. Score / behavior rules? `analysis/game_review/score.py`, `behaviors.py`
2. Per-game assembly? `analysis/game_review/assemble.py`, `views.py`
3. Pipeline wiring? `pipeline/game_review.py` → orchestrator embeds `game_review_json`
4. Dashboard section? `presentation/templates/report.html` `#game-review` + `renderGameReview()` JS (dedicated **Game Review** category tab)
5. Charts? `presentation/graphs.py` → `game_gold_timeline`
6. Chatbot? `analysis/game_review/export.py` → `build_export_summary()` `recent_games` key
7. Config? `GAME_REVIEW_*` in `core/config.py`
8. Key moments (team-impact map scrubber)? `analysis/key_moments.py` at parse time; tab in `report.html`; map asset via `DDragonAssets.map_href()`

Game Review is **orthogonal** to the game-window toggle — it follows the queue filter only.

## Account filter (group reports)

Group reports get an **Accounts** dropdown in the filter bar (name, tag, rank, per-account toggle).

1. Record slicing? `pipeline/bundles.py` → `filter_records_by_accounts()`
2. Precompute? `pipeline/orchestrator.py` → `account_subset_keys()` / `build_account_subset_views()`; every combination for ≤4 members (`ACCOUNT_FULL_COMBINATION_LIMIT`), otherwise singletons only; embedded as `account_filter_json`
3. Non-precomputed combinations? `POST /api/players/{slug}/builds/{build}/account-views` in `web/app.py` (rebuilds from the match store, disk-cached under `account_views/`)
4. UI? `report.html` `#account-filter-bar` + `applyAccountSelection()` JS; swaps the same view JSON as the queue/window toggles
5. Peer comparison stays full-group only — subsets render without peer data

## Icons

- Iconify keys in `presentation/ui_icons.py` → `ICONIFY_ICONS`
- Local PNG assets via `DDragonAssets.ui_icon_href` → `ICON_ASSET_FILES`
- Summoner spells → `output/assets/summoners/{SpellName}.png`
- Rune style trees → `output/assets/rune_trees/{TreeName}.png` (Precision, Domination, …)

## Web app

`uv run python main.py` (or `uv run python -m league_stats.web`) starts FastAPI
(`web/app.py`) with a DB-backed job queue (`data/app.sqlite`, `web/jobs.py`)
drained by an in-process worker thread (`web/worker.py`).

- Jobs run **two-stage**: stage A renders every build with `peer_comparison=None`
  (state `report_ready`), stage B re-renders per build as peer data lands (`done`).
  Peer-stage failures are soft: the base report stays served.
- The orchestrator seams are `prepare_builds()` / `analyze_build()` /
  `build_peer_for_pool()` in `pipeline/orchestrator.py`; `run_all_builds()`
  composes the same functions single-stage (used by tests).
- Progress flows through `core/progress.py` `ProgressReporter` → `Services.progress`
  → job row; the frontend polls `GET /api/jobs/{id}` and `GET /api/players/{slug}`.
- Generated reports stay on disk under `output/` and are served statically at `/out`.
- All jobs share one process-wide `RateLimiter` (`shared_rate_limiter()` in
  `infra/riot_api.py`); keep `worker_concurrency=1` on a dev Riot key.
- Web-rendered reports set `AppConfig.chat_endpoint`/`status_endpoint`: chat goes
  through `POST /api/chat` (key server-side), and the report page polls for
  peer-stage completion and reloads itself.

## Security

Web-served reports never embed `GEMINI_API_KEY` — chat is proxied via `POST /api/chat`.
Do not share HTML that was rendered without `chat_endpoint` if a real key was baked in.

## Commands

```bash
uv sync
uv run python main.py                    # web app on 127.0.0.1:8000
uv run python -m league_stats.web        # same
uv run pytest                            # parallel, skips integration tests (~1 min)
uv run pytest -m integration             # real DDragon CDN download (~6 min, run explicitly)
```

## Tests

Synthetic fixtures in `tests/fixtures.py`. One module per area: `tests/test_<module>.py`.
Default `pytest` runs with `pytest-xdist` (`-n auto`) and excludes `@pytest.mark.integration`
tests (hit real external services) via `addopts` in `pyproject.toml`.

## Netlify preview build

`deploy/build_preview_report.py` (invoked by `deploy/build_preview.sh` via `netlify.toml`)
renders a synthetic multi-report set so every PR gets a live Deploy Preview, with no
Riot API key or secrets involved. It mirrors `tests/test_reports.py`'s `_make_records`/
`_peer`/`_config` helpers and reuses `tests/fixtures.py` directly.

**Update this mock whenever:**
- `MatchRecord`, `PeerComparisonResult`, `RankedEntry`, or `AppConfig` gain/rename fields
- `tests/fixtures.py` changes shape (`make_match`, `make_timeline`, `FAKE_ITEMS`, `MY_PUUID`)
- A report section starts expecting a new peer metric — add the key to `_PEER_METRICS`
  in `deploy/build_preview_report.py`, or that section renders with placeholder/missing data
  in every preview without any test failing (the existing tests only check that a report
  and hub page are produced, not that every metric is populated)

`tests/test_build_preview_report.py` only catches structural breaks (missing hub page,
wrong report count) — it will not catch stale/incomplete mock data, so keep the mock's
shape intentionally in sync rather than relying on that test to flag drift.
