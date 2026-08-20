"""gRPC trace_id propagation: client interceptors attach it, server interceptors extract/mint it.

This codebase has both a synchronous gRPC server pattern (`grpc.server(...)`,
used by RUNNER and PEERS) and an async one (`grpc.aio.server()`, used by
CronWatch) -- see `runner/service.py`'s and `cron_watch/service.py`'s module
docstrings for why. Both need an interceptor implementation here, and they
are NOT interchangeable:

- Sync (`TraceServerInterceptor`): `grpc.ServerInterceptor.intercept_service`
  gives no guarantee it runs on the same thread as the eventual RPC handler
  invocation (the handler runs on one of `grpc.server`'s
  `ThreadPoolExecutor` workers). Setting the trace_id ContextVar directly in
  `intercept_service` would therefore not reliably reach the handler's
  thread. Instead this wraps the handler's *behavior* function so
  `set_trace_id` runs on the exact thread that executes the RPC method.
- Async (`AsyncTraceServerInterceptor`): `grpc.aio.ServerInterceptor`'s own
  contract documents that the interceptor and the final handler call share
  the same `contextvars.Context` -- so setting the ContextVar directly
  before `continuation(...)` is correct and simpler.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Iterable, Iterator
from typing import Any

import grpc
import grpc.aio

from league_stats_common.utils import current_trace_id, set_trace_id

TRACE_METADATA_KEY = "trace-id"


def _trace_id_from_metadata(metadata: Iterable[tuple[str, str]] | None) -> str:
    """Return the `trace-id` value from gRPC call metadata, or `""` if absent."""
    if not metadata:
        return ""
    for key, value in metadata:
        if key == TRACE_METADATA_KEY:
            return value
    return ""


def _with_trace_id_metadata(
    metadata: Iterable[tuple[str, str]] | None,
) -> tuple[tuple[str, str], ...]:
    """Append the current trace_id to outgoing metadata."""
    existing = tuple(metadata) if metadata else ()
    return existing + ((TRACE_METADATA_KEY, current_trace_id()),)


def _incoming_or_minted_trace_id(handler_call_details: grpc.HandlerCallDetails) -> str:
    """Read `trace-id` from incoming metadata, minting a fresh one if this call has none.

    A call with no upstream trace_id is originating a new trace (e.g. CronWatch
    detecting a new game with nothing upstream) -- it must never propagate an
    empty string.
    """
    return (
        _trace_id_from_metadata(handler_call_details.invocation_metadata)
        or uuid.uuid4().hex
    )


class TraceClientInterceptor(
    grpc.UnaryUnaryClientInterceptor,
    grpc.UnaryStreamClientInterceptor,
):
    """Attaches the current trace_id to every outgoing sync gRPC call's metadata."""

    def intercept_unary_unary(self, continuation, client_call_details, request):
        new_details = client_call_details._replace(
            metadata=_with_trace_id_metadata(client_call_details.metadata)
        )
        return continuation(new_details, request)

    def intercept_unary_stream(self, continuation, client_call_details, request):
        new_details = client_call_details._replace(
            metadata=_with_trace_id_metadata(client_call_details.metadata)
        )
        return continuation(new_details, request)


class AsyncTraceClientInterceptor(
    grpc.aio.UnaryUnaryClientInterceptor,
    grpc.aio.UnaryStreamClientInterceptor,
):
    """Attaches the current trace_id to every outgoing async gRPC call's metadata."""

    async def intercept_unary_unary(self, continuation, client_call_details, request):
        new_details = grpc.aio.ClientCallDetails(
            method=client_call_details.method,
            timeout=client_call_details.timeout,
            metadata=_with_trace_id_metadata(client_call_details.metadata),
            credentials=client_call_details.credentials,
            wait_for_ready=client_call_details.wait_for_ready,
        )
        return await continuation(new_details, request)

    async def intercept_unary_stream(self, continuation, client_call_details, request):
        new_details = grpc.aio.ClientCallDetails(
            method=client_call_details.method,
            timeout=client_call_details.timeout,
            metadata=_with_trace_id_metadata(client_call_details.metadata),
            credentials=client_call_details.credentials,
            wait_for_ready=client_call_details.wait_for_ready,
        )
        return await continuation(new_details, request)


def _wrap_sync_handler(
    handler: grpc.RpcMethodHandler, trace_id: str
) -> grpc.RpcMethodHandler:
    """Wrap a sync `RpcMethodHandler`'s behavior so it runs `set_trace_id` on its own thread."""
    if handler.unary_unary is not None:
        inner = handler.unary_unary

        def unary_unary_behavior(request: Any, context: grpc.ServicerContext) -> Any:
            set_trace_id(trace_id)
            return inner(request, context)

        return grpc.unary_unary_rpc_method_handler(
            unary_unary_behavior,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )

    if handler.unary_stream is not None:
        inner_stream = handler.unary_stream

        def unary_stream_behavior(
            request: Any, context: grpc.ServicerContext
        ) -> Iterator[Any]:
            set_trace_id(trace_id)
            yield from inner_stream(request, context)

        return grpc.unary_stream_rpc_method_handler(
            unary_stream_behavior,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )

    if handler.stream_unary is not None:
        inner_su = handler.stream_unary

        def stream_unary_behavior(
            request_iterator: Iterator[Any], context: grpc.ServicerContext
        ) -> Any:
            set_trace_id(trace_id)
            return inner_su(request_iterator, context)

        return grpc.stream_unary_rpc_method_handler(
            stream_unary_behavior,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )

    if handler.stream_stream is not None:
        inner_ss = handler.stream_stream

        def stream_stream_behavior(
            request_iterator: Iterator[Any], context: grpc.ServicerContext
        ) -> Iterator[Any]:
            set_trace_id(trace_id)
            yield from inner_ss(request_iterator, context)

        return grpc.stream_stream_rpc_method_handler(
            stream_stream_behavior,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )

    return handler


class TraceServerInterceptor(grpc.ServerInterceptor):
    """Extracts (or mints) trace_id from incoming metadata before the sync handler runs."""

    def intercept_service(
        self,
        continuation: Callable[[grpc.HandlerCallDetails], grpc.RpcMethodHandler],
        handler_call_details: grpc.HandlerCallDetails,
    ) -> grpc.RpcMethodHandler | None:
        handler = continuation(handler_call_details)
        if handler is None:
            return None
        trace_id = _incoming_or_minted_trace_id(handler_call_details)
        return _wrap_sync_handler(handler, trace_id)


class AsyncTraceServerInterceptor(grpc.aio.ServerInterceptor):
    """Extracts (or mints) trace_id from incoming metadata before the async handler runs.

    Safe to set the ContextVar directly here (rather than wrapping the handler,
    as the sync variant must): `grpc.aio.ServerInterceptor`'s own docs guarantee
    the interceptor and the eventual handler call share one `contextvars.Context`.
    """

    async def intercept_service(
        self,
        continuation: Callable[
            [grpc.HandlerCallDetails], Awaitable[grpc.RpcMethodHandler]
        ],
        handler_call_details: grpc.HandlerCallDetails,
    ) -> grpc.RpcMethodHandler:
        set_trace_id(_incoming_or_minted_trace_id(handler_call_details))
        return await continuation(handler_call_details)
