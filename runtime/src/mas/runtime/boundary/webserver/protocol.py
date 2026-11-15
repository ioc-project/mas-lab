from __future__ import annotations

from typing import Any, Literal, Protocol, runtime_checkable


class WebServerHandle(Protocol):
    def close(self) -> None: ...


@runtime_checkable
class WebServerContract(Protocol):
    """Serve an application through a runtime-selected webserver plugin."""

    def serve_blocking(self, app: Any, *, host: str, port: int) -> None: ...

    def serve_background(self, app: Any, *, host: str, port: int) -> WebServerHandle: ...


WebServerTransport = Literal["http", "grpc"]


@runtime_checkable
class TransportValidatingWebServer(Protocol):
    """Optional startup validation for configured or advertised transports."""

    def validate_transports(self, transports: frozenset[WebServerTransport]) -> None:
        """Raise before serving resources are created if a transport is unsupported."""
        ...
