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
