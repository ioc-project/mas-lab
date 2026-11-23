#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""What a challenge carries and how it is read: the task id, the serialized ``wire``, one peer shape, A2A over HTTP."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from a2a.types.a2a_pb2 import TaskState
from library_ioa.plugins.a2a.handshake import challenge_peer
from library_ioa.plugins.a2a.server.executor import MasLabAgentExecutor
from mas.library.standard.lib.identity.principal import bind, current_principal
from mas.library.standard.lib.stack import Envelope, PeerChallenge
from mas.library.standard.plugins.identity.layer import IdentityLayer, _task_name
from wiring_builders import a2a_stack, envelope
from wiring_schemes import HttpChallenge, InboundRecorder
from wiring_servers import serve_a2a

# --- the task id of an inbound request (nonce binding for a follow-up with a new message id) ----------------------


async def test_an_inbound_request_on_an_open_task_carries_its_task_id_to_the_verifier() -> None:
    """Kills: ``Inbound.task_id`` never filled (a verifier could bind a nonce only to the message id)."""
    layer = IdentityLayer(identity="srv", scheme="wiring_inbound_recorder", params={}, agent_id="a")

    async def handler(env: Envelope) -> str:
        return "ok"

    serve = layer.serve(handler)
    await serve(Envelope(payload="x", meta={"context_id": "c1", "message_id": "m1", "task_id": "t-open"}))
    await serve(Envelope(payload="x", meta={"context_id": "c1", "message_id": "m2"}))
    first, second = InboundRecorder.SEEN
    assert first["task_id"] == "t-open" and first["request_id"] == "m1" and first["session"] == "c1"
    assert "task_id" not in second


async def test_the_http_gate_reads_the_task_a_follow_up_names() -> None:
    layer = IdentityLayer(identity="srv", scheme="wiring_inbound_recorder", params={}, agent_id="a")
    body = {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "SendMessage",
        "params": {"message": {"messageId": "m9", "taskId": "t-named", "role": "ROLE_USER", "parts": [{"text": "hi"}]}},
    }
    with serve_a2a(lambda p, **_: {"text": p}, inbound=layer.serve) as peer:
        async with httpx.AsyncClient(base_url=peer.url) as client:
            await client.post("/", json=body, headers={"a2a-version": "1.0"})
    assert InboundRecorder.SEEN and InboundRecorder.SEEN[0]["task_id"] == "t-named"


# --- the serialized challenge a peer expects, carried verbatim ----------------------------------------------------


async def test_a_wire_challenge_is_carried_verbatim_on_http_and_on_an_a2a_status() -> None:
    """Kills: ``wire`` dropped between the verifier and the peer."""
    from wiring_schemes import WireVerifier

    layer = IdentityLayer(identity="srv", scheme="wiring_wire_verifier", params={}, agent_id="a")
    with serve_a2a(lambda p, **_: {"text": p}, inbound=layer.serve) as peer:
        body = {
            "jsonrpc": "2.0",
            "id": "1",
            "method": "SendMessage",
            "params": {"message": {"messageId": "m", "role": "ROLE_USER", "parts": [{"text": "hi"}]}},
        }
        async with httpx.AsyncClient(base_url=peer.url) as client:
            refused = await client.post("/", json=body, headers={"a2a-version": "1.0"})
    assert refused.status_code == 401
    assert refused.json()["error"]["wire"] == WireVerifier.WIRE
    assert refused.headers["www-authenticate"] == f"Wiring {WireVerifier.WIRE}"


class _Queue:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def enqueue_event(self, event: Any) -> None:
        self.events.append(event)


async def _auth_required_status(refused: PeerChallenge) -> Any:
    from types import SimpleNamespace

    queue = _Queue()
    context = SimpleNamespace(context_id="c", task_id="t", current_task=None)
    await MasLabAgentExecutor(lambda *a, **k: None)._emit_auth_required(context, queue, refused)
    (event,) = queue.events
    return event.status


async def test_an_auth_required_task_carries_the_wire_under_the_carry_key() -> None:
    wire = '{"foreign":"shape"}'
    status = await _auth_required_status(PeerChallenge({"nonce": "n"}, carry={"a2a.metadata_key": "k"}, wire=wire))
    assert status.state == TaskState.TASK_STATE_AUTH_REQUIRED
    assert dict(status.message.metadata) == {"k": wire}
    status = await _auth_required_status(PeerChallenge({"nonce": "n"}, carry={"a2a.data_key": "d"}, wire=wire))
    assert [dict(p.data.struct_value) if p.HasField("data") else None for p in status.message.parts] == [{"d": wire}]


def test_a_namespaced_extra_challenge_key_is_not_dropped_by_the_layer() -> None:
    """Kills: the layer sending only the reserved keys (the contract allows namespaced ones)."""
    import asyncio

    from mas.library.standard.lib.stack import Envelope as E

    layer = IdentityLayer(identity="srv", scheme="wiring_always_challenge", params={}, agent_id="a")

    async def go() -> PeerChallenge:
        async def handler(env: E) -> str:
            return "never"

        with pytest.raises(PeerChallenge) as caught:
            await layer.serve(handler)(E(payload="x", meta={"context_id": "c"}))
        return caught.value

    refused = asyncio.run(go())
    assert refused.challenge["wiring/extra"] == "no" and refused.challenge["nonce"] == "n1"


# --- one challenge-phase peer shape --------------------------------------------------------------------------------


def test_the_a2a_status_message_becomes_the_one_peer_shape() -> None:
    peer = challenge_peer({"challenge": {"nonce": "n"}}, {})
    assert peer == {"challenge": {"nonce": "n"}, "transport": {"data": {"challenge": {"nonce": "n"}}, "metadata": {}}}
    peer = challenge_peer({}, {"their-key": '{"foreign":"shape"}'})
    assert peer["challenge"] == {} and peer["wire"] == '{"foreign":"shape"}'
    assert set(peer) == {"challenge", "wire", "transport"}


async def test_a_401_challenge_phase_peer_has_challenge_wire_and_transport() -> None:
    from wiring_builders import chat, llm_provider
    from wiring_schemes import TokenPerTask
    from wiring_servers import FakeLlm, Reply

    def gate(request: Any) -> Reply | None:
        if request.headers.get("authorization", "").startswith("Bearer chal"):
            return None
        return Reply(401, headers={"WWW-Authenticate": 'Bearer realm="r", nonce="abc"'}, body={})

    with FakeLlm(gate=gate) as server:
        provider, transport = llm_provider(server.url, "wiring_token")
        try:
            with bind({"actor": "agent-a"}):
                await chat(provider)
        finally:
            transport.stop()
    (challenge,) = [c for c in TokenPerTask.CALLS if c.get("phase") == "challenge"]
    peer = challenge["peer"]
    assert set(peer) == {"challenge", "wire", "transport"}
    assert peer["challenge"] == {"realm": "r", "nonce": "abc"}
    assert peer["transport"]["status"] == 401


# --- A2A over HTTP: both sides use the identity layer ----------------------------------------------------------------


async def test_a2a_over_http_a_401_with_a_challenge_is_answered_once_end_to_end() -> None:
    """Kills: an HTTP A2A client that never answers a 401 challenge (the server answered one, the caller failed)."""
    server_layer = IdentityLayer(identity="srv", scheme="wiring_http_challenge", params={}, agent_id="srv-agent")
    runs: list[dict[str, str]] = []

    def handler(prompt: str, **_: Any) -> dict[str, str]:
        runs.append(dict(current_principal()))
        return {"text": f"peer:{prompt}"}

    with serve_a2a(handler, inbound=server_layer.serve) as peer:
        stack, link, _ = a2a_stack(peer.url, "wiring_http_challenge")
        try:
            with bind({"actor": "agent-a"}):
                reply = await stack.asend(envelope("hello"))
        finally:
            link.stop()
        posts = [s for s in peer.sends() if s.method == "POST"]
    assert "peer:hello" in str(reply)
    assert runs == [{"actor": "srv-agent", "subject": "holder-1", "session": runs[0]["session"]}]
    assert len(posts) == 2  # the first send, and the one bounded retry
    (answer,) = HttpChallenge.PEERS
    assert answer["challenge"]["nonce"] and set(answer) <= {"challenge", "wire", "transport"}
    assert posts[1].headers["authorization"].startswith("Bearer proof:")


async def test_a2a_over_http_a_peer_that_keeps_refusing_is_asked_once_and_then_denied() -> None:
    from mas.runtime.boundary.agentcomm.protocol import AgentCommError

    server_layer = IdentityLayer(identity="srv", scheme="wiring_always_challenge", params={}, agent_id="s")
    with serve_a2a(lambda p, **_: {"text": p}, inbound=server_layer.serve) as peer:
        stack, link, _ = a2a_stack(peer.url, "wiring_always_challenge")
        try:
            with (
                bind({"actor": "agent-a"}),
                pytest.raises(AgentCommError, match="^identity: refused:unauthorized$"),
            ):
                await stack.asend(envelope("hello"))
        finally:
            link.stop()
        assert len([s for s in peer.sends() if s.method == "POST"]) == 2


async def test_without_an_identity_layer_a_401_keeps_its_old_error_not_a_challenge_retry() -> None:
    from library_ioa.plugins.a2a.agent_comm import A2AAgentComm
    from mas.library.standard.lib.stack import Unauthenticated
    from mas.runtime.boundary.agentcomm.protocol import AgentCommError

    server_layer = IdentityLayer(identity="srv", scheme="wiring_always_challenge", params={}, agent_id="s")
    with serve_a2a(lambda p, **_: {"text": p}, inbound=server_layer.serve) as peer:
        comm = A2AAgentComm(url=peer.url, timeout=10)
        try:
            with pytest.raises(AgentCommError) as caught:
                await comm.asend("peer", "hello")
        finally:
            comm.stop()
    assert not isinstance(caught.value, Unauthenticated)


# --- the operation a send names ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("skills", "named", "expected"),
    [([], "", "task"), (["one"], "", "one"), (["one", "two"], "", "task"), (["one"], "explicit", "explicit")],
)
def test_the_operation_is_the_peers_one_skill_else_the_intent(skills: list[str], named: str, expected: str) -> None:
    meta: dict[str, Any] = {"intent": "task"}
    if named:
        meta["task"] = named
    assert _task_name(Envelope(payload="x", meta=meta), {"skills": skills}) == expected


async def test_the_holder_gets_the_same_peer_over_http_as_over_slimrpc() -> None:
    """Kills: a transport on which the holder's view of a ``wire`` challenge differs (slimrpc is pinned the same)."""
    from wiring_schemes import WireExchange

    server = IdentityLayer(identity="srv", scheme="wiring_wire_exchange", params={}, agent_id="srv")
    with serve_a2a(lambda p, **_: {"text": p}, inbound=server.serve) as peer:
        _, link, holder = a2a_stack(peer.url, "wiring_wire_exchange", {})
        stack = _stack(link, holder)
        try:
            with bind({"actor": "alice"}):
                assert await stack.asend(envelope("hi")) == "hi"
        finally:
            link.stop()
    (seen,) = WireExchange.PEERS
    assert seen["wire"] == WireExchange.WIRE and seen["challenge"] == {"nonce": "n1"}


def _stack(link: Any, layer: Any) -> Any:
    from mas.library.standard.lib.stack import Stack
    from mas.library.standard.lib.stack.legacy import LinkLayer

    return Stack([LinkLayer(link), layer])


async def test_identity_evidence_never_goes_to_an_origin_the_peers_card_names() -> None:
    """Kills: a peer's agent card that redirects the SDK's POSTs (and so the evidence on them) to another host."""
    import httpx
    from library_ioa.plugins.a2a.client import A2AClient, _Connection
    from mas.runtime.boundary.agentcomm.protocol import AgentCommError

    owner = A2AClient(url="http://peer.test:9000", allowed_origins=["http://also.test"])
    connection = _Connection(owner)
    try:
        connection.watch_unauthorized()  # a layer above is on this connection
        guard = connection._same_origin_only
        await guard(httpx.Request("POST", "http://peer.test:9000/rpc"))
        await guard(httpx.Request("POST", "http://also.test/rpc"))
        for elsewhere in ("http://evil.test/rpc", "http://peer.test:9001/rpc", "https://peer.test:9000/x"):
            with pytest.raises(AgentCommError, match="refusing to send identity evidence"):
                await guard(httpx.Request("POST", elsewhere))
        assert connection._same_origin_only in connection._http.event_hooks["request"]
    finally:
        await connection.aclose()


async def test_no_identity_evidence_over_an_insecure_grpc_channel() -> None:
    """Kills: a layer above that presents over the SDK's plaintext gRPC channel."""
    from library_ioa.plugins.a2a.client import A2AClient, _Connection
    from mas.runtime.boundary.agentcomm.protocol import AgentCommError

    connection = _Connection(A2AClient(url="http://peer.test:9000", transport="GRPC"))
    try:
        connection.watch_unauthorized()
        with pytest.raises(AgentCommError, match="insecure gRPC"):
            await connection.client(False)
    finally:
        await connection.aclose()
