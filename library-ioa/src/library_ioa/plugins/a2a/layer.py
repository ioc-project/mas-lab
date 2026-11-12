#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""layer/a2a-framing: A2A JSON-RPC framing above any transport of a stack.

A send becomes one A2A ``SendMessage`` JSON-RPC request (A2A 1.0, the method
the SDK client sends and the ``a2a`` exposure accepts); the transport below
carries that text and returns the JSON-RPC response, which this layer reads
back to the reply text. On the inbound side ``serve`` turns a received
``SendMessage`` request into the task the session turn answers, and the
answer into a JSON-RPC response.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Mapping
from typing import Any, ClassVar

from a2a.helpers import new_text_message
from a2a.types import Role, SendMessageRequest, SendMessageResponse, TaskState
from google.protobuf.json_format import MessageToDict, ParseDict, ParseError
from mas.library.standard.lib.comm.payload import normalize_send_intent
from mas.library.standard.lib.stack import Envelope, Handler, Layer
from mas.runtime.boundary.agentcomm.protocol import AgentCommError

logger = logging.getLogger(__name__)

METHOD = "SendMessage"
VERSION_HEADER = "a2a-version"
PROTOCOL_VERSION = "1.0"
FAILED_STATES = frozenset({TaskState.TASK_STATE_FAILED, TaskState.TASK_STATE_REJECTED, TaskState.TASK_STATE_CANCELED})
_PARSE_ERROR = -32700
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_INVALID_PARAMS = -32602
_HANDLER_FAILED = "Agent execution failed"


def encode_request(
    task: str,
    *,
    context_id: str = "",
    correlation_id: int = 0,
    caller_call_id: str = "",
    request_id: str = "",
) -> str:
    """The JSON-RPC text of one ``SendMessage`` call, as the SDK client builds it."""
    request = SendMessageRequest(message=new_text_message(task, context_id=context_id or None, role=Role.ROLE_USER))
    request.message.metadata.update(
        {"mas.correlation_id": str(int(correlation_id or 0)), "mas.caller_call_id": str(caller_call_id or "")}
    )
    body = {"jsonrpc": "2.0", "id": request_id or str(uuid.uuid4()), "method": METHOD, "params": MessageToDict(request)}
    return json.dumps(body, ensure_ascii=False)


def _parts_text(parts: Any) -> str:
    return "".join(part.text for part in parts if part.WhichOneof("content") == "text")


def decode_response(raw: Any) -> str:
    """The reply text of a JSON-RPC ``SendMessage`` response; ``AgentCommError`` when the call failed."""
    text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
    if not isinstance(text, str):
        raise AgentCommError("A2A response is not text")
    try:
        body = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AgentCommError("A2A response is not JSON") from exc
    if not isinstance(body, dict):
        raise AgentCommError("A2A response is not a JSON-RPC object")
    error = body.get("error")
    if error is not None:
        message = error.get("message") if isinstance(error, dict) else error
        code = error.get("code") if isinstance(error, dict) else None
        raise AgentCommError(f"A2A error {code}: {message}" if code is not None else f"A2A error: {message}")
    result = body.get("result")
    if not isinstance(result, dict):
        raise AgentCommError("A2A response has no result")
    try:
        response = ParseDict(result, SendMessageResponse(), ignore_unknown_fields=True)
    except ParseError as exc:
        raise AgentCommError("A2A response result is not a SendMessage response") from exc
    kind = response.WhichOneof("payload")
    if kind == "message":
        return _parts_text(response.message.parts)
    if kind != "task":
        raise AgentCommError("A2A response has neither a message nor a task")
    task = response.task
    status_text = _parts_text(task.status.message.parts)
    if task.status.state in FAILED_STATES:
        raise AgentCommError(f"remote A2A task failed: {status_text}" if status_text else "remote A2A task failed")
    artifacts = "".join(_parts_text(artifact.parts) for artifact in task.artifacts)
    if artifacts:
        return artifacts
    if status_text:
        return status_text
    for message in reversed(task.history):
        if message.role == Role.ROLE_AGENT:
            return _parts_text(message.parts)
    return ""


def _error(request_id: Any, code: int, message: str) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})


def _result(request_id: Any, response: SendMessageResponse) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": request_id, "result": MessageToDict(response)}, ensure_ascii=False)


def _failed(request_id: Any, context_id: str) -> str:
    response = SendMessageResponse()
    response.task.id = str(uuid.uuid4())
    response.task.context_id = context_id
    response.task.status.state = TaskState.TASK_STATE_FAILED
    response.task.status.message.CopyFrom(new_text_message(_HANDLER_FAILED, context_id=context_id or None))
    response.task.status.message.role = Role.ROLE_AGENT
    return _result(request_id, response)


class A2ALayer(Layer):
    """A2A framing between the agent call and a transport.

    Client side, ``send`` turns the task into a ``SendMessage`` request:
    the text is one text part, ``context_id`` is the message ``contextId``,
    and ``correlation_id`` / ``caller_call_id`` ride the message metadata as
    ``mas.correlation_id`` / ``mas.caller_call_id``, as the ``a2a``
    ``agent_comm`` plugin sends them. A2A has no inform/ask wire: only the
    ``task`` intent is honoured; any other raises ``AgentCommError``. The
    response's message parts are the reply; a ``failed``, ``rejected`` or
    ``canceled`` task, or a JSON-RPC error, raises ``AgentCommError``.

    Server side, ``serve(handler)`` answers a ``SendMessage`` request: the
    handler gets the text as ``payload`` and the call ids and context id in
    ``meta``, and its reply goes back as an agent message in the same
    context. A request without a context id gets a new one, as the exposure
    does. A handler that raises is a ``failed`` task with a generic message;
    the error text stays in this process's log.
    """

    HONOURED_SEND_INTENTS: ClassVar[frozenset[str]] = frozenset({"task"})

    def honoured_send_intents(self) -> frozenset[str]:
        return self.HONOURED_SEND_INTENTS

    def _encode(self, envelope: Envelope) -> Envelope:
        intent = normalize_send_intent(envelope.meta.get("intent") or "task")
        if intent not in self.HONOURED_SEND_INTENTS:
            raise AgentCommError(f"intent {intent!r} is not supported")
        if not isinstance(envelope.payload, str):
            raise AgentCommError("message must be a string")
        meta = envelope.meta
        body = encode_request(
            envelope.payload,
            context_id=str(meta.get("context_id") or ""),
            correlation_id=int(meta.get("correlation_id") or 0),
            caller_call_id=str(meta.get("caller_call_id") or ""),
        )
        return envelope.with_(payload=body, headers={VERSION_HEADER: PROTOCOL_VERSION}, meta={"intent": "task"})

    def send(self, envelope: Envelope) -> Any:
        return decode_response(self._require().send(self._encode(envelope)))

    async def asend(self, envelope: Envelope) -> Any:
        return decode_response(await super().asend(self._encode(envelope)))

    def serve(self, handler: Handler) -> Handler:
        def answer(envelope: Envelope) -> str:
            return self._answer(envelope, handler)

        return answer

    def _answer(self, envelope: Envelope, handler: Handler) -> str:
        raw = envelope.payload
        text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
        try:
            body = json.loads(text) if isinstance(text, str) else None
        except json.JSONDecodeError:
            return _error(None, _PARSE_ERROR, "Parse error")
        if not isinstance(body, dict) or body.get("jsonrpc") != "2.0":
            return _error(body.get("id") if isinstance(body, dict) else None, _INVALID_REQUEST, "Invalid Request")
        request_id = body.get("id")
        if body.get("method") != METHOD:
            return _error(request_id, _METHOD_NOT_FOUND, "Method not found")
        params = body.get("params")
        try:
            request = ParseDict(
                params if isinstance(params, Mapping) else {}, SendMessageRequest(), ignore_unknown_fields=True
            )
        except ParseError:
            return _error(request_id, _INVALID_PARAMS, "Invalid params")
        message = request.message
        metadata = MessageToDict(message.metadata) if message.HasField("metadata") else {}
        try:
            correlation_id = int(metadata.get("mas.correlation_id") or 0)
        except (TypeError, ValueError):
            logger.warning("ignoring invalid A2A mas.correlation_id metadata")
            correlation_id = 0
        context_id = message.context_id or str(uuid.uuid4())
        inner = envelope.with_(
            payload=_parts_text(message.parts),
            meta={
                "intent": "task",
                "context_id": context_id,
                "correlation_id": correlation_id,
                "caller_call_id": str(metadata.get("mas.caller_call_id") or ""),
            },
        )
        try:
            reply = handler(inner)
        except Exception:
            logger.exception("A2A request handler failed")
            return _failed(request_id, context_id)
        answer = new_text_message(str(reply if reply is not None else ""), context_id=context_id, role=Role.ROLE_AGENT)
        return _result(request_id, SendMessageResponse(message=answer))
