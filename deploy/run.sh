#!/usr/bin/env bash
# Install/start Caddy (systemd) as the public HTTPS front door for the
# docker-compose stack (api-ui + grafana). This script does NOT start the
# app itself -- run `docker compose up -d` (from the repo root, on the VPS)
# separately; this script only manages Caddy's TLS routing in front of it.
#
#   ./deploy/run.sh                        # uses DOMAIN from .env
#   ./deploy/run.sh --domain example.com
#   ./deploy/run.sh --app-port 7999        # api-ui's published host port,
#                                           # if docker-compose.yml's default
#                                           # (8000) is already taken
#   ./deploy/run.sh --http-only            # no TLS; reverse-proxy :80 → app
#   ./deploy/run.sh --status               # show caddy's status
#   ./deploy/run.sh --stop                 # stop caddy
#
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CADDYFILE="/etc/caddy/Caddyfile"
APP_HOST="127.0.0.1"
# api-ui's published host port (docker-compose.yml's `ports: - "8000:8000"`
# by default). Override with --app-port if that mapping's host side has been
# changed (e.g. because something else on the VPS already held 8000).
APP_PORT="8000"
# Grafana (docker-compose service `grafana`) publishes only to host loopback
# (`127.0.0.1:3000:3000` in docker-compose.yml) -- never a public port -- so
# this host-based Caddy reaches it the same way it reaches the app itself.
GRAFANA_HOST="127.0.0.1"
GRAFANA_PORT="3000"
# mongo-express (docker-compose service `mongo-express`) publishes only to
# host loopback (`127.0.0.1:8081:8081` in docker-compose.yml) -- never a
# public port -- so this host-based Caddy reaches it the same way it
# reaches Grafana above. Unlike Grafana, mongo-express has NO fallback
# direct-port path at all: it must go through this Caddy site block so its
# access log gives fail2ban something to watch for failed Basic Auth logins.
MONGO_EXPRESS_HOST="127.0.0.1"
MONGO_EXPRESS_PORT="8081"
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

  --domain NAME    Domain for HTTPS (Caddy + Let's Encrypt). Also: DOMAIN=...
                    Default: ${DEFAULT_DOMAIN} (or DOMAIN in .env)
  --app-port PORT  api-ui's published host port from docker-compose.yml.
                    Default: ${APP_PORT}
  --http-only      Serve HTTP on :80 only (no domain / no TLS)
  --status         Show caddy status
  --stop           Stop caddy
  -h, --help       Show this help

Examples:
  ./deploy/run.sh
  ./deploy/run.sh --domain myapp.example.com
  ./deploy/run.sh --app-port 7999
  ./deploy/run.sh --http-only
EOF
}

die() { echo "error: $*" >&2; exit 1; }

need_root() {
  [[ "$(id -u)" -eq 0 ]] || die "run as root (ssh into the VPS first)"
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
  # API-UI's /metrics (Phase 6, Task 3) reuses the app's own public HTTP
  # server -- unlike RUNNER/CronWatch/PEERS, which each expose metrics only on
  # a dedicated internal-only port never routed through Caddy -- so without a
  # gate here it is the one metrics endpoint in this whole system that is
  # publicly, unauthenticated internet-reachable (Phase 6 final review,
  # Finding 3). `route /metrics { ... }` forces this block's own directives to
  # run in the exact order written (Caddy's default directive sorting is not
  # guaranteed to run `respond` before `reverse_proxy` otherwise), so a
  # request for /metrics from outside `private_ranges` (RFC1918 + loopback +
  # link-local -- Caddy's built-in named IP range) gets a 403 before ever
  # reaching `reverse_proxy`; every other path, and /metrics itself from a
  # private/loopback caller (e.g. Prometheus running on this same host, or an
  # operator over a VPN/bastion in a private range), is unaffected.
  local metrics_gate='	route /metrics {
		@not_private not remote_ip private_ranges
		respond @not_private 403
		reverse_proxy '"${APP_HOST}:${APP_PORT}"'
	}
'
  if [[ "$HTTP_ONLY" -eq 1 ]]; then
    cat >"$CADDYFILE" <<EOF
:80 {
${metrics_gate}	reverse_proxy ${APP_HOST}:${APP_PORT}
}
EOF
  else
    local sites="$DOMAIN"
    if [[ "$DOMAIN" != www.* ]]; then
      sites="${DOMAIN}, www.${DOMAIN}"
    fi
    local grafana_domain="grafana.${DOMAIN}"
    local grafana_sites="$grafana_domain"
    if [[ "$grafana_domain" != www.* ]]; then
      grafana_sites="${grafana_domain}, www.${grafana_domain}"
    fi
    local microservice_domain="microservice.${DOMAIN}"
    local microservice_sites="$microservice_domain"
    if [[ "$microservice_domain" != www.* ]]; then
      microservice_sites="${microservice_domain}, www.${microservice_domain}"
    fi
    local mongo_domain="mongo.${DOMAIN}"
    local mongo_sites="$mongo_domain"
    if [[ "$mongo_domain" != www.* ]]; then
      mongo_sites="${mongo_domain}, www.${mongo_domain}"
    fi
    # NOTE (external, manual, one-time precondition): a DNS A record for
    # grafana.${DOMAIN}, microservice.${DOMAIN}, and mongo.${DOMAIN} (and
    # each one's www. alias above) must already point at this server before
    # Let's Encrypt can issue a cert for them -- Caddy's automatic TLS will
    # fail/retry indefinitely for a site block until its DNS record exists.
    # This is not something to fake or work around here; it's an out-of-band
    # DNS change the operator makes once. microservice.${DOMAIN} is a
    # transitional site block for running the docker-compose stack side by
    # side with the site at ${DOMAIN} during cutover testing -- both
    # currently proxy to the same APP_HOST:APP_PORT, so this only matters
    # while ${DOMAIN} still points somewhere else (e.g. an old deployment);
    # remove this block once ${DOMAIN} itself is cut over to the
    # docker-compose stack.
    #
    # mongo.${DOMAIN}'s `log` directive writes this site's own access log to
    # a host path (Caddy's default JSON format, which already includes a
    # `status` field per request) so fail2ban's mongo-express jail
    # (deploy/fail2ban/jail.d/mongo-express.conf) has a real file to watch --
    # mongo-express itself has no failed-login log of its own, unlike
    # Grafana's GF_LOG_MODE=console file above, so this Caddy access log is
    # the only place a failed Basic Auth attempt against mongo-express ever
    # lands on disk.
    cat >"$CADDYFILE" <<EOF
${sites} {
${metrics_gate}	reverse_proxy ${APP_HOST}:${APP_PORT}
}

${grafana_sites} {
	reverse_proxy ${GRAFANA_HOST}:${GRAFANA_PORT}
}

${microservice_sites} {
${metrics_gate}	reverse_proxy ${APP_HOST}:${APP_PORT}
}

${mongo_sites} {
	log {
		output file /var/log/caddy/mongo-express-access.log
	}
	reverse_proxy ${MONGO_EXPRESS_HOST}:${MONGO_EXPRESS_PORT}
}
EOF
  fi
  echo "→ wrote $CADDYFILE (app: ${APP_HOST}:${APP_PORT}, grafana: ${GRAFANA_HOST}:${GRAFANA_PORT}, mongo-express: ${MONGO_EXPRESS_HOST}:${MONGO_EXPRESS_PORT})"
}

show_status() {
  systemctl status caddy --no-pager || true
}

stop_all() {
  need_root
  systemctl stop caddy 2>/dev/null || true
  echo "stopped caddy"
}

install_and_start() {
  need_root

  [[ -f "$APP_DIR/docker-compose.yml" ]] || die "docker-compose.yml not found in $APP_DIR"
  if [[ ! -f "$APP_DIR/.env" ]]; then
    echo "warning: ${APP_DIR}/.env missing — set RIOT_API_KEY etc. for docker-compose" >&2
  elif ! grep -qE '^[[:space:]]*DOMAIN=' "$APP_DIR/.env"; then
    printf '\n# Public site domain (used by deploy/run.sh for Caddy + HTTPS)\nDOMAIN=%s\n' "$DOMAIN" >>"$APP_DIR/.env"
    echo "→ added DOMAIN=${DOMAIN} to ${APP_DIR}/.env"
  fi

  if [[ "$HTTP_ONLY" -eq 0 && -z "$DOMAIN" ]]; then
    die "pass --domain example.com (HTTPS) or --http-only (plain :80)"
  fi

  ensure_caddy
  write_caddyfile

  systemctl daemon-reload
  systemctl enable caddy
  # Always restart so a fresh Caddyfile is picked up.
  systemctl restart caddy

  echo
  show_status
  echo
  echo "This script only manages Caddy. Start/update the app itself with:"
  echo "  docker compose up -d --build"
  echo
  if [[ "$HTTP_ONLY" -eq 1 ]]; then
    echo "App is up: http://$(hostname -I | awk '{print $1}')/"
  else
    echo "App is up: https://${DOMAIN}/"
    echo "(DNS A records for ${DOMAIN} / www must point at this server for TLS to work.)"
    echo "Grafana: https://grafana.${DOMAIN}/"
    echo "(DNS A record for grafana.${DOMAIN} / www must also point at this server for its TLS cert.)"
    echo "Microservices stack (transitional, cutover testing): https://microservice.${DOMAIN}/"
    echo "(DNS A record for microservice.${DOMAIN} / www must also point at this server for its TLS cert.)"
    echo "mongo-express: https://mongo.${DOMAIN}/"
    echo "(DNS A record for mongo.${DOMAIN} / www must also point at this server for its TLS cert.)"
  fi
  echo "Caddy logs: journalctl -u caddy -f"
  echo "App logs:   docker compose logs -f api-ui runner peers cron-watch"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain)
      DOMAIN="${2:-}"
      [[ -n "$DOMAIN" ]] || die "--domain needs a value"
      shift 2
      ;;
    --app-port)
      APP_PORT="${2:-}"
      [[ -n "$APP_PORT" ]] || die "--app-port needs a value"
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
