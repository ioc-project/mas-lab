#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The slimrpc servicer of a verified agent: every RPC but the sends is refused; without a verifier none is."""

from __future__ import annotations

from typing import Any

import pytest
import slim_bindings as sb
from library_ioa.plugins.a2a.layer import _OTHER_RPCS, _STREAMING_RPCS, _gated_handler, _Refusals
from mas.library.standard.lib.stack import PeerChallenge


class _Base:
    """The SDK servicer's shape: every method of the A2A service answers; ``ran`` records what the SDK served."""

    ran: list[str] = []

    def __init__(self, card: Any, handler: Any) -> None: ...

    async def SendMessage(self, request: Any, context: Any) -> str:  # noqa: N802
        return "sent"

    async def SendStreamingMessage(self, request: Any, context: Any):  # noqa: N802
        yield "sent"


def _serve(name: str) -> Any:
    if name in _STREAMING_RPCS:

        async def stream(self: Any, request: Any, context: Any):
            _Base.ran.append(name)
            yield name

        return stream

    async def one(self: Any, request: Any, context: Any) -> str:
        _Base.ran.append(name)
        return name

    return one


for _name in _OTHER_RPCS:
    setattr(_Base, _name, _serve(_name))


async def _call(servicer: Any, name: str) -> Any:
    result = getattr(servicer, name)(object(), object())
    if name in _STREAMING_RPCS:
        return [item async for item in result]
    return await result


@pytest.mark.parametrize("name", _OTHER_RPCS)
async def test_every_other_rpc_is_unauthenticated_on_a_verified_agent(name: str) -> None:
    """Kills: a handler that wraps only the two sends (GetTask, ListTasks, Cancel, Subscribe, push config were open)."""
    _Base.ran = []
    servicer = _gated_handler(_Base, object(), object(), _Refusals(), gated=True)
    with pytest.raises(sb.RpcError.Rpc) as error:
        await _call(servicer, name)
    assert error.value.code == sb.RpcCode.UNAUTHENTICATED
    assert _Base.ran == []


@pytest.mark.parametrize("name", _OTHER_RPCS)
async def test_without_a_verifier_the_rpcs_are_the_sdks_own(name: str) -> None:
    _Base.ran = []
    servicer = _gated_handler(_Base, object(), object(), _Refusals(), gated=False)
    await _call(servicer, name)
    assert _Base.ran == [name]


def test_refusals_of_sends_nobody_asked_about_are_bounded_and_expire() -> None:
    """Kills: a refusal table that grows with every non-blocking send."""
    refusals = _Refusals(ttl=60.0, limit=4)
    for n in range(50):
        refusals.add(f"m{n}", PeerChallenge({}, code="refused"))
    kept = [n for n in range(50) if refusals.pop(f"m{n}") is not None]
    assert kept == [46, 47, 48, 49]  # the limit is four and the oldest go first
    expired = _Refusals(ttl=0.0, limit=4)
    expired.add("m", PeerChallenge({}, code="refused"))
    assert expired.pop("m") is None


class _SlimContext:
    """What a slimrpc call carries for the SDK: no verified peer, so the SDK's user is the anonymous one."""

    def metadata(self) -> dict[str, str]:
        return {}

    def peer_subject(self) -> str:
        return ""

    def peer_issuer(self) -> str:
        return ""


async def _task_with_history(handler: Any, text: str) -> tuple[Any, Any]:
    from a2a.helpers import new_text_message
    from a2a.types import SendMessageRequest
    from a2a.types.a2a_pb2 import Role

    message = new_text_message(text, context_id="c1", role=Role.ROLE_USER)
    reply = await handler.SendMessage(SendMessageRequest(message=message), _SlimContext())
    return reply.task, message


@pytest.mark.parametrize("streaming", [False, True])
async def test_a_gated_send_naming_a_task_is_refused_before_the_sdk_and_the_task_is_unchanged(streaming: bool) -> None:
    """Kills: a task continuation let through on a gated server (the SDK appended the message to the victim's task and
    flipped its state before anything could refuse it)."""
    from a2a.helpers import new_text_message
    from a2a.server.context import ServerCallContext
    from a2a.types import GetTaskRequest, SendMessageRequest
    from a2a.types.a2a_pb2 import Role, TaskState
    from library_ioa.plugins.a2a.agentcard import slimrpc_agent_card
    from library_ioa.plugins.a2a.server.app import build_request_handler
    from library_ioa.plugins.a2a.server.executor import MasLabAgentExecutor
    from slima2a.handler import SRPCHandler

    request_handler = build_request_handler(
        slimrpc_agent_card("agent", "org/ns/agent"),
        MasLabAgentExecutor(lambda *a, **k: {"text": "need more", "task_state": "input_required"}),
    )
    servicer = _gated_handler(
        SRPCHandler, slimrpc_agent_card("agent", "org/ns/agent"), request_handler, _Refusals(), gated=True
    )
    victim, _ = await _task_with_history(servicer, "alice private")
    before = await request_handler.on_get_task(GetTaskRequest(id=victim.id), ServerCallContext())
    assert before.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
    hijack = new_text_message("mallory", context_id=victim.context_id, task_id=victim.id, role=Role.ROLE_USER)
    with pytest.raises(sb.RpcError.Rpc) as error:
        if streaming:
            async for _ in servicer.SendStreamingMessage(SendMessageRequest(message=hijack), _SlimContext()):
                pass
        else:
            await servicer.SendMessage(SendMessageRequest(message=hijack), _SlimContext())
    assert error.value.code == sb.RpcCode.INVALID_ARGUMENT and "refused:task_continuation" in str(error.value)
    after = await request_handler.on_get_task(GetTaskRequest(id=victim.id), ServerCallContext())
    assert after.SerializeToString() == before.SerializeToString()  # state and history untouched


async def test_a_send_naming_a_task_is_the_sdks_own_on_a_server_with_no_verifier() -> None:
    """Without a verifying layer nothing changes: the SDK handles the continuation."""
    from a2a.helpers import new_text_message
    from a2a.types import SendMessageRequest
    from a2a.types.a2a_pb2 import Role
    from library_ioa.plugins.a2a.agentcard import slimrpc_agent_card
    from library_ioa.plugins.a2a.server.app import build_request_handler
    from library_ioa.plugins.a2a.server.executor import MasLabAgentExecutor
    from slima2a.handler import SRPCHandler

    request_handler = build_request_handler(
        slimrpc_agent_card("agent", "org/ns/agent"),
        MasLabAgentExecutor(lambda *a, **k: {"text": "need more", "task_state": "input_required"}),
    )
    servicer = _gated_handler(
        SRPCHandler, slimrpc_agent_card("agent", "org/ns/agent"), request_handler, _Refusals(), gated=False
    )
    victim, _ = await _task_with_history(servicer, "first")
    again = new_text_message("second", context_id=victim.context_id, task_id=victim.id, role=Role.ROLE_USER)
    reply = await servicer.SendMessage(SendMessageRequest(message=again), _SlimContext())
    assert reply.task.id == victim.id
