#!/usr/bin/env bash
# Netlify build command: builds the Svelte SPA. The preview has no backend of
# its own — netlify.toml proxies /api/* and /out/* to the real deployed app,
# so this script only needs to produce the static frontend bundle.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"

echo "→ building the Svelte SPA"
(cd frontend && npm ci && npm run build)
