#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Inbound verification on the A2A HTTP exposure, and the principal across the sync-plugin worker thread."""

from __future__ import annotations

import json
from typing import Any

import httpx
from a2a.helpers import new_text_message
from a2a.types.a2a_pb2 import Role, SendMessageRequest
from google.protobuf.json_format import MessageToDict
from mas.library.standard.lib.identity.principal import bind, current_principal
from mas.library.standard.lib.stack import Layer, Stack
from mas.library.standard.lib.stack.legacy import LinkLayer
from mas.library.standard.plugins.identity.layer import IdentityLayer
from mas.library.standard.testing.identity_presentation_exchange import PRESENTATION_HEADER, PresentationExchange
from mas.runtime.aio import await_on_loop, new_thread
from wiring_builders import a2a_stack, envelope, key_ref
from wiring_servers import serve_a2a

KEY = "k" * 16
AUD = "srv-aud"


class _Capture:
    def __init__(self) -> None:
        self.params: dict[str, Any] = {}

    async def request(self, method: str, params: Any) -> dict[str, Any]:
        self.params = dict(params)
        return {"session": "unused"}


async def _proof(challenge: dict[str, Any]) -> str:
    holder = PresentationExchange.from_spec({"key": key_ref(KEY), "subject": "holder-1", "claims": {"tier": "gold"}})
    channel = _Capture()
    await holder.handshake(
        {
            "record": "h",
            "principal": {},
            "audience": {"protocol": "a2a", "target": AUD},
            "task": "",
            "peer": {"challenge": challenge},
            "channel": channel,
            "now": 0.0,
        }
    )
    return json.dumps(channel.params)


def _body(metadata: dict[str, str] | None = None) -> dict[str, Any]:
    # the verifier binds a challenge to one message, so every post of a test is the same message
    message = new_text_message("hi", context_id="ctx-1", role=Role.ROLE_USER)
    message.message_id = "msg-1"
    if metadata:
        message.metadata.update(metadata)
    params = MessageToDict(SendMessageRequest(message=message))
    return {"jsonrpc": "2.0", "id": "1", "method": "SendMessage", "params": params}


async def test_inbound_verify_failure_is_401_and_the_handler_never_runs_then_a_valid_caller_is_bound() -> None:
    layer = IdentityLayer(
        identity="srv",
        scheme="presentation_exchange",
        params={"key": key_ref(KEY), "subject": "verifier", "requirements": ["tier"], "audience": AUD},
        agent_id="srv-agent",
        audience={"protocol": "a2a", "target": AUD},
    )
    runs: list[dict[str, Any]] = []

    def handler(prompt: str, **_: Any) -> dict[str, str]:
        runs.append(dict(current_principal()))
        return {"text": "ok"}

    with serve_a2a(handler, inbound=layer.serve) as peer:
        headers = {"content-type": "application/json", "a2a-version": "1.0"}
        async with httpx.AsyncClient(base_url=peer.url) as client:
            refused = await client.post("/", json=_body(), headers=headers)
            assert refused.status_code == 401
            assert refused.headers["www-authenticate"].startswith("Challenge nonce=")
            challenge = refused.json()["error"]["challenge"]
            assert runs == []
            proof = await _proof(challenge)
            spoof = {"x-sender": "evil", "x-subject": "evil"}
            ok = await client.post(
                "/",
                json=_body({"mas.sender": "evil", "sender": "evil"}),
                headers={**headers, PRESENTATION_HEADER: proof, **spoof},
            )
            assert ok.status_code == 200 and "error" not in ok.json()
            assert len(runs) == 1 and (runs[0]["actor"], runs[0]["subject"]) == ("srv-agent", "holder-1")
            # the session of the bound principal is the caller's context, made the verified owner's own
            assert runs[0]["session"].startswith("own.") and "ctx-1" not in runs[0]["session"]  # derived from the owner
            replay = await client.post("/", json=_body(), headers={**headers, PRESENTATION_HEADER: proof})
            assert replay.status_code == 401
            assert len(runs) == 1
            card = await client.get("/.well-known/agent-card.json")
            assert card.status_code == 200  # reading the card is not gated


class _SyncTop(Layer):
    """A layer written sync, above the identity layer: its ``send`` runs on a worker thread."""

    def send(self, envelope_: Any) -> Any:
        return await_on_loop(self._require().asend(envelope_))


async def test_the_principal_reaches_the_layer_through_a_sync_plugin_worker_thread() -> None:
    with serve_a2a(lambda p, **_: {"text": p}) as peer:
        _, link, identity = a2a_stack(peer.url, "wiring_token", {"prefix": "p"})
        stack = Stack([LinkLayer(link), identity, _SyncTop()])
        try:
            with bind({"actor": "agent-a", "subject": "user-1"}):
                assert await stack.asend(envelope("via-worker")) == "via-worker"
        finally:
            link.stop()
        (seen,) = peer.sends()
    assert seen.headers["authorization"] == "Bearer p:agent-a:peer"


def test_a_thread_the_runtime_starts_has_no_principal_so_it_is_taken_explicitly() -> None:
    from mas.library.standard.lib.identity.principal import CapturedPrincipal

    got: dict[str, Any] = {}

    def work(captured: CapturedPrincipal) -> None:
        got["bare"] = dict(current_principal())
        with captured.bind():
            got["taken"] = dict(current_principal())

    with bind({"actor": "agent-a"}):
        captured = CapturedPrincipal(current_principal())
        thread = new_thread(target=lambda: work(captured))
        thread.start()
        thread.join()
    assert got == {"bare": {}, "taken": {"actor": "agent-a"}}


async def test_message_level_evidence_reaches_the_verifier_and_the_handler_gets_the_text_without_it() -> None:
    """Kills: a layer that hands the wrapped text to the model; one that ignores ``Verified.payload``."""
    layer = IdentityLayer(
        identity="srv",
        scheme="presentation_exchange",
        params={"key": key_ref(KEY), "subject": "verifier", "requirements": ["tier"], "audience": AUD},
        agent_id="srv-agent",
        audience={"protocol": "a2a", "target": AUD},
    )
    holder = PresentationExchange.from_spec({"key": key_ref(KEY), "subject": "holder-1", "claims": {"tier": "gold"}})
    prompts: list[str] = []

    def handler(prompt: str, **_: Any) -> dict[str, str]:
        prompts.append(prompt)
        return {"text": "ok"}

    with serve_a2a(handler, inbound=layer.serve) as peer:
        headers = {"content-type": "application/json", "a2a-version": "1.0"}
        async with httpx.AsyncClient(base_url=peer.url) as client:
            refused = await client.post("/", json=_body(), headers=headers)
            assert refused.status_code == 401 and prompts == []
            challenge = refused.json()["error"]["challenge"]
            evidence = await holder.present(
                {
                    "record": "h",
                    "principal": {"actor": "h"},
                    "audience": {"protocol": "a2a", "target": AUD},
                    "phase": "challenge",
                    "peer": {"challenge": challenge},
                    "request": {"body_text": "hi"},
                    "now": 1_000_000.0,
                }
            )
            body = _body()
            body["params"]["message"]["parts"] = [{"text": evidence["payload"]}]
            ok = await client.post("/", json=body, headers=headers)
            assert ok.status_code == 200 and "error" not in ok.json()
    assert prompts == ["hi"]  # the agent saw the message, not the presentation around it
    assert KEY not in "".join(prompts) and "proof" not in "".join(prompts)
