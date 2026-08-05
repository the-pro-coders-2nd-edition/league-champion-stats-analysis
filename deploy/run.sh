#!/usr/bin/env bash
# Install/start the app behind Caddy (systemd). Run on the VPS as root:
#
#   ./deploy/run.sh                     # uses DOMAIN from .env
#   ./deploy/run.sh --domain example.com
#   ./deploy/run.sh --http-only          # no TLS; reverse-proxy :80 → app
#   ./deploy/run.sh --status             # show service status
#   ./deploy/run.sh --stop
#
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_NAME="league-stats"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
CADDYFILE="/etc/caddy/Caddyfile"
APP_HOST="127.0.0.1"
APP_PORT="8000"
DEFAULT_DOMAIN="league-champion-analyser.eu"
HTTP_ONLY=0
ACTION="install"

# Prefer DOMAIN already in the environment, else .env, else the project default.
load_domain_from_env_file() {
  local env_file="$APP_DIR/.env"
  [[ -f "$env_file" ]] || return 0
  local line value
  line="$(grep -E '^[[:space:]]*DOMAIN=' "$env_file" | tail -n 1 || true)"
  [[ -n "$line" ]] || return 0
  value="${line#*=}"
  value="${value%\"}"
  value="${value#\"}"
  value="${value%\'}"
  value="${value#\'}"
  value="$(echo "$value" | tr -d '[:space:]')"
  [[ -n "$value" ]] && DOMAIN_FROM_FILE="$value"
}

DOMAIN_FROM_FILE=""
load_domain_from_env_file
DOMAIN="${DOMAIN:-${DOMAIN_FROM_FILE:-$DEFAULT_DOMAIN}}"

usage() {
  cat <<EOF
Usage: deploy/run.sh [options]

  --domain NAME   Domain for HTTPS (Caddy + Let's Encrypt). Also: DOMAIN=...
                  Default: ${DEFAULT_DOMAIN} (or DOMAIN in .env)
  --http-only     Serve HTTP on :80 only (no domain / no TLS)
  --status        Show league-stats + caddy status
  --stop          Stop both services
  -h, --help      Show this help

Examples:
  ./deploy/run.sh
  ./deploy/run.sh --domain myapp.example.com
  ./deploy/run.sh --http-only
EOF
}

die() { echo "error: $*" >&2; exit 1; }

need_root() {
  [[ "$(id -u)" -eq 0 ]] || die "run as root (ssh into the VPS first)"
}

find_uv() {
  if [[ -n "${UV_BIN:-}" && -x "$UV_BIN" ]]; then
    echo "$UV_BIN"
    return
  fi
  if command -v uv >/dev/null 2>&1; then
    command -v uv
    return
  fi
  for candidate in "$HOME/.local/bin/uv" /root/.local/bin/uv; do
    if [[ -x "$candidate" ]]; then
      echo "$candidate"
      return
    fi
  done
  die "uv not found — install with: curl -LsSf https://astral.sh/uv/install.sh | sh"
}

ensure_caddy() {
  if command -v caddy >/dev/null 2>&1; then
    return
  fi
  echo "→ installing Caddy"
  apt-get update -qq
  if apt-get install -y caddy; then
    return
  fi
  apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    | tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  apt-get update -qq
  apt-get install -y caddy
}

write_caddyfile() {
  mkdir -p "$(dirname "$CADDYFILE")"
  if [[ "$HTTP_ONLY" -eq 1 ]]; then
    cat >"$CADDYFILE" <<EOF
:80 {
	reverse_proxy ${APP_HOST}:${APP_PORT}
}
EOF
  else
    local sites="$DOMAIN"
    if [[ "$DOMAIN" != www.* ]]; then
      sites="${DOMAIN}, www.${DOMAIN}"
    fi
    cat >"$CADDYFILE" <<EOF
${sites} {
	reverse_proxy ${APP_HOST}:${APP_PORT}
}
EOF
  fi
  echo "→ wrote $CADDYFILE"
}

write_systemd_unit() {
  local uv_bin="$1"
  local env_line=""
  if [[ -f "$APP_DIR/.env" ]]; then
    env_line="EnvironmentFile=${APP_DIR}/.env"
  fi

  cat >"$SERVICE_FILE" <<EOF
[Unit]
Description=League Champion Stats Analyzer
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=${APP_DIR}
${env_line}
ExecStart=${uv_bin} run python main.py --host ${APP_HOST} --port ${APP_PORT}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
  echo "→ wrote $SERVICE_FILE"
}

show_status() {
  systemctl status "$SERVICE_NAME" --no-pager || true
  echo
  systemctl status caddy --no-pager || true
}

stop_all() {
  need_root
  systemctl stop "$SERVICE_NAME" 2>/dev/null || true
  systemctl stop caddy 2>/dev/null || true
  echo "stopped ${SERVICE_NAME} and caddy"
}

install_and_start() {
  need_root

  [[ -f "$APP_DIR/main.py" ]] || die "main.py not found in $APP_DIR"
  if [[ ! -f "$APP_DIR/.env" ]]; then
    echo "warning: ${APP_DIR}/.env missing — set RIOT_API_KEY (and optional GEMINI_API_KEY)" >&2
  elif ! grep -qE '^[[:space:]]*DOMAIN=' "$APP_DIR/.env"; then
    printf '\n# Public site domain (used by deploy/run.sh for Caddy + HTTPS)\nDOMAIN=%s\n' "$DOMAIN" >>"$APP_DIR/.env"
    echo "→ added DOMAIN=${DOMAIN} to ${APP_DIR}/.env"
  fi

  if [[ "$HTTP_ONLY" -eq 0 && -z "$DOMAIN" ]]; then
    die "pass --domain example.com (HTTPS) or --http-only (plain :80)"
  fi

  local uv_bin
  uv_bin="$(find_uv)"

  echo "→ syncing dependencies"
  (cd "$APP_DIR" && "$uv_bin" sync)

  ensure_caddy
  write_caddyfile
  write_systemd_unit "$uv_bin"

  systemctl daemon-reload
  systemctl enable --now "$SERVICE_NAME"
  systemctl enable --now caddy
  systemctl reload caddy || systemctl restart caddy

  echo
  show_status
  echo
  if [[ "$HTTP_ONLY" -eq 1 ]]; then
    echo "App is up: http://$(hostname -I | awk '{print $1}')/"
  else
    echo "App is up: https://${DOMAIN}/"
    echo "(DNS A records for ${DOMAIN} / www must point at this server for TLS to work.)"
  fi
  echo "Logs: journalctl -u ${SERVICE_NAME} -f"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain)
      DOMAIN="${2:-}"
      [[ -n "$DOMAIN" ]] || die "--domain needs a value"
      shift 2
      ;;
    --http-only)
      HTTP_ONLY=1
      shift
      ;;
    --status)
      ACTION="status"
      shift
      ;;
    --stop)
      ACTION="stop"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown option: $1 (try --help)"
      ;;
  esac
done

case "$ACTION" in
  install) install_and_start ;;
  status) need_root; show_status ;;
  stop) stop_all ;;
esac
