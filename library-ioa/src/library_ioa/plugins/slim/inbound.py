#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Inbound listen loop and request handler for a SLIM link."""

from __future__ import annotations

import asyncio
import contextvars
import datetime
import json
import logging
from typing import Any

from mas.library.standard.lib.comm.headers import bound_headers
from mas.library.standard.lib.stack.agent_comm import inbound_envelope
from mas.library.standard.lib.stack.errors import StackError
from mas.runtime.boundary.agentcomm.protocol import AgentCommError, bound_inbound

from library_ioa.plugins.slim.actor import MAX_READ_ERRORS

logger = logging.getLogger(__name__)
# What a peer sees when a request or a reply could not be handled. It never carries the failure's text.
HANDLER_ERROR = "peer handler failed"
# A connect-only link: this process does not serve the agent, so a request that reaches it is refused.
NOT_SERVED = "the agent is not served on this link"
# A frame is the task plus its envelope (headers, ids); the task alone is capped by max_message_size.
ENVELOPE_SLACK = 64 * 1024
# Inbound requests a link serves at once. A reader that has this many in flight stops reading, so a peer
# that sends faster than the agent answers waits on the node instead of filling this process.
MAX_INFLIGHT_FRAMES = 32
# The link whose handler is running on this thread; ``stop()`` from that handler waits for the reply to go out.
HANDLING_LINK: contextvars.ContextVar[Any] = contextvars.ContextVar("slim_handling_link", default=None)


class SlimInbound:
    """Listen, drain, and dispatch inbound SLIM frames onto the link."""

    def _on_inbound(self, msg: Any) -> bytes | None:
        token = HANDLING_LINK.set(self)
        try:
            return self._dispatch_inbound(msg)
        finally:
            HANDLING_LINK.reset(token)

    def _dispatch_inbound(self, msg: Any) -> bytes | None:
        raw = msg.payload if isinstance(msg.payload, (bytes, bytearray)) else str(msg.payload).encode("utf-8")
        if len(raw) > self.max_message_size + ENVELOPE_SLACK:
            logger.warning("slim inbound frame of %d bytes exceeds the size limit", len(raw))
            return json.dumps({"kind": "error", "error": HANDLER_ERROR}).encode("utf-8")
        try:
            envelope = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            logger.exception("slim inbound frame is not JSON")
            return json.dumps({"kind": "error", "error": HANDLER_ERROR}).encode("utf-8")
        if not isinstance(envelope, dict):
            logger.warning("slim inbound frame is not an object")
            return json.dumps({"kind": "error", "error": HANDLER_ERROR}).encode("utf-8")
        sender = _sender_id(msg.context.source_name, self._actor)
        headers = dict(envelope.get("headers") or {})
        kind = str(envelope.get("kind") or "message")
        if kind == "request":
            intent = str(envelope.get("intent") or "task")
            try:
                with bound_headers(headers), bound_inbound(sender=sender, intent=intent):
                    request = {
                        "task": str(envelope.get("task") or ""),
                        "sender": sender,
                        "correlation_id": int(envelope.get("correlation_id") or 0),
                        "caller_call_id": str(envelope.get("caller_call_id") or ""),
                        "context_id": str(envelope.get("context_id") or ""),
                    }
                    handler = self._serve_handler
                    if handler is None and self._run_turn is None:
                        # a link this session only sends on (connect): its agent is not served here
                        logger.warning("slim request for %s refused: %s", self.name, NOT_SERVED)
                        return json.dumps({"kind": "error", "error": NOT_SERVED}).encode("utf-8")
                    if handler is None:
                        reply = self._handle_request(**request)
                    else:  # a layer above decodes the request (a stack's inbound chain)
                        reply = str(handler(inbound_envelope(**request, intent=intent, headers=headers)))
                return json.dumps({"kind": "reply", "task": reply}).encode("utf-8")
            except StackError as exc:
                if "no turn to serve" in str(exc):
                    return json.dumps({"kind": "error", "error": NOT_SERVED}).encode("utf-8")
                logger.exception("slim inbound handler failed")
                return json.dumps({"kind": "error", "error": HANDLER_ERROR}).encode("utf-8")
            except Exception:
                logger.exception("slim inbound handler failed")
                return json.dumps({"kind": "error", "error": HANDLER_ERROR}).encode("utf-8")
        to = envelope.get("to")
        if to and str(to) not in {self.agent_id, self.name} and self._full(str(to)) != self.name:
            return None
        message = {
            "sender": sender,
            "channel": envelope.get("channel") or "",
            "kind": kind,
            "payload": envelope.get("payload") or "",
            "headers": headers,
            "to": envelope.get("to"),
        }
        with bound_headers(headers):
            for cb in list(self._subscribers):
                cb(message)
        return None

    def _handle_request(
        self,
        task: str,
        *,
        sender: str,
        correlation_id: int = 0,
        caller_call_id: str = "",
        context_id: str = "",
    ) -> str:
        del sender
        return self._run_turn(self.local_agent_id, task, correlation_id, caller_call_id, context_id)

    async def _aadopt_pending(self) -> None:
        while True:
            try:
                session = await self._call(self._app.listen_for_session_async(timeout=datetime.timedelta(seconds=0)))
            except AgentCommError:
                return
            self._adopt_session(session)

    def _adopt_session(self, session: Any) -> None:
        if session is None:
            return
        channel = session.metadata().get("channel")
        if channel:
            self._groups.setdefault(str(channel), session)

    async def _inbound_loop(self) -> None:
        timeout = self._timeout
        try:
            while not self._stopped:
                if self._need_reconnect or self._app is None:
                    try:
                        await self._areconnect()
                    except Exception:
                        logger.exception("slim reconnect failed")
                        await _async_sleep(_backoff_s(self.timeout_s, self._read_errors or 1))
                    continue
                try:
                    session = await self._call(self._app.listen_for_session_async(timeout=timeout))
                except AgentCommError as exc:
                    if self._stopped:
                        return
                    if self._need_reconnect or self._app is None:
                        continue
                    if _is_session_gone(exc):
                        return
                    if _is_idle_timeout(exc, self._sb):
                        self._read_errors = 0
                        continue
                    self._note_read_error()
                    logger.exception("slim listen failed")
                    await _async_sleep(_backoff_s(self.timeout_s, self._read_errors))
                    continue
                except asyncio.CancelledError:
                    return
                self._read_errors = 0
                if session.is_initiator():
                    continue
                self._adopt_session(session)
                reader = asyncio.create_task(self._drain_session(session))
                self._readers.append(reader)
                reader.add_done_callback(self._forget_reader)
        finally:
            self._listening = False
            readers = list(self._readers)
            self._readers.clear()
            for task in readers:
                task.cancel()
            if readers:
                await asyncio.gather(*readers, return_exceptions=True)

    async def _drain_session(self, session: Any) -> None:
        timeout = self._timeout
        lock = asyncio.Lock()
        while not self._stopped:
            try:
                msg = await self._call(session.get_message_async(timeout=timeout))
            except AgentCommError as exc:
                if self._stopped or _is_session_gone(exc):
                    return
                if _is_idle_timeout(exc, self._sb):
                    self._read_errors = 0
                    continue
                self._note_read_error()
                logger.exception("slim inbound read failed")
                if self._need_reconnect:
                    return
                await _async_sleep(_backoff_s(self.timeout_s, self._read_errors))
                continue
            except asyncio.CancelledError:
                return
            self._read_errors = 0
            # A handler runs a whole turn and may send to another agent, so each frame is served by its own
            # task and a worker thread: on this loop a handler would freeze every agent of the process and
            # could not wait for a reply. The lock keeps one session's frames in order.
            await self._inflight.acquire()
            frame = asyncio.create_task(self._serve_frame(session, msg, lock))
            self._frames.add(frame)
            frame.add_done_callback(self._frames.discard)
            frame.add_done_callback(lambda _task: self._inflight.release())

    def _forget_reader(self, task: asyncio.Task[None]) -> None:
        """A finished session's reader is dropped, so a long-lived link does not keep one per session."""
        try:
            self._readers.remove(task)
        except ValueError:
            pass

    async def _serve_frame(self, session: Any, msg: Any, lock: asyncio.Lock) -> None:
        async with lock:
            try:
                reply = await asyncio.get_running_loop().run_in_executor(None, self._on_inbound, msg)
            except Exception:
                logger.exception("slim inbound frame failed")
                reply = json.dumps({"kind": "error", "error": HANDLER_ERROR}).encode("utf-8")
            if reply is not None:
                try:
                    await self._wait_handle(session.publish_to_async(msg.context, reply, None, None))
                except AgentCommError:
                    logger.exception("slim inbound reply failed")
        if self._stop_deferred:  # the handler asked to stop this link: do it once its reply is out
            self._stop_deferred = False
            await self._astop()

    def _note_read_error(self) -> None:
        self._read_errors += 1
        if self._read_errors < MAX_READ_ERRORS:
            return
        self._need_reconnect = True
        logger.warning("slim dropping the connection after %s read failures", MAX_READ_ERRORS)
        service, conn_id = self._service, self._conn_id
        if service is None:
            return
        self._service = None
        self._conn_id = None
        self._actor.close_connection(service, conn_id)

    def _start_listen_thread(self) -> None:
        if self._listening:
            return
        self._listening = True
        self._actor.spawn(self._inbound_loop())


async def _async_sleep(delay: float) -> None:
    await asyncio.sleep(delay)


def _backoff_s(timeout_s: float, errors: int = 1) -> float:
    cap = max(0.05, min(float(timeout_s), 0.25))
    return min(cap, 0.05 * (2 ** max(int(errors) - 1, 0)))


def _sender_id(source: Any, actor: Any) -> str:
    """The sending agent of a frame: its instance id (``Name.id()``), else the last part of its name."""
    ident = str(source.id() or "")
    if ident in {"", "NULL_COMPONENT"}:
        ident = str(source or "").rsplit("/", 1)[-1]
    return actor.agent_of(ident) or ident


# What the bindings say when a read found nothing before its deadline (slim-bindings 2.2: a ReceiveError).
IDLE_READ_MESSAGES = ("listen_for_session timed out", "receive timeout")


def _is_idle_timeout(exc: BaseException, sb: Any) -> bool:
    """True when a read only found nothing in time: the binding's ``Timeout``, or one of its idle-read messages.

    Any other failure, a connect or handshake timeout included, counts toward ``MAX_READ_ERRORS``.
    """
    cause = exc.__cause__
    if cause is not None and isinstance(cause, sb.SlimError.Timeout):
        return True
    text = str(cause if cause is not None else exc).lower()
    return any(message in text for message in IDLE_READ_MESSAGES)


def _is_session_gone(exc: BaseException) -> bool:
    text = str(exc).lower()
    cause = exc.__cause__
    cause_text = str(cause).lower() if cause is not None else ""
    return "session closed" in text or "session closed" in cause_text
