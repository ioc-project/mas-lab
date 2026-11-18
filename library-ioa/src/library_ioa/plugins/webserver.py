from __future__ import annotations

from dataclasses import dataclass
from threading import Thread
from typing import Any

from mas.runtime.aio import new_thread


@dataclass
class UvicornHandle:
    server: Any
    thread: Thread

    def close(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=5)


class UvicornWebServer:
    """Generic ASGI webserver plugin backed by Uvicorn."""

    def serve_blocking(self, app: Any, *, host: str, port: int) -> None:
        import uvicorn

        uvicorn.run(app, host=host, port=port)

    async def serve_async(self, app: Any, *, host: str, port: int) -> None:
        import uvicorn

        server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning"))
        await server.serve()

    def serve_background(self, app: Any, *, host: str, port: int) -> UvicornHandle:
        import uvicorn

        server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning"))
        server.install_signal_handlers = False
        thread = new_thread(target=server.run, daemon=True)
        thread.start()
        return UvicornHandle(server=server, thread=thread)
