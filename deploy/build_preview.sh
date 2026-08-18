#!/usr/bin/env bash
# Netlify build command: builds the Svelte SPA, syncs Python deps, then
# fails loudly — see the explanation below before touching this file.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"

if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

echo "→ building the Svelte SPA"
(cd frontend && npm ci && npm run build)

echo "→ syncing Python dependencies"
uv sync

cat <<'EOF' >&2
Netlify preview is intentionally disabled after the Svelte SPA migration.

The old preview rendered fully static HTML (Jinja report.html + player hub
index.html) that needed no backend, so Netlify's static hosting could serve
it directly. The SPA is different: every page (landing, player hub, report)
fetches live data from /api/* at runtime, and Netlify only serves static
files — there is no FastAPI process behind this deploy to answer those
requests. Building the SPA above and serving it as-is would just show a
perpetual loading/empty state, which is worse than a clear failure.

A real fix needs a static-data shim (pre-generate report.json-shaped fetch
responses and add netlify.toml redirects for /api/groups, /api/players/:slug,
/api/players/:slug/builds/:build_slug) — deliberately left as follow-up work,
not bundled into the Svelte SPA cutover task. See the migration plan
(2026-08-18-svelte-spa-migration, Task 18) for context.
EOF
exit 1
