#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The identity layer above a2a-rpc on a real SLIM node: headers cross as metadata, a refusal is UNAUTHENTICATED."""

from __future__ import annotations

from typing import Any

import pytest
from mas.library.standard.lib.identity.principal import bind, current_principal
from mas.library.standard.lib.stack import StackHandle
from mas.runtime.boundary.agentcomm.protocol import AgentCommError
from wiring_builders import key_ref

SECRET = "e4aaecb9ae0b23b82086bb8a8633e01fba16ae8d9c1379a613c00838"


def _handle(slim_node: str, identity_params: dict[str, Any], run_turn: Any) -> StackHandle:
    slim = {
        "type": "slim",
        "params": {"url": slim_node, "identity": "acme/lab/wiring", "secret": SECRET, "timeout_s": 5.0},
    }
    return StackHandle("wiring", [slim, {"type": "a2a-rpc"}, {"type": "identity", "params": identity_params}], run_turn)


async def test_presentation_headers_cross_slimrpc_and_the_server_verifies_them(slim_node: str) -> None:
    seen: list[tuple[str, dict[str, str]]] = []

    async def run_turn(agent_id: str, task: str, *rest: Any) -> str:
        seen.append((task, dict(current_principal())))
        return f"{agent_id}:{task}"

    params = {"identity": "id1", "scheme": "wiring_bearer", "params": {"token": "ok"}}
    handle = _handle(slim_node, params, run_turn)
    try:
        caller = handle.link("caller")
        handle.link("callee")
        with bind({"actor": "caller"}):
            assert await caller.asend("callee", "hi") == "callee:hi"
    finally:
        handle.close()
    # the turn ran, for the callee, on behalf of the subject its verification found in what crossed slimrpc
    ((task, principal),) = seen
    assert task == "hi"
    assert (principal["actor"], principal["subject"]) == ("callee", "u1") and principal["session"]


async def test_a_caller_the_server_cannot_verify_gets_unauthenticated_and_the_turn_never_runs(slim_node: str) -> None:
    turns: list[str] = []

    async def run_turn(agent_id: str, task: str, *rest: Any) -> str:
        turns.append(task)
        return "never"

    params = {"identity": "id1", "scheme": "wiring_bearer", "params": {"token": "wrong"}}
    handle = _handle(slim_node, params, run_turn)
    try:
        caller = handle.link("caller")
        handle.link("callee")
        with pytest.raises(AgentCommError):
            await caller.asend("callee", "hi")
    finally:
        handle.close()
    assert turns == []


# --- a challenge over slimrpc: answered once, on the same send ------------------------------------------------

KEY = "k" * 16
PE = {
    "key": key_ref(KEY),
    "subject": "alice",
    "claims": {"group": "infraadmin"},
    "requirements": ["group"],
    "audience": "bob",
}
BOB = {"protocol": "a2a", "target": "bob"}


def _exchange(slim_node: str, scheme: str, run_turn: Any, **extra: Any) -> StackHandle:
    params = {"identity": "id1", "scheme": scheme, "params": {**PE, **extra}, "audience": BOB}
    return _handle(slim_node, params, run_turn)


async def test_a_challenge_is_answered_with_a_signed_presentation_on_the_retried_send(slim_node: str) -> None:
    seen: list[tuple[str, dict[str, str]]] = []

    async def run_turn(agent_id: str, task: str, *rest: Any) -> str:
        seen.append((task, dict(current_principal())))
        return f"{agent_id}:{task}"

    handle = _exchange(slim_node, "presentation_exchange", run_turn)
    try:
        caller = handle.link("alice")
        handle.link("bob")
        with bind({"actor": "alice"}):
            assert await caller.asend("bob", "hi") == "bob:hi"
            assert await caller.asend("bob", "again") == "bob:again"  # a new nonce each time: the first is spent
    finally:
        handle.close()
    assert [task for task, _ in seen] == ["hi", "again"]
    assert all(p["subject"] == "alice" and p["actor"] == "bob" for _, p in seen)


async def test_a_peer_that_keeps_challenging_is_asked_once_and_then_denied(slim_node: str) -> None:
    """Kills: a retry loop; a second answer to a second challenge."""
    from wiring_schemes import AlwaysChallenge

    turns: list[str] = []

    async def run_turn(agent_id: str, task: str, *rest: Any) -> str:
        turns.append(task)
        return "never"

    handle = _exchange(slim_node, "wiring_always_challenge", run_turn)
    try:
        caller = handle.link("alice")
        handle.link("bob")
        with bind({"actor": "alice"}):
            with pytest.raises(AgentCommError, match="^identity: refused:unauthorized$"):
                await caller.asend("bob", "hi")
    finally:
        handle.close()
    assert len(AlwaysChallenge.VERIFIES) == 2  # the send, and the one retry
    # the answer was given the challenge as the peer sent it: the reserved keys and the scheme's namespaced one
    assert AlwaysChallenge.ANSWERS == [{"challenge": {"nonce": "n1", "audience": "bob", "wiring/extra": "no"}}]
    assert turns == []


async def test_a_challenge_for_another_audience_is_not_answered(slim_node: str) -> None:
    """Kills: a holder that signs a challenge without checking the audience it was issued for."""
    from wiring_schemes import MisaudiencedExchange

    async def run_turn(agent_id: str, task: str, *rest: Any) -> str:
        raise AssertionError("the turn must not run")

    handle = _exchange(slim_node, "wiring_pe_misaudience", run_turn)
    try:
        caller = handle.link("alice")
        handle.link("bob")
        with bind({"actor": "alice"}):
            with pytest.raises(AgentCommError, match="^identity: refused:wrong_audience$"):
                await caller.asend("bob", "hi")
    finally:
        handle.close()
    assert len(MisaudiencedExchange.VERIFIES) == 1  # nothing was sent the second time


async def test_an_observed_presentation_cannot_be_replayed(slim_node: str) -> None:
    """Kills: a verifier that does not spend the nonce."""
    turns: list[str] = []

    async def run_turn(agent_id: str, task: str, *rest: Any) -> str:
        turns.append(task)
        return f"{agent_id}:{task}"

    handle = _exchange(slim_node, "wiring_pe_replaying", run_turn)
    try:
        caller = handle.link("alice")
        handle.link("bob")
        with bind({"actor": "alice"}):
            assert await caller.asend("bob", "first") == "bob:first"
            with pytest.raises(AgentCommError, match="^identity: refused:unauthorized$"):
                await caller.asend("bob", "second")
    finally:
        handle.close()
    assert turns == ["first"]


async def test_a_streaming_send_is_gated_like_a_plain_one(slim_node: str) -> None:
    """Kills: a gate that only guards ``SendMessage`` (the agent would run for an unverified streaming caller)."""
    from a2a.helpers import new_text_message
    from a2a.types.a2a_pb2 import Role, SendMessageRequest, TaskState
    from library_ioa.plugins.a2a.agentcard import slimrpc_agent_card
    from library_ioa.plugins.a2a.layer import BINDING, A2ALayer
    from slima2a.client_transport import ClientConfig, MultiAgentClientFactory, slimrpc_channel_factory

    turns: list[str] = []

    async def run_turn(agent_id: str, task: str, *rest: Any) -> str:
        turns.append(task)
        return "never"

    params = {"identity": "id1", "scheme": "wiring_bearer", "params": {"token": "ok"}}
    handle = _handle(slim_node, params, run_turn)
    try:
        caller = handle.link("caller")
        handle.link("callee")
        layer = next(item for item in caller._stack.layers if isinstance(item, A2ALayer))
        link = layer._slim_link(layer._require())
        _, app, conn_id = link.rpc_endpoint()

        async def stream() -> list[int]:
            factory = MultiAgentClientFactory(
                ClientConfig(
                    supported_protocol_bindings=[BINDING],
                    streaming=True,
                    slimrpc_channel_factory=slimrpc_channel_factory(app, conn_id),
                )
            )
            client = factory.create(card=slimrpc_agent_card("callee", link.rpc_name("callee")))
            # no credential at all: the caller bypasses the identity layer of its own stack
            request = SendMessageRequest(message=new_text_message("hi", role=Role.ROLE_USER))
            states: list[int] = []
            try:
                async for response in client.send_message(request=request):
                    if response.WhichOneof("payload") == "task":
                        states.append(response.task.status.state)
            except Exception as exc:
                states.append(-1)  # an RPC error is as good as a refusal
                seen.append(type(exc).__name__)
            return states

        seen: list[str] = []
        states = await link.actor.asubmit(stream(), timeout=15)
    finally:
        handle.close()
    assert turns == []
    assert TaskState.TASK_STATE_COMPLETED not in states


# --- the serialized challenge and its carry cross slimrpc as they cross HTTP --------------------------------------


def _wire_exchange(slim_node: str, run_turn: Any) -> StackHandle:
    params = {"identity": "id1", "scheme": "wiring_wire_exchange", "params": {}, "audience": BOB}
    return _handle(slim_node, params, run_turn)


async def test_the_wire_challenge_crosses_slimrpc_and_the_holder_answers_from_it(slim_node: str) -> None:
    """Kills: an UNAUTHENTICATED answer that serializes only the structured challenge (the holder gets no ``wire`` and
    cannot answer), however long the wire is (up to the contract's cap)."""
    from wiring_schemes import WireExchange

    turns: list[str] = []

    async def run_turn(agent_id: str, task: str, *rest: Any) -> str:
        turns.append(task)
        return f"{agent_id}:{task}"

    handle = _wire_exchange(slim_node, run_turn)
    try:
        caller = handle.link("alice")
        handle.link("bob")
        with bind({"actor": "alice"}):
            assert await caller.asend("bob", "hi") == "bob:hi"
    finally:
        handle.close()
    assert turns == ["hi"]
    (peer,) = WireExchange.PEERS
    assert peer["wire"] == WireExchange.WIRE and peer["challenge"] == {"nonce": "n1", "audience": "bob"}


async def test_an_advisory_hint_crosses_slimrpc_to_the_caller_with_no_nonce(slim_node: str) -> None:
    """Kills: an advisory challenge (a static hint, no nonce) lost on the slimrpc carry: the caller got a bare
    UNAUTHENTICATED, or a hint with a nonce the verifier never made."""
    from wiring_schemes import AdvisoryCaller

    turns: list[str] = []

    async def run_turn(agent_id: str, task: str, *rest: Any) -> str:
        turns.append(task)
        return "never"

    params = {"identity": "id1", "scheme": "wiring_advisory_caller", "params": {}, "audience": BOB}
    handle = _handle(slim_node, params, run_turn)
    try:
        caller = handle.link("alice")
        handle.link("bob")
        with bind({"actor": "alice"}):
            with pytest.raises(AgentCommError, match="^identity: refused:unauthorized$"):
                await caller.asend("bob", "hi")
    finally:
        handle.close()
    assert turns == []
    (peer,) = AdvisoryCaller.ANSWERS  # the holder was told the hint once, then its second try was refused
    assert peer["challenge"] == dict(AdvisoryCaller.HINT) and "nonce" not in peer["challenge"]


def test_the_status_message_round_trips_challenge_wire_and_carry_and_survives_garbage() -> None:
    """Kills: a message that drops one of the three, or a parse that trusts a malformed or oversized one."""
    from library_ioa.plugins.a2a.layer import challenge_from_message, challenge_message

    carry = {"a2a.metadata_key": "k", "http.auth_scheme": "Wiring"}
    text = challenge_message({"nonce": "n"}, '{"foreign":1}', carry)
    assert challenge_from_message(text) == ({"nonce": "n"}, '{"foreign":1}', carry)
    assert challenge_message({}, None, None) == "unauthenticated"
    assert challenge_from_message("unauthenticated") == ({}, None, {})
    assert challenge_from_message("unauthenticated:{not json") == ({}, None, {})
    assert challenge_from_message("unauthenticated:[1]") == ({}, None, {})
    assert challenge_from_message("unauthenticated:" + '{"wire":"' + "x" * 40000 + '"}') == ({}, None, {})
    assert challenge_from_message('unauthenticated:{"wire":1,"carry":3,"challenge":"s"}') == ({}, None, {})


# --- the A2A same-task handshake over slimrpc ---------------------------------------------------------------------


async def _serve_same_task_peer(handle: StackHandle, executor: Any) -> None:
    """Make ``bob`` of ``handle`` a peer that answers auth-required on the task, through the SDK request handler and
    the slimrpc servicer, with no gate: a foreign peer that wants its proof on the same task."""
    import asyncio

    from library_ioa.plugins.a2a.agentcard import slimrpc_agent_card
    from library_ioa.plugins.a2a.layer import A2ALayer
    from library_ioa.plugins.a2a.server.app import build_request_handler
    from slima2a.handler import SRPCHandler
    from slima2a.types.v1.a2a_pb2_slimrpc import add_A2AServiceServicer_to_server

    bob = handle.link("bob")
    layer = next(item for item in bob._stack.layers if isinstance(item, A2ALayer))
    link = layer._slim_link(layer._require())

    async def start() -> None:
        import slim_bindings as sb

        _, app, conn_id = link.rpc_endpoint()
        url = link.rpc_name()
        name = sb.Name(*url.split("/"))
        await app.subscribe_async(name, conn_id)
        card = slimrpc_agent_card("bob", url)
        layer._request_handler = build_request_handler(card, executor)
        server = sb.Server.new_with_connection(app, name, conn_id)
        add_A2AServiceServicer_to_server(SRPCHandler(card, layer._request_handler), server)
        layer._server = server
        layer._serving = asyncio.get_running_loop().create_task(server.serve_async())

    await link.actor.asubmit(layer._astop_server(), timeout=10)  # the stack's own server gives its name up
    await link.actor.asubmit(start(), timeout=10)


async def test_a_same_task_handshake_works_over_slimrpc_for_the_presentation_exchange_double(slim_node: str) -> None:
    """Kills: a slimrpc client that cannot answer an auth-required task on its own task (the send fails, or a second
    task is opened), or a hook whose answer never reaches the peer's verifier."""
    from test_wiring_handshake import HOLDER, PeerVerifier, _AuthExecutor

    verifier = PeerVerifier()
    executor = _AuthExecutor(verifier, "bob")

    async def run_turn(agent_id: str, task: str, *rest: Any) -> str:
        raise AssertionError("the caller's own turn does not run")

    params = {"identity": "id1", "scheme": "presentation_exchange", "params": HOLDER, "audience": BOB}
    handle = _handle(slim_node, params, run_turn)
    try:
        caller = handle.link("alice")
        await _serve_same_task_peer(handle, executor)
        with bind({"actor": "alice"}):
            reply = await caller.asend("bob", "work")
    finally:
        handle.close()
    assert "final-result" in str(reply)
    assert len(executor.task_ids) == 2 and executor.task_ids[0] == executor.task_ids[1] != ""  # one task, one follow-up
    assert len(verifier.admitted) == 1
