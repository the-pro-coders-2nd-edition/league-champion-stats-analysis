# Report component library

Svelte components that get compiled into the Jinja templates used by
`src/league_stats/presentation`.

## Workflow

Edited a `.svelte` file? Run:

```bash
cd frontend
npm run generate
```

Then commit the regenerated files under
`src/league_stats/presentation/templates/generated/` along with your component
change. CI checks that the generated output matches what a fresh `npm run generate`
produces — catching this locally is faster than waiting on CI.

## Rules

- Pin Svelte 4. Do not upgrade to Svelte 5 without re-validating the SSR
  compile/render mechanics this pipeline depends on.
- No spread attributes (`{...props}`), no `<svelte:element>` with a dynamic tag
  name, no `contenteditable`/`bind:innerText`/`bind:textContent` — these map to
  known Svelte 4 SSR XSS advisories (see `npm audit`). Plain `export let` props
  bound to individual attributes only.
- Components never fetch data or contain business logic (tone/verdict/threshold
  computation). That logic lives once, in Python
  (`src/league_stats/presentation/tones.py`), and is passed in as an
  already-resolved prop (e.g. `tone="good"`) via a Jinja token at generation time.
- Every component uses `<script lang="ts">`. Props with a closed vocabulary
  get a `type` alias (e.g. `type Tone = 'good' | 'warn' | 'bad' | 'flat' |
  'accent'`) instead of a comment. This documents the contract but does not
  validate it at generation time: `generate.js` reads `manifest.json` as
  untyped JSON and calls the compiled component directly, so a bad literal in
  the manifest (or a bad value produced by the Python code behind a Jinja
  token prop) is not caught by TypeScript — types are erased before that call
  happens. There is deliberately no runtime `.includes()`/throw check either.
- CSS lives in the component's own `<style>` block, not in `report.css`. Any
  class also rebuilt by a client-side JS re-render function (grep `report.html`
  for the class name) must be wrapped in `:global(...)` — Svelte's scoping
  attribute is only added to elements the compiler renders, so JS-injected
  markup would silently fall outside a scoped rule.
