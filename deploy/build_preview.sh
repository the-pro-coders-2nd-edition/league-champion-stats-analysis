#!/usr/bin/env bash
# Netlify build command: compiles the Svelte components, syncs Python deps,
# then renders a synthetic multi-report set into output/ for PR previews.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"

if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

echo "→ compiling Svelte components"
(cd frontend && npm ci && npm run generate)

echo "→ syncing Python dependencies"
uv sync

echo "→ building preview report"
uv run python deploy/build_preview_report.py
