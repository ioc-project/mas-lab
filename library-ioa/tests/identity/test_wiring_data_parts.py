#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The data parts of an inbound A2A message reach the verifier under one reserved ``Inbound.meta`` key, on both paths.

A scheme whose answer is a data part (a handshake answer, a presentation) reads it there. Each test names the mutation
it kills.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from library_ioa.plugins.a2a.data_parts import DATA_PARTS_KEY, MAX_DATA_PART_BYTES, MAX_DATA_PARTS
from mas.library.standard.lib.identity.principal import bind, current_principal
from mas.library.standard.lib.stack import StackHandle
from mas.library.standard.plugins.identity.layer import IdentityLayer
from wiring_schemes import DataAnswer
from wiring_servers import serve_a2a

SECRET = "e4aaecb9ae0b23b82086bb8a8633e01fba16ae8d9c1379a613c00838"
HEADERS = {"content-type": "application/json", "a2a-version": "1.0"}


def _layer() -> IdentityLayer:
    return IdentityLayer(
        identity="srv",
        scheme="wiring_data_answer",
        params={},
        agent_id="srv-agent",
        audience={"protocol": "a2a", "target": "srv"},
    )


def _body(parts: list[dict[str, Any]], metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {
        "messageId": "msg-1",
        "contextId": "ctx-1",
        "role": "ROLE_USER",
        "parts": [{"text": "hi"}, *parts],
    }
    if metadata:
        message["metadata"] = metadata
    return {"jsonrpc": "2.0", "id": "1", "method": "SendMessage", "params": {"message": message}}


async def _post(parts: list[dict[str, Any]], metadata: dict[str, Any] | None = None) -> tuple[httpx.Response, list]:
    runs: list[dict[str, Any]] = []

    def handler(prompt: str, **_: Any) -> dict[str, str]:
        runs.append(dict(current_principal()))
        return {"text": "ok"}

    with serve_a2a(handler, inbound=_layer().serve) as peer:
        async with httpx.AsyncClient(base_url=peer.url) as client:
            response = await client.post("/", json=_body(parts, metadata), headers=HEADERS)
    return response, runs


async def test_over_http_the_data_part_reaches_the_verifier_and_the_agent_runs_as_who_it_found() -> None:
    """Kills: an HTTP inbound path that hands a verifier only text, headers and metadata."""
    response, runs = await _post([{"data": {"answer": "ok", "subject": "carol"}}])
    assert response.status_code == 200 and "error" not in response.json()
    assert DataAnswer.SEEN == [{DATA_PARTS_KEY: [{"answer": "ok", "subject": "carol"}]}]
    assert runs[0]["subject"] == "carol"


async def test_a_part_the_verifier_refuses_is_a_denial_and_the_agent_never_runs() -> None:
    """Kills: a verifier answer that is ignored; a refusal that lets the agent run."""
    response, runs = await _post([{"data": {"answer": "no"}}])
    assert response.status_code == 401 and runs == []
    assert "no" not in json.dumps(response.json().get("error", {}).get("challenge", {}))


async def test_no_data_part_means_no_key() -> None:
    """Kills: a key that is always there (a verifier cannot tell a missing answer from an empty one)."""
    response, runs = await _post([])
    assert response.status_code == 401 and runs == []
    assert DATA_PARTS_KEY not in DataAnswer.SEEN[0]


@pytest.mark.parametrize(
    "parts",
    [
        [{"data": [1, 2, 3]}],
        [{"data": "scalar"}],
        [{"data": {"pad": "x" * (MAX_DATA_PART_BYTES + 1)}}],
        [{"data": {"answer": "ok"}}] * (MAX_DATA_PARTS + 1),
    ],
    ids=["array", "scalar", "oversized", "too-many"],
)
async def test_a_malformed_part_is_refused_before_the_verifier_with_a_value_free_error(parts: list[Any]) -> None:
    """Kills: a verifier that is handed an unbounded or non-object part; an error that echoes it."""
    response, runs = await _post(parts)
    assert response.status_code == 400 and runs == [] and DataAnswer.SEEN == []
    assert response.json() == {"error": {"code": "invalid_data_part"}}


async def test_a_reserved_key_a_caller_sends_in_its_own_metadata_is_not_the_transports() -> None:
    """Kills: a caller that forges the transport's reading (the key it sent reaches the verifier)."""
    forged = {DATA_PARTS_KEY: [{"answer": "ok", "subject": "mallory"}], "other/key": "kept", "plain": "dropped"}
    response, runs = await _post([], forged)
    assert response.status_code == 401 and runs == []
    assert DataAnswer.SEEN == [{"other/key": "kept"}]
    response, runs = await _post([{"data": {"answer": "ok", "subject": "carol"}}], forged)
    assert response.status_code == 200 and runs[0]["subject"] == "carol"
    assert DataAnswer.SEEN[-1][DATA_PARTS_KEY] == [{"answer": "ok", "subject": "carol"}]


# --- slimrpc ---------------------------------------------------------------------------------------------------


def _handle(slim_node: str, run_turn: Any) -> StackHandle:
    slim = {
        "type": "slim",
        "params": {"url": slim_node, "identity": "acme/lab/wiring", "secret": SECRET, "timeout_s": 5.0},
    }
    identity = {
        "identity": "srv",
        "scheme": "wiring_data_answer",
        "params": {},
        "audience": {"protocol": "a2a", "target": "callee"},
    }
    return StackHandle("wiring", [slim, {"type": "a2a-rpc"}, {"type": "identity", "params": identity}], run_turn)


async def _send_raw(handle: StackHandle, parts: list[dict[str, Any]], metadata: dict[str, Any] | None = None) -> str:
    """One message with ``parts`` to the callee over slimrpc, straight through the SDK client (no identity layer)."""
    from a2a.helpers import new_data_part, new_message, new_text_part
    from a2a.types.a2a_pb2 import Role, SendMessageRequest
    from library_ioa.plugins.a2a.agentcard import slimrpc_agent_card
    from library_ioa.plugins.a2a.layer import BINDING, A2ALayer
    from slima2a.client_transport import ClientConfig, MultiAgentClientFactory, slimrpc_channel_factory

    caller = handle.link("caller")
    handle.link("callee")
    layer = next(item for item in caller._stack.layers if isinstance(item, A2ALayer))
    link = layer._slim_link(layer._require())
    _, app, conn_id = link.rpc_endpoint()

    async def send() -> str:
        factory = MultiAgentClientFactory(
            ClientConfig(
                supported_protocol_bindings=[BINDING],
                streaming=False,
                slimrpc_channel_factory=slimrpc_channel_factory(app, conn_id),
            )
        )
        client = factory.create(card=slimrpc_agent_card("callee", link.rpc_name("callee")))
        message = new_message([new_text_part("hi"), *(new_data_part(p) for p in parts)], role=Role.ROLE_USER)
        if metadata:
            message.metadata.update(metadata)
        try:
            async for _ in client.send_message(request=SendMessageRequest(message=message)):
                pass
        except Exception as exc:
            return type(exc).__name__ + ":" + str(getattr(exc, "message", ""))
        return "ok"

    return await link.actor.asubmit(send(), timeout=15)


async def test_over_slimrpc_the_data_part_reaches_the_verifier_and_the_agent_runs_as_who_it_found(
    slim_node: str,
) -> None:
    """Kills: a slimrpc inbound path that drops the data parts (the verifier never sees the answer)."""
    seen: list[dict[str, str]] = []

    async def run_turn(agent_id: str, task: str, *rest: Any) -> str:
        seen.append(dict(current_principal()))
        return "done"

    handle = _handle(slim_node, run_turn)
    try:
        with bind({"actor": "caller"}):
            assert await _send_raw(handle, [{"answer": "ok", "subject": "carol"}]) == "ok"
    finally:
        handle.close()
    assert DataAnswer.SEEN == [{DATA_PARTS_KEY: [{"answer": "ok", "subject": "carol"}]}]
    assert seen[0]["subject"] == "carol"


async def test_over_slimrpc_a_refused_or_malformed_part_never_runs_the_agent(slim_node: str) -> None:
    """Kills: a refused part that lets the turn run; a malformed one that reaches the verifier."""
    turns: list[str] = []

    async def run_turn(agent_id: str, task: str, *rest: Any) -> str:
        turns.append(task)
        return "never"

    handle = _handle(slim_node, run_turn)
    try:
        refused = await _send_raw(handle, [{"answer": "no"}])
        seen_before = len(DataAnswer.SEEN)
        oversized = await _send_raw(handle, [{"pad": "x" * (MAX_DATA_PART_BYTES + 1)}])
        too_many = await _send_raw(handle, [{"answer": "ok"}] * (MAX_DATA_PARTS + 1))
        forged = await _send_raw(handle, [], {DATA_PARTS_KEY: [{"answer": "ok"}]})
    finally:
        handle.close()
    assert turns == []
    assert all(r != "ok" for r in (refused, oversized, too_many, forged))
    assert len(DataAnswer.SEEN) == seen_before + 1  # only the forged one reached the verifier, and without the key
    assert DATA_PARTS_KEY not in DataAnswer.SEEN[-1]
