#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Typed A2A hooks: client interceptor and server handler wrappers."""

from __future__ import annotations

import json
import logging
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Mapping
from urllib.parse import urlsplit

from mas.runtime.schema.transport import (
    DISCOVERY,
    ERROR,
    MESSAGE_COMPLETED,
    MESSAGE_RECEIVED,
    MESSAGE_SENT,
    SOURCE_HOOK,
    TransportEvent,
)
from mas.runtime.transport.emit import emit_transport, protocol_config

PROTOCOL = "a2a"
OBSERVE_KEY = "observe"
_logger = logging.getLogger(__name__)


def _cfg():
    return protocol_config(PROTOCOL)


def correlation_key(*, context_id: str = "", task_id: str = "", message_id: str = "") -> str:
    return "|".join((context_id, task_id, message_id))


def _emit(
    kind: str,
    *,
    side: str,
    key: str,
    source: str = "",
    target: str = "",
    **attrs: Any,
) -> None:
    if not _cfg().hooks:
        return
    attributes = {k: v for k, v in attrs.items() if v not in (None, "")}
    if source:
        attributes["source"] = source
    if target:
        attributes["target"] = target
    try:
        emit_transport(
            TransportEvent(
                kind=kind,
                protocol=PROTOCOL,
                side=side,
                correlation_key=key or "a2a",
                source=SOURCE_HOOK,
                timestamp=time.time(),
                session_id=str(attributes.pop("session_id", "") or ""),
                agent_id=str(attributes.pop("agent_id", "") or source or target),
                call_id=str(attributes.pop("call_id", "") or ""),
                run_id=str(attributes.pop("run_id", "") or ""),
                mas_id=str(attributes.pop("mas_id", "") or ""),
                attributes=attributes,
            )
        )
    except Exception:
        _logger.debug("a2a transport emit failed", exc_info=True)


_MAX_RAW = 4096
_MAX_NAME = 256
_MAX_TRACEPARENT = 128
_STATE_KEY = "mas.transport"
_TERMINAL_TASK_STATES = frozenset(
    {"TASK_STATE_COMPLETED", "TASK_STATE_FAILED", "TASK_STATE_CANCELED", "TASK_STATE_REJECTED"}
)


_identity: ContextVar[tuple[str, str, str]] = ContextVar("mas_a2a_transport_identity", default=("", "", ""))


@contextmanager
def bound_identity(source: str, target: str, session_id: str = ""):
    """Who is sending to whom for the calls made inside this block (this task only)."""
    token = _identity.set((str(source or ""), str(target or ""), str(session_id or "")))
    try:
        yield
    finally:
        _identity.reset(token)


def current_identity() -> tuple[str, str, str]:
    return _identity.get()


def peer_label(url: str) -> str:
    """Scheme, host and port of a peer url. Userinfo, path and query never reach an event."""
    try:
        parts = urlsplit(str(url or ""))
    except ValueError:
        return ""
    if not parts.hostname:
        return ""
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{parts.hostname}{port}" if parts.scheme else f"{parts.hostname}{port}"


def observe_metadata(
    *,
    session_id: str = "",
    agent_id: str = "",
    sequence: int = 0,
    traceparent: str = "",
) -> dict[str, str]:
    """Wire metadata the peer recovers. Empty when ``propagate_metadata`` is off or there is nothing to say."""
    if not _cfg().propagate_metadata:
        return {}
    payload = {
        "session_id": session_id,
        "last_agent_name": agent_id,
        "agent_sequence": sequence,
        "traceparent": traceparent,
    }
    kept = {k: v for k, v in payload.items() if v not in ("", 0, None)}
    return {OBSERVE_KEY: json.dumps(kept)} if kept else {}


def _as_mapping(value: Any) -> dict[str, Any]:
    """Dict from JSON metadata, a protobuf Struct, or nothing."""
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    items = getattr(value, "items", None)
    if callable(items):
        try:
            return {str(key): val for key, val in items()}
        except Exception:
            pass
    if getattr(value, "DESCRIPTOR", None) is not None:
        try:
            from google.protobuf.json_format import MessageToDict

            converted = MessageToDict(value)
            return dict(converted) if isinstance(converted, Mapping) else {}
        except Exception:
            return {}
    return {}


def _bounded_text(value: Any, limit: int) -> str:
    return value[:limit] if isinstance(value, str) else ""


def read_observe_metadata(metadata: Any) -> dict[str, Any]:
    """What a peer put under ``observe``, reduced to known keys, string typed and size bounded.

    The peer is not trusted: unknown keys, nested values and oversized text are dropped.
    """
    mapping = _as_mapping(metadata)
    if not mapping:
        return {}
    raw = mapping.get(OBSERVE_KEY)
    if isinstance(raw, str):
        if len(raw) > _MAX_RAW:
            return {}
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, RecursionError):
            return {}
    if not isinstance(raw, Mapping):
        return {}
    out: dict[str, Any] = {}
    for key, limit in (
        ("session_id", _MAX_NAME),
        ("last_agent_name", _MAX_NAME),
        ("traceparent", _MAX_TRACEPARENT),
    ):
        text = _bounded_text(raw.get(key), limit)
        if text:
            out[key] = text
    sequence = raw.get("agent_sequence")
    if isinstance(sequence, int) and not isinstance(sequence, bool) and 0 < sequence < 2**31:
        out["agent_sequence"] = sequence
    return out


def inject_observe_metadata(message: Any, extra: Mapping[str, Any]) -> None:
    """Merge observe keys onto the SDK message. Never raises."""
    if message is None or not extra:
        return
    try:
        current = getattr(message, "metadata", None)
        if current is None:
            message.metadata = dict(extra)
            return
        updater = getattr(current, "update", None)
        if callable(updater):
            updater(dict(extra))
            return
        if isinstance(current, Mapping):
            message.metadata = {**dict(current), **dict(extra)}
    except Exception:
        _logger.debug("a2a observe metadata inject failed", exc_info=True)


def emit_sent(*, side: str = "client", key: str, source: str, target: str, **attrs: Any) -> None:
    _emit(MESSAGE_SENT, side=side, key=key, source=source, target=target, **attrs)


def emit_received(*, side: str = "server", key: str, source: str, target: str, **attrs: Any) -> None:
    _emit(MESSAGE_RECEIVED, side=side, key=key, source=source, target=target, **attrs)


def emit_completed(*, side: str, key: str, source: str, target: str, **attrs: Any) -> None:
    _emit(MESSAGE_COMPLETED, side=side, key=key, source=source, target=target, **attrs)


def emit_error(*, side: str, key: str, **attrs: Any) -> None:
    _emit(ERROR, side=side, key=key, **attrs)


def emit_agent_card(card: Mapping[str, Any], *, side: str, agent_id: str = "") -> None:
    if not _cfg().hooks:
        return
    name = str(card.get("name") or agent_id or "")
    _emit(
        DISCOVERY,
        side=side,
        key=f"card:{name}",
        discovery_kind="agent_card",
        card={"name": name, "description": str(card.get("description") or "")},
        agent_id=name,
    )


def _message_ids(message: Any) -> tuple[str, str, str]:
    if message is None:
        return "", "", ""
    context_id = str(getattr(message, "context_id", None) or getattr(message, "contextId", None) or "")
    task_id = str(getattr(message, "task_id", None) or getattr(message, "taskId", None) or "")
    message_id = str(getattr(message, "message_id", None) or getattr(message, "messageId", None) or "")
    if hasattr(message, "get"):
        context_id = context_id or str(message.get("context_id") or message.get("contextId") or "")
        task_id = task_id or str(message.get("task_id") or message.get("taskId") or "")
        message_id = message_id or str(message.get("message_id") or message.get("messageId") or "")
    return context_id, task_id, message_id


def start_call_state(
    state: Any,
    *,
    key: str,
    source: str,
    target: str,
    session_id: str = "",
) -> dict[str, Any]:
    """Remember one call's identity on the SDK call context, so ``after`` can join it to ``before``."""
    call = {"key": key, "source": source, "target": target, "session_id": session_id, "done": False}
    if state is not None:
        try:
            state[_STATE_KEY] = call
        except Exception:
            _logger.debug("a2a call state not stored", exc_info=True)
    return call


def finish_call(state: Any, *, error: BaseException | None = None) -> None:
    """Emit the terminal event of a call once: completed, or error. Never raises."""
    try:
        call = state.get(_STATE_KEY) if state is not None else None
        if not isinstance(call, dict) or call.get("done"):
            return
        call["done"] = True
        if not call.get("key") or not _cfg().hooks:
            return  # no key: ``before`` never ran, nothing was announced
        if error is None:
            emit_completed(
                side="client",
                key=call["key"],
                source=call["source"],
                target=call["target"],
                session_id=call["session_id"],
            )
        else:
            emit_error(
                side="client",
                key=call["key"],
                source=call["source"],
                target=call["target"],
                session_id=call["session_id"],
                error_type=type(error).__name__,
            )
    except Exception:
        _logger.debug("a2a finish_call failed", exc_info=True)


def _is_terminal(method: str, result: Any) -> bool:
    """One non-streaming reply, a message, or a task in a final state ends a call."""
    if method == "send_message":
        return True
    try:
        if result.HasField("message"):
            return True
        if result.HasField("task"):
            from a2a.types.a2a_pb2 import TaskState

            return TaskState.Name(result.task.status.state) in _TERMINAL_TASK_STATES
        if result.HasField("status_update"):
            from a2a.types.a2a_pb2 import TaskState

            return TaskState.Name(result.status_update.status.state) in _TERMINAL_TASK_STATES
    except Exception:
        return False
    return False


class A2ATransportInterceptor:
    """Client interceptor: inject observe metadata and emit sent/completed for each call.

    One interceptor serves every call on a connection, so a call's identity travels on
    the SDK call context (``args.context.state``), never on the interceptor.
    """

    def __init__(self, *, agent_id: str = "", peer_id: str = "", session_id: str = "") -> None:
        self.agent_id = agent_id
        self.peer_id = peer_label(peer_id)
        self.session_id = session_id

    async def before(self, args: Any) -> None:
        try:
            method = str(getattr(args, "method", "") or "")
            if method not in {"send_message", "send_message_streaming"}:
                return
            cfg = _cfg()
            if not (cfg.hooks or cfg.propagate_metadata):
                return
            state = getattr(getattr(args, "context", None), "state", None)
            known = state.get(_STATE_KEY) if state is not None else None
            source = (known or {}).get("source") or self.agent_id
            target = (known or {}).get("target") or self.peer_id
            session_id = (known or {}).get("session_id") or self.session_id
            message = getattr(getattr(args, "input", None), "message", None)
            context_id, task_id, message_id = _message_ids(message)
            key = correlation_key(context_id=context_id, task_id=task_id, message_id=message_id)
            if key == "||":
                key = f"a2a:{uuid.uuid4().hex}"
            inject_observe_metadata(message, observe_metadata(session_id=session_id, agent_id=source))
            start_call_state(state, key=key, source=source, target=target, session_id=session_id)
            if cfg.hooks:
                emit_sent(
                    key=key,
                    source=source,
                    target=target,
                    session_id=session_id,
                    agent_id=source,
                    message_id=message_id,
                    context_id=context_id,
                    task_id=task_id,
                )
        except Exception:
            _logger.debug("a2a transport interceptor before failed", exc_info=True)

    async def after(self, args: Any) -> None:
        try:
            method = str(getattr(args, "method", "") or "")
            if method not in {"send_message", "send_message_streaming"}:
                return
            if not _is_terminal(method, getattr(args, "result", None)):
                return
            finish_call(getattr(getattr(args, "context", None), "state", None))
        except Exception:
            _logger.debug("a2a transport interceptor after failed", exc_info=True)


__all__ = [
    "A2ATransportInterceptor",
    "OBSERVE_KEY",
    "bound_identity",
    "current_identity",
    "PROTOCOL",
    "correlation_key",
    "emit_agent_card",
    "emit_completed",
    "emit_error",
    "emit_received",
    "emit_sent",
    "finish_call",
    "inject_observe_metadata",
    "observe_metadata",
    "peer_label",
    "read_observe_metadata",
    "start_call_state",
]
