#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""One SLIM node connection per actor; every actor shares one event loop."""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import threading
from collections.abc import Coroutine
from typing import Any

from mas.runtime.boundary.agentcomm.protocol import AgentCommError

from library_ioa.plugins.slim.bindings import load_slim_bindings
from library_ioa.plugins.slim.endpoint import assert_endpoint_allowed, use_insecure_client_config

logger = logging.getLogger(__name__)

# Read failures in a row (after the backoff between them) before a link drops its connection and reconnects.
MAX_READ_ERRORS = 5


def _service_id(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in str(name)).strip("-")
    return cleaned or "slim-link"


class _SharedLoop:
    """The process-wide SLIM event loop.

    slim-bindings keeps one global event loop (``uniffi_set_event_loop``), so
    two loops in a process break each other: the last actor wins and the
    others fail. Every actor leases this one loop; the last release stops it.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._refs = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None

    def acquire(self) -> tuple[asyncio.AbstractEventLoop, threading.Thread]:
        with self._lock:
            if self._refs == 0:
                loop = asyncio.new_event_loop()
                ready = threading.Event()
                thread = threading.Thread(target=self._run, args=(loop, ready), name="slim-link", daemon=True)
                thread.start()
                ready.wait()
                self._loop, self._thread = loop, thread
            self._refs += 1
            assert self._loop is not None and self._thread is not None
            return self._loop, self._thread

    def release(self) -> None:
        with self._lock:
            self._refs -= 1
            if self._refs > 0:
                return
            loop, thread = self._loop, self._thread
            self._loop = self._thread = None
        if loop is None or thread is None:
            return
        loop.call_soon_threadsafe(loop.stop)
        if thread is not threading.current_thread():
            thread.join(timeout=2.0)

    @staticmethod
    def _run(loop: asyncio.AbstractEventLoop, ready: threading.Event) -> None:
        asyncio.set_event_loop(loop)
        ready.set()
        try:
            loop.run_forever()
        finally:
            tasks = [task for task in asyncio.all_tasks(loop) if not task.done()]
            for task in tasks:
                task.cancel()
            if tasks:
                loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
            loop.close()


_SHARED_LOOP = _SharedLoop()


class ConnectionActor:
    """Owns one node connection on the shared SLIM loop."""

    # Time for a node to propagate new subscriptions before the first send.
    SUBSCRIBE_SETTLE_S = 0.2
    # Same, for a route to a peer before the first session to it.
    ROUTE_SETTLE_S = 0.2

    def __init__(
        self,
        *,
        endpoint: str,
        dev: bool = False,
        timeout_s: float = 2.0,
        reply_timeout_s: float = 120.0,
        name: str = "slim-link",
    ) -> None:
        self.endpoint = str(endpoint)
        self.dev = bool(dev)
        self.timeout_s = float(timeout_s)
        # How long a send waits for the peer's reply: a peer may run a whole model turn.
        self.reply_timeout_s = float(reply_timeout_s)
        self._name = str(name)
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._leased = False
        self._sb: Any = None
        self._config: Any = None
        self._connections: list[tuple[Any, Any]] = []
        self._instances: dict[str, str] = {}
        self._agents: dict[str, str] = {}
        self.failed = False
        self._stopping = False
        self._tasks: set[asyncio.Task[Any]] = set()

    def __repr__(self) -> str:
        return f"ConnectionActor(endpoint={self.endpoint!r})"

    @property
    def thread(self) -> threading.Thread | None:
        return self._thread

    @property
    def running(self) -> bool:
        """True once ``start`` leased the loop and until ``stop``."""
        return self._loop is not None

    @property
    def bindings(self) -> Any:
        return self._sb

    def start(self) -> None:
        if self.failed:
            raise AgentCommError("slim link is failed")
        if self._loop is not None:
            return
        self._stopping = False
        loop, thread = _SHARED_LOOP.acquire()
        self._leased = True
        self._loop, self._thread = loop, thread
        try:
            asyncio.run_coroutine_threadsafe(self._aconnect(), loop).result(timeout=max(self.timeout_s * 2, 0.1))
        except concurrent.futures.TimeoutError:
            self._abort_start()
            raise AgentCommError("slim link did not start") from None
        except (AttributeError, TypeError):
            self._abort_start()
            raise
        except Exception as exc:
            self._abort_start()
            raise AgentCommError(str(exc) or "slim link start failed") from exc

    def _abort_start(self) -> None:
        self.failed = True
        self._release()

    def _release(self) -> None:
        self._loop = None
        self._thread = None
        if self._leased:
            self._leased = False
            _SHARED_LOOP.release()

    def submit(self, coro: Coroutine[Any, Any, Any], *, timeout: float | None = None) -> Any:
        if self.failed:
            raise AgentCommError("slim link is failed")
        loop = self._loop
        if self._stopping or loop is None:
            raise AgentCommError("slim link is stopped")
        if threading.current_thread() is self._thread:
            raise AgentCommError("cannot wait on the SLIM loop thread")
        future = asyncio.run_coroutine_threadsafe(self._guard(coro), loop)
        wait = timeout if timeout is not None else max(self.timeout_s * 4, 0.1)
        try:
            return future.result(timeout=wait)
        except concurrent.futures.TimeoutError:
            future.cancel()  # a call nobody waits for any more must not keep running on the loop
            raise AgentCommError("slim link call timed out") from None
        except AgentCommError:
            raise
        except (asyncio.CancelledError, concurrent.futures.CancelledError):
            raise AgentCommError("slim link is stopped") from None
        except Exception as exc:
            raise AgentCommError(str(exc) or "slim binding error") from exc

    async def asubmit(self, coro: Coroutine[Any, Any, Any], *, timeout: float) -> Any:
        """``submit`` for a caller that awaits: run ``coro`` on the SLIM loop and wait at most ``timeout`` seconds.

        A call that times out or whose caller is cancelled is cancelled on the loop too.
        """
        if self.failed:
            coro.close()
            raise AgentCommError("slim link is failed")
        loop = self._loop
        if self._stopping or loop is None:
            coro.close()
            raise AgentCommError("slim link is stopped")
        try:
            if threading.current_thread() is self._thread:
                return await asyncio.wait_for(self._guard(coro), timeout)
            future = asyncio.run_coroutine_threadsafe(self._guard(coro), loop)
            return await asyncio.wait_for(asyncio.wrap_future(future), timeout)
        except (asyncio.TimeoutError, concurrent.futures.TimeoutError):
            raise AgentCommError("slim link call timed out") from None
        except concurrent.futures.CancelledError:
            raise AgentCommError("slim link is stopped") from None

    def spawn(self, coro: Coroutine[Any, Any, Any]) -> None:
        loop = self._loop
        if loop is None or self._stopping:
            return

        def _create() -> None:
            task = loop.create_task(coro)
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

        if threading.current_thread() is self._thread:
            _create()
            return
        loop.call_soon_threadsafe(_create)

    def register_instance(self, agent_id: str, instance_id: str) -> None:
        key = str(agent_id)
        ident = str(instance_id)
        self._instances[key] = ident
        self._agents[ident] = key

    def register_source(self, source: str, agent_id: str) -> None:
        key = str(source)
        if key and key != "NULL_COMPONENT":
            self._agents[key] = str(agent_id)

    def instance_of(self, agent_id: str) -> str:
        return self._instances.get(str(agent_id), "")

    def agent_of(self, instance_id: str) -> str:
        return self._agents.get(str(instance_id), "")

    async def open_connection(self, name: str) -> tuple[Any, Any]:
        sb = self._sb
        config = self._config
        if sb is None or config is None:
            raise AgentCommError("slim link is not connected")
        service = sb.Service(_service_id(name))
        conn_id = await service.connect_async(config)
        self._connections.append((service, conn_id))
        return service, conn_id

    def close_connection(self, service: Any, conn_id: Any) -> None:
        """Disconnect one connection opened by ``open_connection`` (a link that failed to start)."""
        try:
            self._connections.remove((service, conn_id))
        except ValueError:
            return
        try:
            service.disconnect(conn_id)
        except Exception:
            logger.exception("slim link disconnect")

    def mark_failed(self) -> None:
        self.failed = True

    def stop(self) -> None:
        self._stopping = True
        loop = self._loop
        if loop is None:
            return
        if threading.current_thread() is self._thread:
            loop.create_task(self._ashutdown())
            return
        try:
            asyncio.run_coroutine_threadsafe(self._ashutdown(), loop).result(timeout=max(self.timeout_s * 2, 0.1))
        except Exception:
            logger.exception("slim link stop")
        self._release()

    async def _aconnect(self) -> None:
        assert_endpoint_allowed(self.endpoint, dev=self.dev)
        sb = load_slim_bindings()
        self._sb = sb
        sb.uniffi_set_event_loop(asyncio.get_running_loop())
        if not sb.is_initialized():
            sb.initialize_with_defaults()
        if use_insecure_client_config(self.endpoint, dev=self.dev):
            self._config = sb.new_insecure_client_config(self.endpoint)
        else:
            self._config = sb.new_secure_client_config(self.endpoint)

    async def _guard(self, coro: Coroutine[Any, Any, Any]) -> Any:
        task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        try:
            return await coro
        except AgentCommError:
            raise
        except asyncio.CancelledError:
            raise
        except (AttributeError, TypeError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            raise AgentCommError(str(exc) or "slim binding error") from exc

    async def _ashutdown(self) -> None:
        self._stopping = True
        connections = list(self._connections)
        self._connections.clear()
        self._instances.clear()
        self._agents.clear()
        for service, conn_id in connections:
            try:
                service.disconnect(conn_id)
            except Exception:
                logger.exception("slim link disconnect")
        current = asyncio.current_task()
        tasks = [task for task in self._tasks if task is not current]
        for task in tasks:
            task.cancel()
        if tasks:
            try:
                await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=0.5)
            except asyncio.TimeoutError:
                pass
        if threading.current_thread() is self._thread:
            self._release()
