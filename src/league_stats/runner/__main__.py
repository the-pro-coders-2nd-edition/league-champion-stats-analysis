"""Standalone entrypoint for the RUNNER gRPC service."""

import os
from concurrent import futures

import grpc
from prometheus_client import start_http_server

from league_stats_rpc.v1 import runner_pb2_grpc
from league_stats.infra.trace_context import TraceServerInterceptor
from league_stats.runner.service import RunnerServicer
from league_stats.utils import get_logger, setup_logging

log = get_logger("runner")


def serve() -> None:
    setup_logging(service="runner", version=os.environ.get("GIT_COMMIT", "dev"))
    port = os.environ.get("RUNNER_GRPC_PORT", "50051")
    metrics_port = int(os.environ.get("RUNNER_METRICS_PORT", "9100"))
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=10),
        interceptors=[TraceServerInterceptor()],
    )
    runner_pb2_grpc.add_RunnerServiceServicer_to_server(RunnerServicer(), server)
    server.add_insecure_port(f"0.0.0.0:{port}")
    server.start()
    # Minimal Prometheus /metrics HTTP surface -- first of RUNNER's metrics
    # (runner_job_duration_seconds, runner_jobs_total, see runner/service.py).
    start_http_server(metrics_port)
    log.info(
        "RUNNER gRPC service listening on :%s (metrics on :%s/metrics)", port, metrics_port
    )
    server.wait_for_termination()


def main() -> None:
    serve()


if __name__ == "__main__":
    main()
