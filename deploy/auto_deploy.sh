#!/usr/bin/env bash
# Poll origin/main for new commits and deploy automatically when one lands.
#
# Runs forever, checking every 10s by default. Meant to be kept alive by
# something else (systemd, screen, tmux, nohup), e.g. on the VPS:
#   nohup ./deploy/auto_deploy.sh >> deploy/auto_deploy.log 2>&1 &
#
# Override with env vars: DEPLOY_BRANCH=main POLL_INTERVAL_S=10
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"

BRANCH="${DEPLOY_BRANCH:-main}"
POLL_INTERVAL_S="${POLL_INTERVAL_S:-10}"

log() { echo "[$(date -u +%FT%TZ)] $*"; }

current_branch="$(git rev-parse --abbrev-ref HEAD)"
if [[ "$current_branch" != "$BRANCH" ]]; then
  log "error: checked out branch is '${current_branch}', not '${BRANCH}' -- refusing to auto-pull"
  exit 1
fi

deploy() {
  if ! git diff --quiet || ! git diff --cached --quiet; then
    log "error: uncommitted local changes present -- refusing to pull, skipping this cycle"
    return 1
  fi
  log "new commit on origin/${BRANCH} -- deploying"
  git pull --ff-only origin "$BRANCH"
  ./deploy/build_spa.sh
  ./deploy/run.sh
  log "deploy finished, now at $(git rev-parse --short HEAD)"
}

log "watching origin/${BRANCH} every ${POLL_INTERVAL_S}s (currently at $(git rev-parse --short HEAD))"
while true; do
  if git fetch --quiet origin "$BRANCH"; then
    local_rev="$(git rev-parse HEAD)"
    remote_rev="$(git rev-parse "origin/${BRANCH}")"
    if [[ "$local_rev" != "$remote_rev" ]]; then
      deploy || log "deploy failed or skipped, will retry next poll"
    fi
  else
    log "git fetch failed, will retry next poll"
  fi
  sleep "$POLL_INTERVAL_S"
done
