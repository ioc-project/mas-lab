#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""A2A gRPC transport extra. Optional extra ``a2a-grpc`` of mas-lab-library-ioa."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from mas._dist import extra_spec

A2A_GRPC_EXTRA = "a2a-grpc"
_A2A_GRPC_PROTOCOL = "a2a+grpc"
_TRANSPORT_REASON = "the A2A gRPC transport"


def a2a_grpc_needed_message(reason: str = _TRANSPORT_REASON) -> str:
    """Name the setting that asked for gRPC and the extra that provides it."""
    spec = extra_spec(A2A_GRPC_EXTRA, file=__file__)
    return f"{reason} needs pip install '{spec}'"


def a2a_plugin_type(protocol: str) -> str:
    """Registry type for an Application protocol. ``a2a+grpc`` is the A2A plugin."""
    text = str(protocol or "").strip()
    if text.lower() == _A2A_GRPC_PROTOCOL:
        return "a2a"
    return text


class A2AGrpcExtraError(RuntimeError):
    """The row asked for A2A over gRPC and the extra is not installed."""

    def __init__(self, reason: str = _TRANSPORT_REASON) -> None:
        super().__init__(a2a_grpc_needed_message(reason))


def grpc_is_importable() -> bool:
    try:
        _import_a2a_grpc_stack()
    except ImportError:
        return False
    return True


def a2a_row_grpc_reason(row: Any) -> str | None:
    """The setting of an Application endpoint that asks for A2A gRPC, or None."""
    if not isinstance(row, dict):
        return None
    protocol = str(row.get("protocol") or "").strip().lower()
    a2a = row.get("a2a") if isinstance(row.get("a2a"), dict) else {}
    transport = str(row.get("transport") or a2a.get("transport") or "").strip().upper()
    if protocol == _A2A_GRPC_PROTOCOL:
        return f"protocol: {_A2A_GRPC_PROTOCOL}"
    if protocol != "a2a":
        return None
    if transport == "GRPC":
        return "transport: GRPC"
    if a2a.get("grpc_port") is not None:
        return "a2a.grpc_port"
    if a2a.get("grpc_url"):
        return "a2a.grpc_url"
    return None


def a2a_row_needs_grpc(row: Any) -> bool:
    """True when an Application endpoint asks for the A2A gRPC transport."""
    return a2a_row_grpc_reason(row) is not None


def require_a2a_grpc(reason: str = _TRANSPORT_REASON) -> None:
    """Import the A2A gRPC stack or raise the extra-naming error."""
    load_a2a_grpc(reason)


def _import_a2a_grpc_stack() -> Any:
    """Import the stack ``start_grpc_server`` needs. ``grpcio`` alone is not the extra."""
    import grpc
    from a2a.server.request_handlers.grpc_handler import GrpcHandler  # noqa: F401
    from a2a.types.a2a_pb2_grpc import add_A2AServiceServicer_to_server  # noqa: F401
    from google.rpc import error_details_pb2, status_pb2  # noqa: F401

    return grpc


def load_a2a_grpc(reason: str = _TRANSPORT_REASON) -> Any:
    """The ``grpc`` module after the A2A gRPC stack imports, or the extra-naming error."""
    try:
        return _import_a2a_grpc_stack()
    except ImportError as exc:
        raise A2AGrpcExtraError(reason) from exc


def require_a2a_grpc_rows(rows: Iterable[tuple[str, Any]]) -> None:
    """Fail at plan time when any row asks for A2A gRPC and the extra is missing."""
    for _path, row in rows:
        reason = a2a_row_grpc_reason(row)
        if reason is not None:
            require_a2a_grpc(reason)
            return


def collect_a2a_grpc_issues(data: dict[str, Any] | None) -> list[tuple[str, str]]:
    """``(path, message)`` for each endpoint that needs the extra and cannot import the A2A gRPC stack."""
    if grpc_is_importable():
        return []
    issues: list[tuple[str, str]] = []
    for path, row in _endpoint_rows(data, ""):
        reason = a2a_row_grpc_reason(row)
        if reason is not None:
            issues.append((path, a2a_grpc_needed_message(f"{path}: {reason}")))
    return issues


def _endpoint_rows(node: Any, path: str) -> list[tuple[str, dict[str, Any]]]:
    rows: list[tuple[str, dict[str, Any]]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}" if path else str(key)
            if key == "endpoints" and isinstance(value, dict):
                rows.extend((f"{child}.{name}", row) for name, row in value.items() if isinstance(row, dict))
            else:
                rows.extend(_endpoint_rows(value, child))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            rows.extend(_endpoint_rows(item, f"{path}[{index}]"))
    return rows
