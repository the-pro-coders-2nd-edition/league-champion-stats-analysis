"""Standalone entrypoint for the PEERS gRPC service.

Plain synchronous pattern, following RUNNER's entrypoint
(`runner/__main__.py`) exactly, NOT CRON-watch's `grpc.aio` one
(`cron_watch/__main__.py`) -- `PeersServicer` is a plain `grpc.server(...)`
servicer, because `resolve_peer_baseline`'s whole call graph is synchronous
Python (see `peers/service.py`'s module docstring, "Sync vs async").
"""

import os
from concurrent import futures

import grpc
from prometheus_client import start_http_server

from league_stats_rpc.v1 import peers_pb2_grpc
from league_stats.peers.service import PeersServicer
from league_stats.utils import get_logger, setup_logging

log = get_logger("peers")


def serve() -> None:
    setup_logging(service="peers", version=os.environ.get("GIT_COMMIT", "dev"))
    port = os.environ.get("PEERS_GRPC_PORT", "50053")
    metrics_port = int(os.environ.get("PEERS_METRICS_PORT", "9102"))
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    peers_pb2_grpc.add_PeersServiceServicer_to_server(PeersServicer(), server)
    server.add_insecure_port(f"0.0.0.0:{port}")
    server.start()
    # Minimal Prometheus /metrics HTTP surface -- same pattern as RUNNER
    # (peers_baseline_resolution_duration_seconds, peers_baseline_resolutions_total,
    # see peers/service.py).
    start_http_server(metrics_port)
    log.info(
        "PEERS gRPC service listening on :%s (metrics on :%s/metrics)", port, metrics_port
    )
    server.wait_for_termination()


def main() -> None:
    serve()


if __name__ == "__main__":
    main()
