#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Advertise ControlContract by session id for mas-ctl chat/serve."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any

from mas.library.standard.plugins.control.attach import ControlHost, serve_and_advertise
from mas.library.standard.plugins.control.directory import FileSessionDirectory


class ControlDirectoryHost:
    """Background RPC server keyed in a file directory by session id."""

    def __init__(self, control: Any, directory: str | Path, *, hub: Any | None = None) -> None:
        self.control = control
        self.directory = FileSessionDirectory(Path(directory))
        self._hub = hub
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._error: BaseException | None = None
        self._thread: threading.Thread | None = None
        self._server: Any = None
        self._hosted: ControlHost | None = None

    @property
    def hosted(self) -> ControlHost | None:
        return self._hosted

    def start(self, session_id: str) -> ControlHost:
        if self._thread is not None:
            return self.advertise(session_id)

        def runner() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            async def main() -> None:
                try:
                    server, hosted = await serve_and_advertise(
                        self.control,
                        self.directory,
                        session_id,
                        hub=self._hub,
                    )
                    self._server = server
                    self._hosted = hosted
                    self._ready.set()
                    while not self._stop.is_set():
                        hosted.heartbeat()
                        await asyncio.sleep(1)
                except BaseException as exc:
                    self._error = exc
                    self._ready.set()
                finally:
                    if self._hosted is not None:
                        self._hosted.unadvertise()
                    if self._server is not None:
                        await self._server.close()

            try:
                loop.run_until_complete(main())
            finally:
                loop.close()

        self._thread = threading.Thread(target=runner, daemon=True, name="mas-ctl-control-host")
        self._thread.start()
        if not self._ready.wait(timeout=5):
            self.close()
            raise RuntimeError("control directory host did not start")
        if self._error is not None:
            raise self._error
        if self._hosted is None:
            raise RuntimeError("control directory host did not advertise")
        return self._hosted

    def advertise(self, session_id: str) -> Any:
        if self._hosted is None:
            return self.start(session_id)
        return self._hosted.advertise_session(session_id)

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None
