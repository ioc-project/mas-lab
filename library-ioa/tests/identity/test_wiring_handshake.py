#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Session handshakes end to end: MCP (capability, challenge, present, admitted) and the A2A same-task reply."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import pytest
from a2a.helpers import get_data_parts, new_data_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import Task, TaskState, TaskStatus, TaskStatusUpdateEvent
from mas.library.standard.lib.identity.contract import ChallengeRequired, Inbound
from mas.library.standard.lib.identity.principal import bind
from mas.library.standard.testing.identity_presentation_exchange import (
    META_SESSION,
    PRESENTATION_HEADER,
    SUBMIT_METHOD,
    PresentationExchange,
    sign_presentation,
)
from mas.runtime.boundary.agentcomm.protocol import AgentCommError
from wiring_builders import a2a_stack, close, envelope, identity_step, key_ref, mcp_provider
from wiring_servers import FakeMcp, Refuse, Seen, serve_a2a

KEY = "k" * 16
NOW = 1_000_000.0
HOLDER = {"key": key_ref(KEY), "subject": "holder-1", "claims": {"tier": "gold"}}
EXCHANGE = "presentationexchange/declaration"


def _run(coro: Any) -> Any:
    """Run a coroutine to completion from any context, running loop or not."""
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(1) as pool:
        return pool.submit(asyncio.run, coro).result()


class PeerVerifier:
    """The peer side of a session handshake: a presentation-exchange verifier per audience that issues the challenge
    and admits one submission per challenge. ``admitted`` lists the session ids it handed out."""

    def __init__(self) -> None:
        self._by_audience: dict[str, PresentationExchange] = {}
        self.admitted: list[str] = []

    def _verifier(self, audience: str) -> PresentationExchange:
        if audience not in self._by_audience:
            self._by_audience[audience] = PresentationExchange.from_spec(
                {"key": key_ref(KEY), "subject": "peer", "requirements": ["tier"], "audience": audience}
            )
        return self._by_audience[audience]

    def _inbound(self, audience: str, headers: dict[str, str]) -> Inbound:
        return Inbound(
            direction="request",
            headers=headers,
            audience={"protocol": "mcp", "target": audience},
            session="peer-caller",
            request_id="r1",
            now=NOW,
        )

    def issue(self, audience: str) -> dict[str, Any]:
        try:
            _run(self._verifier(audience).verify(self._inbound(audience, {})))
        except ChallengeRequired as asked:
            return dict(asked.challenge)
        raise AssertionError("a verifier shown nothing must ask")

    def admit(self, answer: dict[str, Any], audience: str, task: str) -> str:
        headers = {PRESENTATION_HEADER: json.dumps(answer)}
        _run(self._verifier(audience).verify(self._inbound(audience, headers)))
        self.admitted.append(f"session-{len(self.admitted) + 1}")
        return self.admitted[-1]


def _answer(fields: dict[str, Any], key: str = EXCHANGE) -> dict[str, Any]:
    """An ``initialize`` answer to the advertised capability ``key``: the server's challenge under the same key."""
    return {"capabilities": {"tools": {}, "extensions": {key: fields}}}


def _challenge_server(verifier: PeerVerifier, sessions: set[str]) -> FakeMcp:
    box: dict[str, FakeMcp] = {}

    def present(rpc: dict[str, Any], request: Seen) -> dict[str, Any]:
        try:
            session = verifier.admit(rpc["params"], box["s"].url, "")
        except Exception as exc:
            raise Refuse(exc.reason) from None  # type: ignore[attr-defined]
        sessions.add(session)
        return {"session": session}

    def gate(request: Seen) -> Any:
        from wiring_servers import Reply

        rpc = request.json()
        if rpc.get("method") == "tools/call":
            meta = rpc["params"].get("_meta") or {}
            if meta.get(META_SESSION) not in sessions:
                return Reply(
                    200, body={"jsonrpc": "2.0", "id": rpc["id"], "error": {"code": -32002, "message": "not_admitted"}}
                )
        return None

    server = FakeMcp(gate=gate, on_method={SUBMIT_METHOD: present})
    box["s"] = server
    return server


async def test_mcp_challenge_handshake_is_admitted_once_per_session() -> None:
    verifier, sessions = PeerVerifier(), set()
    server = _challenge_server(verifier, sessions)
    with server:
        # the peer issues its challenge in the initialize result, bound to its own audience
        server.initialize_extra = _answer({"challenge": verifier.issue(server.url)})
        provider = mcp_provider(server.url, identity_step("presentation_exchange", HOLDER))
        try:
            with bind({"actor": "agent-a"}):
                first = await provider.acall_tool("echo", {})
                second = await provider.acall_tool("other", {})
        finally:
            await close(provider)
        assert first["is_error"] is False and second["is_error"] is False
        assert len(verifier.admitted) == 1
        advertised = server.initializes[0]["capabilities"]["extensions"]
        assert advertised == {"urn:example:presentation-exchange": {"holder": True}}
        sent = {s.json()["params"]["_meta"][META_SESSION] for s in server.posts("tools/call")}
        assert sent == {verifier.admitted[0]}


async def test_mcp_a_hook_refusal_reaches_the_tool_result_as_its_code(caplog: Any) -> None:
    with FakeMcp() as server:
        provider = mcp_provider(server.url, identity_step("wiring_refusing"))
        try:
            with bind({"actor": "agent-a"}):
                result = await provider.acall_tool("echo", {})
        finally:
            await close(provider)
        assert result["is_error"] is True
        assert re.fullmatch(r"identity: refused:xyz", result["error"])
        assert "TaskGroup" not in str(result) and "Traceback" not in str(result)
        assert server.tool_calls == []
    assert "TaskGroup" not in caplog.text and "Traceback" not in caplog.text


async def test_mcp_a_one_shot_session_unwraps_the_hook_refusal_too() -> None:
    from library_ioa.plugins.mcp.client import MCPClient
    from mas.library.standard.lib.stack import SessionHook

    class Refusing(SessionHook):
        async def established(self, session: Any) -> dict[str, Any]:
            raise AgentCommError("identity: refused:xyz")

    with FakeMcp() as server:
        client = MCPClient(url=server.url + "/mcp", session_hooks=[Refusing()])
        with pytest.raises(AgentCommError) as caught:
            await client.call_tool("echo", {})
        assert str(caught.value) == "identity: refused:xyz"


async def test_mcp_a_held_session_gives_later_calls_the_servers_declared_facts() -> None:
    from wiring_schemes import PeerRecording

    with FakeMcp(initialize_extra=_answer({"audience": "srv"}, "wiring/declaration")) as server:
        provider = mcp_provider(server.url, identity_step("wiring_peer"))
        try:
            with bind({"actor": "agent-a"}):
                await provider.acall_tool("echo", {})
                await provider.acall_tool("other", {})
        finally:
            await close(provider)
    seen = PeerRecording.SEEN
    # nothing is known before the first connection; the handshake carries what the server declared
    assert seen[0][0] == "initialize" and not seen[0][2]
    handshake = next(peer for task, phase, peer in seen if phase == "handshake")
    assert handshake["audience"] == "srv"
    calls = [peer for task, _, peer in seen if task.startswith("tools/call:")]
    # each request is presented for once, for its headers (the transport)
    assert len(calls) == 2 and all(peer and peer["audience"] == "srv" for peer in calls)


async def test_mcp_a_session_is_never_shared_between_principals() -> None:
    verifier, sessions = PeerVerifier(), set()
    server = _challenge_server(verifier, sessions)
    with server:
        server.initialize_extra = lambda: _answer({"challenge": verifier.issue(server.url)})
        provider = mcp_provider(server.url, identity_step("presentation_exchange", HOLDER))
        try:
            with bind({"actor": "agent-a"}):
                await provider.acall_tool("echo", {})
            with bind({"actor": "agent-b"}):
                await provider.acall_tool("echo", {})
            with bind({"actor": "agent-b"}):
                await provider.acall_tool("other", {})
        finally:
            await close(provider)
        assert len(verifier.admitted) == 2  # one admission per principal, none for the repeat
        used = [s.json()["params"]["_meta"][META_SESSION] for s in server.posts("tools/call")]
        assert used == [verifier.admitted[0], verifier.admitted[1], verifier.admitted[1]]


async def test_mcp_replay_and_a_copy_without_the_credential_are_refused() -> None:
    verifier, sessions = PeerVerifier(), set()
    server = _challenge_server(verifier, sessions)
    with server:
        challenge = verifier.issue(server.url)
        server.initialize_extra = _answer({"challenge": challenge})
        provider = mcp_provider(server.url, identity_step("presentation_exchange", HOLDER))
        try:
            with bind({"actor": "agent-a"}):
                await provider.acall_tool("echo", {})
        finally:
            await close(provider)
        proof = next(s for s in server.posts(SUBMIT_METHOD)).json()["params"]
        # the observed submission, sent again: the nonce is spent, and the refusal tells nothing
        with pytest.raises(Exception, match="challenge_invalid"):
            verifier.admit(proof, server.url, "")
        # a fresh nonce answered without the key: no admission
        fresh = verifier.issue(server.url)
        claims = {"tier": "gold"}
        forged = {
            "alg": "hmac-sha256",
            "nonce": fresh["nonce"],
            "audience": server.url,
            "subject": "holder-1",
            "claims": claims,
            "proof": sign_presentation("wrong-key-0000", fresh["nonce"], server.url, claims),
        }
        with pytest.raises(Exception, match="bad_proof"):
            verifier.admit(forged, server.url, "")
        assert len(verifier.admitted) == 1


async def test_mcp_presentation_exchange_handshake() -> None:
    holder_params = {"key": key_ref(KEY), "subject": "holder-1", "claims": {"tier": "gold"}}
    admitted: list[str] = []
    box: dict[str, Any] = {}

    def inbound(**extra: Any) -> Inbound:
        return Inbound(
            direction="request",
            audience={"protocol": "mcp", "target": box["server"].url},
            session="mcp-1",
            request_id="r1",
            now=NOW,
            **extra,
        )

    def on_submit(rpc: dict[str, Any], request: Seen) -> dict[str, Any]:
        try:
            verified = asyncio.run(
                box["verifier"].verify(inbound(headers={PRESENTATION_HEADER: json.dumps(rpc["params"])}))
            )
        except Exception as exc:
            raise Refuse(getattr(exc, "reason", "") or "refused") from None
        admitted.append(verified["subject"])
        return {"session": f"s-{verified['subject']}"}

    def gate(request: Seen) -> Any:
        from wiring_servers import Reply

        rpc = request.json()
        if rpc.get("method") == "tools/call" and (rpc["params"].get("_meta") or {}).get(META_SESSION) != "s-holder-1":
            return Reply(
                200, body={"jsonrpc": "2.0", "id": rpc["id"], "error": {"code": -32002, "message": "not_admitted"}}
            )
        return None

    server = FakeMcp(gate=gate, on_method={SUBMIT_METHOD: on_submit})
    box["server"] = server
    with server:
        box["verifier"] = PresentationExchange.from_spec(
            {"key": key_ref(KEY), "subject": "verifier", "requirements": ["tier"], "audience": server.url}
        )
        # the verifier shows nothing yet: it answers with the challenge this session's submission must carry
        try:
            await box["verifier"].verify(inbound(headers={}))
        except ChallengeRequired as asked:
            server.initialize_extra = _answer({"challenge": asked.challenge}, "presentationexchange/declaration")
        provider = mcp_provider(server.url, identity_step("presentation_exchange", holder_params))
        try:
            with bind({"actor": "agent-a"}):
                result = await provider.acall_tool("echo", {})
        finally:
            await close(provider)
        assert result["is_error"] is False
        assert admitted == ["holder-1"]


# --- A2A: auth-required, answered on the same task -----------------------------------------------------------


class _AuthExecutor(AgentExecutor):
    """Answers the first message auth-required with a challenge; the follow-up on that task is the proof."""

    def __init__(self, verifier: PeerVerifier, audience: str, *, stubborn: bool = False) -> None:
        self.verifier = verifier
        self.audience = audience
        self.stubborn = stubborn
        self.task_ids: list[str] = []

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        self.task_ids.append(context.task_id or "")
        task_id, ctx_id = context.task_id or "", context.context_id or ""
        if context.current_task is None:
            status = TaskStatus(
                state=TaskState.TASK_STATE_AUTH_REQUIRED,
                message=new_data_message(
                    {"challenge": self.verifier.issue(self.audience)}, task_id=task_id, context_id=ctx_id
                ),
            )
            await event_queue.enqueue_event(Task(id=task_id, context_id=ctx_id, status=status))
            return
        if self.stubborn:
            status = TaskStatus(
                state=TaskState.TASK_STATE_AUTH_REQUIRED,
                message=new_data_message(
                    {"challenge": self.verifier.issue(self.audience)}, task_id=task_id, context_id=ctx_id
                ),
            )
            await event_queue.enqueue_event(TaskStatusUpdateEvent(task_id=task_id, context_id=ctx_id, status=status))
            return
        (data,) = get_data_parts(context.message.parts)
        session = self.verifier.admit(data["params"], self.audience, "peer")  # the card's one skill
        message = new_data_message({"session": session}, task_id=task_id, context_id=ctx_id)
        message.parts.add().text = "final-result"
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=task_id,
                context_id=ctx_id,
                status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED, message=message),
            )
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        return None


def _peer(executor: _AuthExecutor):
    return serve_a2a(lambda p, **_: {"text": p}, executor=executor)


async def test_a2a_auth_required_is_answered_on_the_same_task() -> None:
    verifier = PeerVerifier()
    executor = _AuthExecutor(verifier, "peer-audience")
    with _peer(executor) as peer:
        stack, link, _ = a2a_stack(
            peer.url,
            "presentation_exchange",
            HOLDER,
            audience={"protocol": "a2a", "target": "peer-audience"},
        )
        try:
            with bind({"actor": "agent-a"}):
                reply = await stack.asend(envelope("work"))
        finally:
            link.stop()
        sends = peer.sends()
    assert "final-result" in str(reply)  # how a completed task becomes reply text is not this layer's business
    assert len(sends) == 2  # the task, then one follow-up: bounded
    assert len(executor.task_ids) == 2 and executor.task_ids[0] == executor.task_ids[1] != ""
    assert len(verifier.admitted) == 1


async def test_a2a_and_mcp_handshakes_publish_identity_handshake_events() -> None:
    """Kills: a session handshake that runs the scheme with no event, where the send and the challenge each have one."""
    from mas.runtime.boundary.obs.event_stream import EventStream, reset_event_stream, set_event_stream

    stream, seen = EventStream(), []
    stream.subscribe(seen.append)
    token = set_event_stream(stream)
    try:
        verifier = PeerVerifier()
        executor = _AuthExecutor(verifier, "peer-audience")
        with _peer(executor) as peer:
            stack, link, _ = a2a_stack(
                peer.url,
                "presentation_exchange",
                HOLDER,
                audience={"protocol": "a2a", "target": "peer-audience"},
            )
            try:
                with bind({"actor": "agent-a"}):
                    await stack.asend(envelope("work"))
            finally:
                link.stop()
        mcp_verifier, sessions = PeerVerifier(), set()
        server = _challenge_server(mcp_verifier, sessions)
        with server:
            server.initialize_extra = _answer({"challenge": mcp_verifier.issue(server.url)})
            provider = mcp_provider(server.url, identity_step("presentation_exchange", HOLDER))
            try:
                with bind({"actor": "agent-a"}):
                    await provider.acall_tool("echo", {})
            finally:
                await close(provider)
    finally:
        reset_event_stream(token)
    handshakes = [e for e in seen if e["kind"] == "identity.handshake"]
    assert sorted(e["protocol"] for e in handshakes) == ["a2a", "mcp"]
    assert all(e["outcome"] == "ok" and e["record"] == "id1" and e["latency_ms"] >= 0 for e in handshakes)
    assert all(e["operation"] == "handshake" and e["actor"] == "agent-a" for e in handshakes)


async def test_a2a_a_peer_that_asks_again_is_refused_after_one_round() -> None:
    verifier = PeerVerifier()
    executor = _AuthExecutor(verifier, "peer-audience", stubborn=True)
    with _peer(executor) as peer:
        stack, link, _ = a2a_stack(
            peer.url,
            "presentation_exchange",
            HOLDER,
            audience={"protocol": "a2a", "target": "peer-audience"},
        )
        try:
            with pytest.raises(AgentCommError, match="refused:not_admitted"):
                await stack.asend(envelope("work"))
        finally:
            link.stop()
        assert len(peer.sends()) == 2


async def test_a2a_a_hook_that_trusts_a_second_challenge_is_still_refused() -> None:
    """The protocol side bounds the exchange itself, whatever the hook accepts."""
    from a2a.types.a2a_pb2 import StreamResponse
    from library_ioa.plugins.a2a.handshake import complete
    from mas.library.standard.lib.stack import SessionHook

    def asking() -> list[StreamResponse]:
        status = TaskStatus(
            state=TaskState.TASK_STATE_AUTH_REQUIRED, message=new_data_message({"challenge": {"nonce": "n"}})
        )
        return [StreamResponse(task=Task(id="t", context_id="c", status=status))]

    class Trusting(SessionHook):
        async def established(self, session: Any) -> dict[str, Any]:
            await session.request("m", {})
            return {}

    async def send(_message: Any) -> list[StreamResponse]:
        return asking()

    with pytest.raises(AgentCommError, match="still requires authentication"):
        await complete(asking(), Trusting(), send)


# --- A2A: a task that ends in failure is an error, not a reply ------------------------------------------------


# --- A2A: what the peer's card declares reaches the scheme ---------------------------------------------------


class _Done(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id, ctx_id = context.task_id or "", context.context_id or ""
        message = new_data_message({}, task_id=task_id, context_id=ctx_id)
        message.parts.add().text = "done"
        status = TaskStatus(state=TaskState.TASK_STATE_COMPLETED, message=message)
        await event_queue.enqueue_event(Task(id=task_id, context_id=ctx_id, status=status))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        return None


@pytest.mark.parametrize(("declared", "expected"), [(["urn:example:badge/v1"], ["urn:example:badge/v1"]), ([], [])])
async def test_a2a_the_cards_extension_uris_reach_the_scheme_on_initiate(
    declared: list[str], expected: list[str]
) -> None:
    from wiring_schemes import PeerRecording

    with serve_a2a(lambda p, **_: {"text": p}, executor=_Done(), extensions=declared) as peer:
        stack, link, _ = a2a_stack(peer.url, "wiring_peer")
        try:
            await stack.asend(envelope("work"))
        finally:
            link.stop()
    ((_, phase, known),) = PeerRecording.SEEN
    assert phase == "initiate" and known == {"extensions": expected, "skills": ["peer"]}


async def test_a2a_the_cards_extension_uris_are_in_the_handshake_peer_too() -> None:

    verifier = PeerVerifier()
    executor = _AuthExecutor(verifier, "peer-audience")
    with serve_a2a(lambda p, **_: {"text": p}, executor=executor, extensions=["urn:example:badge/v1"]) as peer:
        stack, link, layer = a2a_stack(
            peer.url,
            "presentation_exchange",
            HOLDER,
            audience={"protocol": "a2a", "target": "peer-audience"},
        )
        seen: list[Any] = []
        original = layer.caller.handshake

        async def spy(ctx: Any, **kw: Any) -> Any:
            seen.append(dict(ctx["peer"]))
            return await original(ctx, **kw)

        layer.caller.handshake = spy
        try:
            with bind({"actor": "agent-a"}):
                await stack.asend(envelope("work"))
        finally:
            link.stop()
    assert seen[0]["extensions"] == ["urn:example:badge/v1"] and "challenge" in seen[0]
