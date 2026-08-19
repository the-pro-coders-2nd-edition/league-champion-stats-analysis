#!/usr/bin/env bash
# Regenerate the gRPC Python stubs from protos/ into src/league_stats_rpc/.
# Run this after editing any .proto file.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"

uv run python -m grpc_tools.protoc \
  -I protos \
  --python_out=src \
  --grpc_python_out=src \
  --pyi_out=src \
  protos/league_stats_rpc/v1/common.proto \
  protos/league_stats_rpc/v1/cron_watch.proto \
  protos/league_stats_rpc/v1/runner.proto \
  protos/league_stats_rpc/v1/peers.proto

echo "→ regenerated stubs in src/league_stats_rpc/v1/"
