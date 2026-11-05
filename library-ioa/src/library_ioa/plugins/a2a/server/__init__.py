from __future__ import annotations

from typing import Any

from .app import build_app
from .executor import MasLabAgentExecutor


async def start_grpc_server(request_handler: Any, *, host: str, port: int) -> Any:
    """Start an SDK gRPC service around an existing A2A request handler."""
    try:
        import grpc
        from a2a.server.request_handlers.grpc_handler import GrpcHandler
        from a2a.types.a2a_pb2_grpc import add_A2AServiceServicer_to_server
        from a2a.utils.constants import VERSION_HEADER
        from a2a.utils.errors import (
            A2A_ERROR_MAPPING,
            A2A_ERROR_REASONS,
            PushNotificationNotSupportedError,
            UnsupportedOperationError,
            VersionNotSupportedError,
        )
        from a2a.utils.grpc_status import status_to_grpc
        from google.protobuf import any_pb2
        from google.rpc import error_details_pb2, status_pb2
        from packaging.version import InvalidVersion, Version
    except ImportError as exc:
        raise RuntimeError("A2A gRPC serving requires the library-ioa grpc extra") from exc

    class _A2AGrpcHandler(GrpcHandler):
        def _build_call_context(self, context: Any, request: Any) -> Any:
            server_context = super()._build_call_context(context, request)
            metadata = context.invocation_metadata() or ()
            headers = {
                str(key).lower(): value.decode("utf-8") if isinstance(value, bytes) else str(value)
                for key, value in metadata
            }
            server_context.state["headers"] = headers
            actual_version = headers.get(VERSION_HEADER.lower()) or "0.3"
            try:
                supported = Version(actual_version).major == Version("1.0").major
            except InvalidVersion:
                supported = False
            if not supported:
                raise VersionNotSupportedError(
                    message=(
                        f"A2A version '{actual_version}' is not supported by this handler. "
                        "Expected version '1.0'."
                    )
                )
            return server_context

        async def abort_context(self, error: Any, context: Any) -> None:
            unimplemented_errors = (
                PushNotificationNotSupportedError,
                UnsupportedOperationError,
                VersionNotSupportedError,
            )
            if not isinstance(error, unimplemented_errors):
                await super().abort_context(error, context)
                return

            mapping = A2A_ERROR_MAPPING[type(error)]
            error_info = error_details_pb2.ErrorInfo(
                reason=mapping.reason or A2A_ERROR_REASONS.get(type(error), "UNKNOWN_ERROR"),
                domain="a2a-protocol.org",
            )
            grpc_code = getattr(grpc.StatusCode, mapping.grpc_status, grpc.StatusCode.UNKNOWN)
            status = status_pb2.Status(
                code=grpc_code.value[0],
                message=getattr(error, "message", str(error)),
            )
            detail = any_pb2.Any()
            detail.Pack(error_info)
            status.details.append(detail)
            rich_status = status_to_grpc(status)
            trailing = list(context.trailing_metadata() or ())
            trailing.extend(rich_status.trailing_metadata)
            context.set_trailing_metadata(tuple(trailing))
            await context.abort(rich_status.code, rich_status.details)

    server = grpc.aio.server()
    add_A2AServiceServicer_to_server(_A2AGrpcHandler(request_handler), server)
    bound_port = server.add_insecure_port(f"{host}:{port}")
    if not bound_port:
        raise OSError(f"Could not bind A2A gRPC server to {host}:{port}")
    await server.start()
    return server


__all__ = ["MasLabAgentExecutor", "build_app", "start_grpc_server"]
