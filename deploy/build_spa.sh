#!/usr/bin/env bash
# Build the Svelte SPA into src/league_stats/web/spa_dist/ (required for the web UI).
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

die() { echo "error: $*" >&2; exit 1; }

command -v npm >/dev/null 2>&1 || die "npm not found — install Node.js (https://nodejs.org/)"

echo "→ building the Svelte SPA"
(cd "$APP_DIR/frontend" && npm ci && npm run build)
