# --- Stage 1: build the Svelte SPA ---
FROM node:20-slim AS spa-builder
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- Stage 2: the Python app ---
FROM python:3.12-slim
RUN pip install --no-cache-dir uv
WORKDIR /app

# Commit sha every service tags its logs/metrics with (see `os.environ.get("GIT_COMMIT",
# "dev")` in each service's __main__.py `setup_logging(...)` call). Build with
# `--build-arg GIT_COMMIT=$(git rev-parse HEAD)` (docker-compose.yml passes this through
# from the host's GIT_COMMIT env var, defaulting to "dev" for a local/unset build) --
# without this ARG/ENV pair, GIT_COMMIT is never actually set inside the image, and every
# real deployment's `version` tag silently stays "dev" (Phase 6 final review, Finding 2).
ARG GIT_COMMIT=dev
ENV GIT_COMMIT=$GIT_COMMIT

COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/
COPY main.py ./
COPY config.toml.example ./
# Ops scripts (e.g. deploy/clear_mongodb.py) meant to be run via `docker compose exec
# api-ui uv run python deploy/<script>.py` against the running container's
# environment/network, not just from the VPS host checkout. Plain `python` (not `uv run
# python`) can't see the uv-managed venv `uv sync` installs into below, so it'll
# ModuleNotFoundError on any dependency (e.g. pymongo) despite it being declared and
# installed correctly.
COPY deploy/ ./deploy/

# vite.config.js sets build.outDir to ../src/league_stats_api_ui/spa_dist (relative to
# frontend/), so the builder stage already writes to that path under /app.
COPY --from=spa-builder /app/src/league_stats_api_ui/spa_dist/ ./src/league_stats_api_ui/spa_dist/

RUN uv sync --no-dev --frozen

EXPOSE 8000
CMD ["uv", "run", "python", "main.py", "--host", "0.0.0.0", "--port", "8000"]
