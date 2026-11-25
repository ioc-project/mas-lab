#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The A2A same-task handshake: answer an auth-required task on the task it came from.

A peer that wants proof before it works answers a message with a task in the auth-required state. The
status message carries what it needs in a data part (a challenge, a request). The client does not open a
new task: it sends one follow-up message with the same task id and the reply in a data part
``{"method": ..., "params": ...}``, and the peer's answer to *that* message is the result of the whole send.

This module is the protocol side only. Who answers, and with what, is a
:class:`~mas.library.standard.lib.stack.session.SessionHook`; it sees the challenge as ``peer`` and sends its
reply through :meth:`TaskChannel.request`. One round per send: a second ``request`` raises, and so does a
peer that answers the follow-up with auth-required again.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from a2a.helpers import get_data_parts, get_message_text, new_data_part, new_message
from a2a.types.a2a_pb2 import Message, Role, StreamResponse, TaskState
from google.protobuf.json_format import MessageToDict
from mas.library.standard.lib.stack import SessionHook, SessionInfo
from mas.runtime.boundary.agentcomm.protocol import AgentCommError

Send = Callable[[Message], Awaitable[list[StreamResponse]]]
"""Send one message through the binding that carried the first one and collect the answer."""

#: A follow-up's data part: the hook's ``method`` and ``params``.
METHOD_KEY = "method"
PARAMS_KEY = "params"


def _task_of(responses: list[StreamResponse]) -> Any:
    for response in reversed(responses):
        if response.WhichOneof("payload") == "task":
            return response.task
    return None


def challenge_peer(data: Mapping[str, Any], metadata: Mapping[str, Any]) -> dict[str, Any]:
    """What an auth-required status message declared, in the one shape a scheme reads (``PresentRequest.peer``).

    ``challenge`` is the mapping the peer sent (the data part key ``challenge``, else the first mapping under
    its own metadata or data key), ``wire`` the raw string it sent in place of, or next to, a mapping, as
    received. ``transport`` holds the data parts and the metadata untouched, for a scheme that reads a peer's own
    shape. Nothing is dropped and nothing is renamed.
    """
    challenge = data.get("challenge")
    if not isinstance(challenge, Mapping):
        found = [v for v in (*metadata.values(), *data.values()) if isinstance(v, Mapping)]
        challenge = found[0] if found else {}
    raw = data.get("wire")
    if not isinstance(raw, str):
        strings = [v for v in (*metadata.values(), *data.values()) if isinstance(v, str)]
        raw = strings[0] if strings else None
    peer: dict[str, Any] = {"challenge": dict(challenge)}
    if raw is not None:
        peer["wire"] = raw
    peer["transport"] = {"data": dict(data), "metadata": dict(metadata)}
    return peer


def auth_required(responses: list[StreamResponse]) -> tuple[str, str, dict[str, Any]] | None:
    """``(task id, context id, what the peer asked for)`` when the answer is an auth-required task, else ``None``."""
    task = _task_of(responses)
    if task is None or task.status.state != TaskState.TASK_STATE_AUTH_REQUIRED:
        return None
    data: dict[str, Any] = {}
    for part in get_data_parts(task.status.message.parts):
        if isinstance(part, dict):
            data.update(part)
    # a scheme may carry its challenge in the status message's metadata (``ChallengeRequired.carry``)
    return task.id, task.context_id, challenge_peer(data, MessageToDict(task.status.message.metadata))


def reply_mapping(responses: list[StreamResponse]) -> dict[str, Any]:
    """The answer to a follow-up as a mapping: the data parts merged, plus ``text`` and ``state``."""
    out: dict[str, Any] = {}
    text = ""
    state = ""
    for response in responses:
        kind = response.WhichOneof("payload")
        message = None
        if kind == "message":
            message = response.message
        elif kind == "task":
            message = response.task.status.message
            state = TaskState.Name(response.task.status.state)
            for artifact in response.task.artifacts:
                for part in get_data_parts(artifact.parts):
                    if isinstance(part, dict):
                        out.update(part)
        if message is not None:
            for part in get_data_parts(message.parts):
                if isinstance(part, dict):
                    out.update(part)
            text = get_message_text(message) or text
    out["text"] = text
    if state:
        out["state"] = state
    return out


class TaskChannel(SessionInfo):
    """What a hook sees of an auth-required task: the challenge, and one reply on the same task."""

    def __init__(self, send: Send, task_id: str, context_id: str, peer: Mapping[str, Any]) -> None:
        self._send = send
        self._task_id = task_id
        self._context_id = context_id
        self.peer = dict(peer)
        self.answers: list[StreamResponse] = []

    async def request(
        self, method: str, params: Mapping[str, Any], *, data_key: str | None = None
    ) -> Mapping[str, Any]:
        if self.answers:
            raise AgentCommError("a2a handshake: one reply per send")
        # the stack's own shape is ``{method, params}``; a scheme that speaks a peer's shape names the key
        data = {METHOD_KEY: method, PARAMS_KEY: dict(params)} if data_key is None else {data_key: dict(params)}
        follow_up = new_message(
            [new_data_part(data)],
            context_id=self._context_id or None,
            task_id=self._task_id,
            role=Role.ROLE_USER,
        )
        self.answers = await self._send(follow_up)
        return reply_mapping(self.answers)


async def complete(responses: list[StreamResponse], hook: SessionHook | None, send: Send) -> list[StreamResponse]:
    """``responses``, or when they are an auth-required task and a hook is given, the answer to its follow-up.

    Without a hook nothing changes: the answer is returned as it is, as it was before hooks existed.
    """
    asked = auth_required(responses)
    if asked is None or hook is None:
        return responses
    task_id, context_id, peer = asked
    channel = TaskChannel(send, task_id, context_id, peer)
    await hook.established(channel)
    if not channel.answers:
        raise AgentCommError("a2a handshake: the hook sent no reply on the task")
    if auth_required(channel.answers) is not None:
        raise AgentCommError("remote A2A task still requires authentication")
    return channel.answers
