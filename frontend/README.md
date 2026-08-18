# Report frontend

Svelte 4 single-page app that renders the report UI (nav, sections, career
mode, etc.). It talks to the FastAPI backend (`src/league_stats/web/app.py`)
over `/api` and reads generated report data from `/out`.

## Workflow

```bash
cd frontend
npm install     # first time / after dependency changes
npm run dev      # dev server on http://localhost:5173, proxies /api and /out to :8000
npm run build    # type-checks via the Vite/Svelte build and emits the production bundle
npx svelte-check --threshold error   # standalone type/template check, run before pushing
```

`npm run dev` expects the backend running locally on port 8000 (see the repo
root README for how to start it) since `/api` and `/out` requests are proxied
there.

`npm run build` writes the production bundle to
`../src/league_stats/web/spa_dist/` (see `vite.config.js`), which
`league_stats.web.app` serves at `/`. There is no separate "generate" step and
no server-side templating — the backend serves this built SPA directly and
exposes JSON/report data for it to fetch.

CI (`.github/workflows/pytest.yml`, `frontend` job) runs `npm ci`,
`npx svelte-check --threshold error`, and `npm run build` on every push.

## Rules

- Pin Svelte 4. Do not upgrade to Svelte 5 without re-validating the
  compile/render mechanics this app depends on.
- No spread attributes (`{...props}`), no `<svelte:element>` with a dynamic
  tag name, no `contenteditable`/`bind:innerText`/`bind:textContent` — these
  map to known Svelte 4 SSR/XSS advisories (see `npm audit`). Plain
  `export let` props bound to individual attributes only.
- Components never fetch data or contain business logic (tone/verdict/
  threshold computation). That logic lives once, in Python
  (`src/league_stats/presentation/tones.py`) and its JS mirror
  (`src/lib/`), and is passed in as an already-resolved prop (e.g.
  `tone="good"`).
- Props with a closed vocabulary get a `type` alias (e.g. `type Tone = 'good'
  | 'warn' | 'bad' | 'flat' | 'accent'`) instead of a comment, to document the
  contract even where TypeScript can't fully enforce it across the JSON
  boundary with the backend.
- Shared visual styling lives in `src/styles/`; component-specific styling
  lives in the component's own `<style>` block.
