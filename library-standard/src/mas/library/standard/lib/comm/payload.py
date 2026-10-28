#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Inbound send payload: size bound, intent set, and a model-only label."""

from __future__ import annotations

import inspect
from collections.abc import Iterable, Mapping
from typing import Any

from mas.runtime.boundary.agentcomm.protocol import AgentCommError
from mas.runtime.boundary.agentcomm.protocol import InboundSend as InboundSend
from mas.runtime.boundary.agentcomm.protocol import bound_inbound as bound_inbound
from mas.runtime.boundary.agentcomm.protocol import inbound_send as inbound_send
from mas.runtime.boundary.control.contract import (
    ControlCapability,
    ControlDenied,
    SessionPaused,
)
from mas.runtime.boundary.delegation.policy import SEND_INTENTS

MAX_MESSAGE_SIZE = 1_048_576
TASK_ONLY_INTENTS = frozenset({"task"})
INFORM_ASK_INTENTS = frozenset({"task", "inform", "ask"})
PUBLISH_INTENT = "publish"
STEER_INTENT = "steer"
UNTRUSTED_INTENTS = frozenset({"inform", "ask", PUBLISH_INTENT, STEER_INTENT})
STEER_MODES = frozenset({"after", "preempt", "replace"})
_STEER_MODE_ALIASES = {"enqueue": "after", "amend": "preempt", "inject_now": "preempt"}
PEER_STEER_METHODS = frozenset({"steer"})


def normalize_send_intent(raw: object) -> str:
    kind = str(raw or "task").strip().lower() or "task"
    if kind not in SEND_INTENTS:
        raise AgentCommError(f"unknown intent {raw!r}")
    return kind


def intents_honoured_by(communication: Any) -> frozenset[str]:
    hook = getattr(communication, "honoured_send_intents", None)
    if callable(hook):
        return _as_intent_set(hook())
    flags = getattr(communication, "HONOURED_SEND_INTENTS", None)
    if flags is not None:
        return _as_intent_set(flags)
    return TASK_ONLY_INTENTS


def _as_intent_set(raw: Iterable[object]) -> frozenset[str]:
    return frozenset(str(item) for item in raw)


def require_honoured_intent(communication: Any, intent: str) -> str:
    kind = normalize_send_intent(intent)
    if kind not in intents_honoured_by(communication):
        raise AgentCommError(f"intent {kind!r} is not supported")
    return kind


def check_message_size(text: object, *, max_size: int = MAX_MESSAGE_SIZE) -> None:
    if not isinstance(text, str):
        raise AgentCommError("message must be a string")
    if len(text.encode("utf-8")) > max_size:
        raise AgentCommError("message exceeds max size")


def label_untrusted_peer(text: str, *, sender: str, intent: str = "") -> str:
    who = str(sender or "").strip() or "unknown"
    head = f"[untrusted peer message from {who}"
    if intent:
        head += f" intent={intent}"
    return f"{head}]\n{text}"


def inbound_payload(text: str, *, sender: str, intent: str) -> str:
    if intent in UNTRUSTED_INTENTS:
        return label_untrusted_peer(text, sender=sender, intent=intent)
    return text


def send_result(intent: str, result: str) -> str:
    return "delivered" if intent == "inform" else result


def normalize_steer_mode(raw: object) -> str:
    when = str(raw or "after").strip().lower() or "after"
    when = _STEER_MODE_ALIASES.get(when, when)
    if when not in STEER_MODES:
        raise AgentCommError(f"unknown steer mode {raw!r}")
    return when


def steer_peer_sessions(raw: object) -> dict[str, str]:
    """Target name → control session. A sequence is names with no session."""
    if not raw:
        return {}
    if isinstance(raw, Mapping) and not isinstance(raw, (str, bytes)):
        return {str(key).strip(): str(value).strip() for key, value in raw.items() if str(key).strip()}
    if isinstance(raw, Iterable) and not isinstance(raw, (str, bytes)):
        return {str(item).strip(): "" for item in raw if str(item).strip()}
    return {}


def peer_steer_capability(*, actor: str, session_id: str) -> ControlCapability:
    """Peer send may steer the mapped session and nothing else."""
    who = str(actor or "peer").strip() or "peer"
    return ControlCapability(
        actor=who,
        surface="peer",
        session_ids=frozenset({str(session_id)}),
        methods=PEER_STEER_METHODS,
    )


def peer_steer_control(control: Any, *, actor: str, session_id: str) -> Any:
    """Least-privilege handle: this sender, steer only, this session.

    The result is the intersection of the passed control and the peer token.
    A handle that could not steer this session still cannot. If the object
    has no ``scoped`` method, reject a control that can do more than steer
    the mapped session.
    """
    cap = getattr(control, "capability", None)
    if cap is None or not cap.allows("steer", session_id):
        raise AgentCommError("steer is not allowed")
    wanted = peer_steer_capability(actor=actor, session_id=session_id)
    scoped = getattr(control, "scoped", None)
    if callable(scoped):
        handle = scoped(wanted)
        handle_cap = getattr(handle, "capability", None)
        if handle_cap is None or not handle_cap.allows("steer", session_id):
            raise AgentCommError("steer is not allowed")
        return handle
    extra = cap.methods is None or bool(set(cap.methods) - PEER_STEER_METHODS)
    wide = cap.session_ids is None or session_id not in cap.session_ids
    if extra or wide:
        raise AgentCommError("control allows more than steer")
    return control


def dispatch_steer(
    control: Any,
    *,
    session_id: str,
    text: str,
    mode: str,
    target: str,
    allowed: Iterable[str],
    sender: str = "",
) -> str:
    """Call ``ControlContract.steer``. The link supplies the session; default mode is after."""
    if control is None or not callable(getattr(control, "steer", None)):
        raise AgentCommError("steer is unsupported")
    if str(target) not in {str(item) for item in allowed}:
        raise AgentCommError("steer is not allowed")
    sid = str(session_id or "").strip()
    if not sid:
        raise AgentCommError("steer requires a session id")
    when = normalize_steer_mode(mode)
    labelled = inbound_payload(text, sender=sender, intent=STEER_INTENT)
    extra: dict[str, Any] = {"text": labelled, "mode": when}
    handle = peer_steer_control(control, actor=sender, session_id=sid)
    try:
        params = inspect.signature(handle.steer).parameters
    except (TypeError, ValueError):
        params = {}
    names = set(params)
    var_kw = any(item.kind is inspect.Parameter.VAR_KEYWORD for item in params.values())
    if when == "after" and (var_kw or "at" in names):
        extra["at"] = "tail"
    try:
        handle.steer(sid, **extra)
    except KeyError as exc:
        raise AgentCommError(f"unknown session {sid!r}") from exc
    except (ControlDenied, SessionPaused) as exc:
        raise AgentCommError(str(exc)) from exc
    except ValueError as exc:
        raise AgentCommError(str(exc)) from exc
    return "delivered"
