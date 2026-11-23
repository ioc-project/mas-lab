#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Verified callers are kept apart by derivation: the SDK keeps tasks per owner, a handler gets conversation ids that
name the owner, and nothing is looked up, expires or fills up."""

from __future__ import annotations

import json
import re
import time
import uuid
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from a2a.helpers import new_text_message
from a2a.server.routes.common import StarletteUser
from a2a.types import Task, TaskState, TaskStatus
from library_ioa.plugins.a2a.exposure import A2AExposure
from library_ioa.plugins.a2a.server.app import build_app
from library_ioa.plugins.a2a.server.executor import MasLabAgentExecutor
from library_ioa.plugins.mcp.server import MCPToolServerFactory
from mas.library.standard.lib.stack import conversation_id
from mas.library.standard.plugins.identity.layer import IdentityLayer
from mas.library.standard.testing.identity_signed_badge import BADGE_HEADER, sign_badge
from starlette.authentication import SimpleUser
from wiring_builders import agent_card as _card
from wiring_builders import key_ref

KEY = "k" * 16


def _layer() -> IdentityLayer:
    return IdentityLayer(
        identity="srv",
        scheme="signed_badge",
        params={"key": key_ref(KEY), "subject": "server", "requirements": ["group"]},
        agent_id="srv-agent",
    )


def _hdr(who: str) -> dict[str, str]:
    return {
        BADGE_HEADER: sign_badge(KEY, who, {"group": "infra"}),
        "a2a-version": "1.0",
        "content-type": "application/json",
    }


def _rest(text: str, **message: Any) -> dict[str, Any]:
    return {"message": {"messageId": str(uuid.uuid4()), "role": "ROLE_USER", "parts": [{"text": text}], **message}}


class _Secret:
    """An executor whose task carries a secret result, as a real agent's would."""

    async def execute(self, context: Any, queue: Any) -> None:
        status = TaskStatus(
            state=TaskState.TASK_STATE_COMPLETED,
            message=new_text_message("alice-secret-result", context_id=context.context_id, task_id=context.task_id),
        )
        await queue.enqueue_event(Task(id=context.task_id, context_id=context.context_id, status=status))

    async def cancel(self, context: Any, queue: Any) -> None: ...


async def test_a_verified_caller_cannot_read_another_callers_tasks() -> None:
    """Kills: the gate never setting the SDK user (one anonymous owner shared every verified caller's tasks)."""
    app = build_app(_card(), _Secret(), inbound=_layer().serve)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://srv") as client:
        sent = await client.post("/a2a/rest/message:send", json=_rest("alice private"), headers=_hdr("alice"))
        task = sent.json()["task"]
        listed = await client.get("/a2a/rest/tasks", headers=_hdr("mallory"))
        assert listed.status_code == 200 and "alice-secret-result" not in listed.text
        stolen = await client.get(f"/a2a/rest/tasks/{task['id']}", headers=_hdr("mallory"))
        assert "alice-secret-result" not in stolen.text and stolen.status_code != 200
        rpc = {"jsonrpc": "2.0", "id": 1, "method": "GetTask", "params": {"id": task["id"]}}
        assert "alice-secret-result" not in (await client.post("/", json=rpc, headers=_hdr("mallory"))).text
        mine = await client.get(f"/a2a/rest/tasks/{task['id']}", headers=_hdr("alice"))
        assert mine.status_code == 200 and "alice-secret-result" in mine.text


OWNER_ID = re.compile(r"own\.[0-9a-f]{40}\.[0-9a-f]{32}")


def _handler_ids(seen: list[tuple[str, str]], *, answer: Any = None) -> Any:
    """A runtime-style handler (what ctl builds), recording the message and the session id it is given."""

    def handler(prompt: str, *, session_id: str, **_: Any) -> Any:
        seen.append((prompt, session_id))
        if answer is not None:
            return answer(session_id)
        return {"text": f"reply to {prompt}", "context_id": session_id}

    return A2AExposure._adapt_runtime_handler(handler)


def _gated_app(handler: Any, layer: IdentityLayer | None = None) -> Any:
    return build_app(_card(), MasLabAgentExecutor(handler), inbound=(layer or _layer()).serve)


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://srv")


async def test_same_context_two_owners_both_served() -> None:
    """Kills: a context id that one owner holds against another (the tables refused the second owner 403), and a
    namespacing that ignores the client id or the owner."""
    seen: list[tuple[str, str]] = []
    async with _client(_gated_app(_handler_ids(seen))) as c:
        a = await c.post("/a2a/rest/message:send", json=_rest("alice", contextId="c1"), headers=_hdr("alice"))
        m = await c.post("/a2a/rest/message:send", json=_rest("mallory", contextId="c1"), headers=_hdr("mallory"))
        a2 = await c.post("/a2a/rest/message:send", json=_rest("alice2", contextId="c1"), headers=_hdr("alice"))
        other = await c.post("/a2a/rest/message:send", json=_rest("alice3", contextId="c2"), headers=_hdr("alice"))
    assert [r.status_code for r in (a, m, a2, other)] == [200] * 4
    ids = dict(seen)
    assert all(OWNER_ID.fullmatch(i) for i in ids.values())
    assert ids["alice"] == ids["alice2"] != ids["mallory"]
    assert ids["alice3"] != ids["alice"]


async def test_no_takeover_after_any_time(monkeypatch: Any) -> None:
    """Kills: ownership that expires (the tables freed a context after their ttl and the next caller joined it)."""
    history: dict[str, list[str]] = {}

    def handler(prompt: str, *, session_id: str, **_: Any) -> str:
        history.setdefault(session_id, []).append(prompt)
        return "ok"

    real = time.monotonic
    app = _gated_app(A2AExposure._adapt_runtime_handler(handler))
    async with _client(app) as c:
        await c.post("/a2a/rest/message:send", json=_rest("alice private", contextId="c1"), headers=_hdr("alice"))
        monkeypatch.setattr(time, "monotonic", lambda: real() + 10 * 365 * 86400)
        await c.post("/a2a/rest/message:send", json=_rest("mallory", contextId="c1"), headers=_hdr("mallory"))
    assert sorted(len(v) for v in history.values()) == [1, 1]
    assert not any("mallory" in v and "alice private" in v for v in history.values())


def _task_routes(task_id: str) -> list[tuple[str, str, Any]]:
    cont = {"jsonrpc": "2.0", "id": 1, "method": "SendMessage", "params": _rest("steal", taskId=task_id)}
    return [
        ("GET", f"/a2a/rest/tasks/{task_id}", None),
        ("POST", f"/a2a/rest/tasks/{task_id}:cancel", {}),
        ("POST", f"/a2a/rest/tasks/{task_id}:subscribe", {}),
        ("GET", f"/a2a/rest/tasks/{task_id}/pushNotificationConfigs", None),
        ("POST", "/a2a/rest/message:send", _rest("steal", taskId=task_id)),
        ("POST", "/", cont),
    ]


async def test_other_owners_task_is_not_found() -> None:
    """Kills: a task that another verified owner can get, cancel, subscribe to, configure or continue; and a refusal
    that tells a real task of another from one that does not exist."""
    app = build_app(_card(), _Secret(), inbound=_layer().serve)
    async with _client(app) as c:
        task = (await c.post("/a2a/rest/message:send", json=_rest("alice private"), headers=_hdr("alice"))).json()[
            "task"
        ]
        mine = await c.get(f"/a2a/rest/tasks/{task['id']}", headers=_hdr("alice"))
        assert mine.status_code == 200 and "alice-secret-result" in mine.text
        for method, path, body in _task_routes(task["id"]):
            stolen = await c.request(method, path, json=body, headers=_hdr("mallory"))
            ghost_path = path.replace(task["id"], "no-such-task")
            ghost_body = json.loads(json.dumps(body).replace(task["id"], "no-such-task")) if body else body
            ghost = await c.request(method, ghost_path, json=ghost_body, headers=_hdr("mallory"))
            assert "alice-secret-result" not in stolen.text, (method, path)
            assert stolen.status_code == ghost.status_code, (method, path)
            assert stolen.text.replace(task["id"], "X") == ghost.text.replace("no-such-task", "X"), (method, path)
        listed = await c.get("/a2a/rest/tasks", headers=_hdr("mallory"))
        assert listed.status_code == 200 and "alice-secret-result" not in listed.text and task["id"] not in listed.text


async def test_no_owner_quota() -> None:
    """Kills: a per-owner quota or a table that fills (the tables answered 429 past 1000 ids for one owner)."""
    app = _gated_app(MasLabAgentExecutor(lambda *a, **k: "ok"))
    async with _client(app) as c:
        codes = {
            (await c.post("/a2a/rest/message:send", json=_rest("y"), headers=_hdr("dave"))).status_code
            for _ in range(1100)
        }
    assert codes == {200}


def _results(session_id: str) -> list[Any]:
    return [
        {"text": "plain", "context_id": session_id},
        {"text": "with artifact", "context_id": session_id, "artifacts": [{"name": "a", "text": "body"}]},
        {"text": "asks", "context_id": session_id, "task_state": "input_required"},
        {"text": "working", "context_id": session_id, "task_state": "working"},
        "bare string",
    ]


@pytest.mark.parametrize("which", range(5))
async def test_wire_carries_client_ids_only(which: int) -> None:
    """Kills: ``_emit_result`` putting the handler's (internal) id on the wire, as ctl returns its own session id."""
    seen: list[tuple[str, str]] = []
    handler = _handler_ids(seen, answer=lambda sid: _results(sid)[which])
    app = _gated_app(handler)
    wire: list[str] = []
    async with _client(app) as c:
        for who in ("alice", "mallory"):
            sent = await c.post("/a2a/rest/message:send", json=_rest("hi", contextId="c1"), headers=_hdr(who))
            wire.append(sent.text)
            body = sent.json()
            found = body.get("task") or body["message"]
            assert found["contextId"] == "c1"
            tid = (body.get("task") or {}).get("id")
            streamed = await c.post("/a2a/rest/message:stream", json=_rest("hi", contextId="c1"), headers=_hdr(who))
            wire.append(streamed.text)
            assert "c1" in streamed.text
            if tid:
                wire.append((await c.get(f"/a2a/rest/tasks/{tid}", headers=_hdr(who))).text)
                wire.append((await c.post(f"/a2a/rest/tasks/{tid}:cancel", json={}, headers=_hdr(who))).text)
                wire.append((await c.get(f"/a2a/rest/tasks/{tid}/pushNotificationConfigs", headers=_hdr(who))).text)
            wire.append((await c.get("/a2a/rest/tasks", headers=_hdr(who))).text)
            wire.append((await c.get("/a2a/rest/tasks/no-such", headers=_hdr(who))).text)
            wire.append((await c.get("/a2a/rest/extendedAgentCard", headers=_hdr(who))).text)
            rpc = {"jsonrpc": "2.0", "id": 1, "method": "SendMessage", "params": _rest("hi", contextId="c1")}
            wire.append((await c.post("/", json=rpc, headers=_hdr(who))).text)
    assert len(seen) >= 4 and all(OWNER_ID.fullmatch(i) for _, i in seen)
    for text in wire:
        assert not OWNER_ID.search(text), text


async def test_task_only_send_keeps_conversation() -> None:
    """Kills: the conversation read from the fresh context id the SDK gives a send that names only its task."""
    seen: list[tuple[str, str]] = []
    handler = _handler_ids(seen, answer=lambda sid: {"text": "asks", "context_id": sid, "task_state": "input_required"})
    async with _client(_gated_app(handler)) as c:
        first = (await c.post("/a2a/rest/message:send", json=_rest("one"), headers=_hdr("alice"))).json()["task"]
        again = await c.post("/a2a/rest/message:send", json=_rest("two", taskId=first["id"]), headers=_hdr("alice"))
        assert again.status_code == 200
    assert seen[0][1] == seen[1][1] and OWNER_ID.fullmatch(seen[0][1])


async def test_identity_off_ids_unchanged() -> None:
    """Kills: namespacing on without a verifier (a bare id must reach the handler as the client sent it)."""
    seen: list[tuple[str, str]] = []
    app = build_app(_card(), MasLabAgentExecutor(_handler_ids(seen)))
    async with _client(app) as c:
        sent = await c.post("/a2a/rest/message:send", json=_rest("hi", contextId="c1"), headers={"a2a-version": "1.0"})
        assert sent.status_code == 200 and (sent.json().get("task") or sent.json()["message"])["contextId"] == "c1"
    assert seen == [("hi", "c1")]


@pytest.mark.parametrize("name", ["own:" + "a" * 40, "alice", ""])
async def test_slim_user_not_namespaced(name: str) -> None:
    """Kills: namespacing keyed on a non-empty ``user_name`` (a transport's user, or one that looks like an owner key,
    switched it on with identity off)."""
    seen: list[tuple[str, str]] = []
    handler = _handler_ids(seen)
    context = SimpleNamespace(
        context_id="c1",
        current_task=None,
        message=None,
        task_id="t",
        call_context=SimpleNamespace(user=StarletteUser(SimpleUser(name))),
    )
    handler("hi", context=context)
    assert seen == [("hi", "c1")]


async def test_ids_over_256_refused_when_gated() -> None:
    """Kills: a verifier that reads the first 256 characters of an id (two longer ids shared evidence binding)."""
    async with _client(_gated_app(MasLabAgentExecutor(lambda *a, **k: "ok"))) as c:
        for field in ("contextId", "messageId"):
            long = await c.post("/a2a/rest/message:send", json=_rest("x", **{field: "x" * 257}), headers=_hdr("alice"))
            assert long.status_code == 400, field
        exact = await c.post("/a2a/rest/message:send", json=_rest("x", contextId="x" * 256), headers=_hdr("alice"))
        assert exact.status_code == 200
        get = await c.get(f"/a2a/rest/tasks/{'x' * 257}", headers=_hdr("alice"))
        assert get.status_code == 400
    async with _client(build_app(_card(), MasLabAgentExecutor(lambda *a, **k: "ok"))) as c:
        ungated = await c.post(
            "/a2a/rest/message:send", json=_rest("x", contextId="x" * 300), headers={"a2a-version": "1.0"}
        )
        assert ungated.status_code == 200


async def test_serve_namespaces_the_conversation_and_the_principal_and_both_bindings_agree() -> None:
    """Kills: one translation site that drifts from the other (HTTP adapter vs identity layer), and a principal whose
    session is the caller's raw id."""
    from mas.library.standard.lib.stack import Envelope

    seen: list[tuple[str, str]] = []
    layer = _layer()
    async with _client(_gated_app(_handler_ids(seen), layer)) as c:
        await c.post("/a2a/rest/message:send", json=_rest("hi", contextId="c1"), headers=_hdr("alice"))
    got: list[Envelope] = []

    async def agent(envelope: Envelope) -> str:
        got.append(envelope)
        return "ok"

    badge = {BADGE_HEADER: sign_badge(KEY, "alice", {"group": "infra"})}
    served = layer.serve(agent)
    await served(Envelope(payload="", headers=badge, meta={"protocol": "a2a", "context_id": "c1"}))
    meta = got[0].meta
    assert meta["context_id"] == seen[0][1] == meta["principal"]["session"]
    assert meta["context_id"] == conversation_id(meta["owner"], "c1")  # derived from the verified owner
    assert "bind_owner" not in meta
    await served(Envelope(payload="", headers=badge, meta={"protocol": "a2a"}))
    assert "session" not in got[1].meta["principal"] and not got[1].meta.get("context_id")


async def test_the_verifier_sees_the_message_the_handler_receives_on_each_binding() -> None:
    """Kills: ``params.message`` preferred on a REST route (the verifier saw a decoy, the handler the real message)."""
    layer = _layer()
    verified: list[tuple[str, str]] = []
    original = layer._verify

    async def spy(envelope: Any) -> Any:
        verified.append((str(envelope.payload), str(envelope.meta["context_id"])))
        return await original(envelope)

    layer._verify = spy  # type: ignore[method-assign]
    got: list[str] = []

    async def handler(message: str, context: Any, **_: Any) -> str:
        got.append(message)
        return "ok"

    app = build_app(_card(), MasLabAgentExecutor(handler), inbound=layer.serve)
    real, decoy = _rest("REAL text", contextId="ctx-real")["message"], _rest("decoy text", contextId="ctx-decoy")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://srv") as client:
        await client.post(
            "/a2a/rest/message:send", json={"message": real, "params": {"message": decoy}}, headers=_hdr("a")
        )
        real_rpc = {**real, "contextId": "ctx-real-2", "messageId": "m-2"}
        rpc = {"jsonrpc": "2.0", "id": 1, "method": "SendMessage", "params": {"message": real_rpc}}
        await client.post("/", json=rpc, headers=_hdr("b"))
    assert verified == [("REAL text", "ctx-real"), ("REAL text", "ctx-real-2")]
    assert got == ["REAL text"] * 2


class _Probe:
    H = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
    INIT = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "x", "version": "1"}},
    }

    @classmethod
    def who(cls, name: str) -> dict[str, str]:
        return {**cls.H, BADGE_HEADER: sign_badge(KEY, name, {"group": "infra"})}


async def _open(client: httpx.AsyncClient, who: str) -> httpx.Response:
    return await client.post("/mcp", json=_Probe.INIT, headers=_Probe.who(who))


def _badge_headers(sub: str, iss: str = "", **extra: str) -> dict[str, str]:
    headers = {"x-sub": sub, "a2a-version": "1.0", "content-type": "application/json", **extra}
    if iss:
        headers["x-iss"] = iss
    return headers


def _issuer_layer() -> IdentityLayer:
    return IdentityLayer(identity="srv", scheme="wiring_issuer", params={}, agent_id="srv-agent")


async def test_the_same_subject_at_two_issuers_is_two_owners() -> None:
    """Kills: owners keyed by the bare subject (alice at one identity provider got alice's tasks at another)."""
    seen: list[tuple[str, str]] = []
    app = _gated_app(_handler_ids(seen), _issuer_layer())
    async with _client(app) as c:
        await c.post(
            "/a2a/rest/message:send", json=_rest("a", contextId="c1"), headers=_badge_headers("alice", "idp-a")
        )
        await c.post(
            "/a2a/rest/message:send", json=_rest("b", contextId="c1"), headers=_badge_headers("alice", "idp-b")
        )
        await c.post(
            "/a2a/rest/message:send", json=_rest("c", contextId="c1"), headers=_badge_headers("alice", "idp-a")
        )
        bad = await c.get("/a2a/rest/tasks", headers=_badge_headers("alice", "idp-a", **{"x-bad-iss": "1"}))
        assert bad.status_code in {401, 403}  # an issuer that is not a string is a failed verification
    ids = dict(seen)
    assert ids["a"] == ids["c"] != ids["b"]


def test_owner_keys_differ_by_record_issuer_and_subject() -> None:
    from mas.library.standard.plugins.identity.layer import owner_key

    alice_a = owner_key("r", {"subject": "alice", "claims": {"iss": "A"}})
    assert alice_a == owner_key("r", {"subject": "alice", "claims": {"iss": "A"}})
    assert (
        len(
            {
                alice_a,
                owner_key("r", {"subject": "alice", "claims": {"iss": "B"}}),
                owner_key("r2", {"subject": "alice", "claims": {"iss": "A"}}),
                owner_key("r", {"subject": "alice"}),
            }
        )
        == 4
    )


def test_task_ids_are_found_on_tenant_routes_and_in_push_config_calls() -> None:
    """Kills: a task id read from ``params.id`` for a push-config call (that is the config's id) or missed on a tenant
    route."""
    from library_ioa.plugins.a2a.server.app import _ids_of

    assert _ids_of("/a2a/rest/tenant-1/tasks/abc:cancel", None, rest=True).task_id == "abc"
    assert _ids_of("/a2a/rest/tasks/xyz/pushNotificationConfigs/cfg", None, rest=True).task_id == "xyz"
    config = {"method": "GetTaskPushNotificationConfig", "params": {"taskId": "T", "id": "cfg"}}
    assert _ids_of("/", config, rest=False).task_id == "T"
    assert _ids_of("/", {"method": "GetTask", "params": {"id": "T2"}}, rest=False).task_id == "T2"


async def test_the_gate_reads_the_ids_the_sdk_reads_and_refuses_a_body_it_cannot_parse() -> None:
    """Kills: ``contextId`` read by hand while the protobuf parse takes the last of two spellings."""
    layer = _layer()
    verified: list[str] = []
    original = layer._verify

    async def spy(envelope: Any) -> Any:
        verified.append(str(envelope.meta["context_id"]))
        return await original(envelope)

    layer._verify = spy  # type: ignore[method-assign]
    seen: list[tuple[str, str]] = []
    async with _client(_gated_app(_handler_ids(seen), layer)) as c:
        both = _rest("two spellings")
        both["message"].update({"contextId": "ctx-shown", "context_id": "ctx-sdk"})
        assert (await c.post("/a2a/rest/message:send", json=both, headers=_hdr("alice"))).status_code == 200
        assert verified == ["ctx-sdk"]  # the verifier is shown the id the SDK will use, not the other spelling
        await c.post("/a2a/rest/message:send", json=_rest("j", contextId="ctx-sdk"), headers=_hdr("alice"))
        junk = {"message": {"parts": "not-a-list"}}
        assert (await c.post("/a2a/rest/message:send", json=junk, headers=_hdr("alice"))).status_code == 400
    assert seen[0][1] == seen[1][1]  # the conversation the SDK used is the one the verifier was shown


async def test_a_gate_with_no_verifier_binds_nothing_and_serves_as_before() -> None:
    """Kills: owner state that runs for callers nobody verified."""
    factory = MCPToolServerFactory(inbound=lambda handler: handler)
    app = factory.asgi_app()
    async with factory._server.session_manager.run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000") as c:
            opened = await c.post("/mcp", json=_Probe.INIT, headers=_Probe.H)
            assert opened.status_code == 200
            ghost = {"mcp-session-id": "not-a-session", **_Probe.H}
            answer = await c.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "ping"}, headers=ghost)
            assert answer.status_code == 404  # the SDK's own answer, not the gate's
    seen: list[tuple[str, str]] = []
    plain = build_app(_card(), MasLabAgentExecutor(_handler_ids(seen)), inbound=lambda handler: handler)
    async with _client(plain) as c:
        await c.post("/a2a/rest/message:send", json=_rest("hi", contextId="c1"), headers={"a2a-version": "1.0"})
    assert seen == [("hi", "c1")]


async def test_slimrpc_same_context_from_two_verified_owners_gives_distinct_conversations() -> None:
    """Kills: a context id shared by two owners behind slimrpc (the tables refused the second owner), and a slimrpc
    turn that hands the agent the caller's raw id."""
    from a2a.helpers import new_text_message
    from a2a.types.a2a_pb2 import Role
    from library_ioa.plugins.a2a.layer import HEADERS_KEY, A2ALayer
    from mas.library.standard.lib.stack import Envelope

    identity = _layer()
    seen: list[tuple[str, str, str]] = []

    async def agent(envelope: Envelope) -> str:
        seen.append(
            (str(envelope.payload), str(envelope.meta["context_id"]), str(envelope.meta["principal"]["session"]))
        )
        return "ok"

    a2a = A2ALayer()
    a2a.bind_above([identity])
    a2a.serve(identity.serve(agent))

    async def send(who: str, text: str, ctx: str) -> None:
        message = new_text_message(text, role=Role.ROLE_USER)
        message.metadata.update({HEADERS_KEY: {BADGE_HEADER: sign_badge(KEY, who, {"group": "infra"})}})
        context = SimpleNamespace(message=message, context_id=ctx, task_id="t", current_task=None)
        await a2a._turn(text, context)

    await send("alice", "a1", "c1")
    await send("mallory", "m1", "c1")
    await send("alice", "a2", "c1")
    by_text = {text: (ctx, session) for text, ctx, session in seen}
    assert by_text["a1"] == by_text["a2"] and by_text["a1"][0] == by_text["a1"][1]
    assert by_text["m1"] != by_text["a1"] and all(OWNER_ID.fullmatch(c) for c, _ in by_text.values())


class _Carrier:
    """A verifier that finds its credential in message metadata and says which key carried it."""

    scheme = "carrier"
    capabilities = frozenset({"verify"})
    PARAMS_SCHEMA = {"type": "object"}

    @classmethod
    def from_spec(cls, params: Any) -> _Carrier:
        return cls()

    def identities(self) -> list:
        return []

    def select(self, req: Any) -> None:
        return None

    async def present(self, req: Any) -> dict:
        return {}

    async def verify(self, inbound: Any) -> dict:
        if "CRED-VALUE" not in (inbound.get("meta", {}).get("x/cred"), inbound.get("headers", {}).get("x-cred")):
            from mas.library.standard.lib.identity.contract import IdentityError

            raise IdentityError("no_credential")
        return {"subject": "alice", "claims": {}, "source": "other", "credential_keys": ["x/cred"]}


class _Keeper(_Secret):
    """The SDK's own view of the message: what its task store and the executor are given."""

    kept: list[str] = []

    async def execute(self, context: Any, queue: Any) -> None:
        from google.protobuf.json_format import MessageToJson

        self.kept.append(MessageToJson(context.message))
        for task in (context.current_task,):
            if task is not None:
                self.kept.append(str(task))
        await super().execute(context, queue)


async def test_the_credential_carriers_of_a_message_are_not_kept_by_the_task_store() -> None:
    """Kills: a credential in message metadata that the SDK keeps (the executor's message, task history, GetTask)."""
    layer = IdentityLayer(identity="srv", scheme="carrier", params={}, agent_id="srv", schemes={"carrier": _Carrier})
    keeper = _Keeper()
    keeper.kept = []
    app = build_app(_card(), keeper, inbound=layer.serve)
    message = {
        "messageId": str(uuid.uuid4()),
        "role": "ROLE_USER",
        "parts": [{"text": "hi"}],
        "metadata": {"x/cred": "CRED-VALUE", "mas.headers": {"authorization": "Bearer HDR-VALUE"}, "x/keep": "kept"},
    }
    headers = {"a2a-version": "1.0", "content-type": "application/json"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://srv") as client:
        sent = await client.post("/a2a/rest/message:send", json={"message": message}, headers=headers)
        assert sent.status_code == 200, sent.text
        task_id = sent.json()["task"]["id"]
        got = await client.get(
            f"/a2a/rest/tasks/{task_id}?historyLength=50", headers={**headers, "x-cred": "CRED-VALUE"}
        )
    assert got.status_code == 200
    seen = sent.text + got.text + " ".join(keeper.kept)
    assert "CRED-VALUE" not in seen and "HDR-VALUE" not in seen
    assert "x/keep" in " ".join(keeper.kept)  # what is not a carrier of the credential is the caller's data and stays


async def test_the_identity_layer_has_a_slimrpc_server_drop_the_carriers_its_verifier_names() -> None:
    """Kills: the identity layer never telling the transport which keys carried the credential (slimrpc path)."""
    from types import SimpleNamespace

    from a2a.helpers import new_text_message
    from a2a.types.a2a_pb2 import Role
    from library_ioa.plugins.a2a.layer import A2ALayer

    identity = IdentityLayer(identity="srv", scheme="carrier", params={}, agent_id="srv", schemes={"carrier": _Carrier})
    layer = A2ALayer()
    layer.bind_above([identity])
    ran: list[str] = []

    async def handler(envelope: Any) -> str:
        ran.append(str(envelope.payload))
        return "ok"

    layer.serve(identity.serve(handler))
    message = new_text_message("hi", role=Role.ROLE_USER)
    message.metadata.update({"x/cred": "CRED-VALUE", "x/keep": "kept"})
    context = SimpleNamespace(message=message, context_id="c1", task_id="t1", current_task=None)
    await layer._turn("hi", context)
    assert ran == ["hi"] and dict(message.metadata) == {"x/keep": "kept"}


class _DataCarrier(_Carrier):
    """A verifier whose credential is a data part and whose text is wrapped: `WRAP:<text>`."""

    scheme = "data_carrier"
    RETURNS_PAYLOAD = True

    async def verify(self, inbound: Any) -> dict:
        from library_ioa.plugins.a2a.data_parts import DATA_PARTS_KEY
        from mas.library.standard.lib.identity.contract import IdentityError

        parts = inbound.get("meta", {}).get(DATA_PARTS_KEY) or []
        text = inbound.get("payload", "")
        if not parts or parts[0].get("cred") != "DATA-CRED" or not text.startswith("WRAP:"):
            raise IdentityError("no_credential")
        return {
            "subject": "alice",
            "claims": {},
            "source": "other",
            "payload": text.removeprefix("WRAP:"),
            "credential_keys": [DATA_PARTS_KEY],
        }


def _wrapped_message() -> dict[str, Any]:
    return {
        "messageId": str(uuid.uuid4()),
        "role": "ROLE_USER",
        "parts": [{"text": "WRAP:hi"}, {"data": {"cred": "DATA-CRED"}}],
    }


async def test_a_data_part_credential_and_the_wrapper_text_are_not_kept_over_http() -> None:
    """Kills: a credential in a data part, or the wrapped text, staying in the message the SDK keeps (HTTP)."""
    layer = IdentityLayer(
        identity="srv", scheme="data_carrier", params={}, agent_id="srv", schemes={"data_carrier": _DataCarrier}
    )
    keeper = _Keeper()
    keeper.kept = []
    app = build_app(_card(), keeper, inbound=layer.serve)
    headers = {"a2a-version": "1.0", "content-type": "application/json"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://srv") as client:
        sent = await client.post("/a2a/rest/message:send", json={"message": _wrapped_message()}, headers=headers)
        assert sent.status_code == 200, sent.text
        task_id = sent.json()["task"]["id"]
        got = await client.get(f"/a2a/rest/tasks/{task_id}?historyLength=50", headers=headers)
    seen = sent.text + got.text + " ".join(keeper.kept)
    assert "DATA-CRED" not in seen and "WRAP" not in seen
    assert '"text": "hi"' in " ".join(keeper.kept)  # the agent got, and the SDK kept, the text without the wrapper


async def test_a_data_part_credential_and_the_wrapper_text_are_not_kept_over_slimrpc() -> None:
    """Kills: the same, on the slimrpc server path (the verifier's payload text is not written back there)."""
    from types import SimpleNamespace

    from a2a.helpers import new_text_message
    from a2a.types.a2a_pb2 import Part, Role
    from library_ioa.plugins.a2a.layer import A2ALayer

    identity = IdentityLayer(
        identity="srv", scheme="data_carrier", params={}, agent_id="srv", schemes={"data_carrier": _DataCarrier}
    )
    layer = A2ALayer()
    layer.bind_above([identity])
    ran: list[str] = []

    async def handler(envelope: Any) -> str:
        ran.append(str(envelope.payload))
        return "ok"

    layer.serve(identity.serve(handler))
    message = new_text_message("WRAP:hi", role=Role.ROLE_USER)
    part = Part()
    part.data.struct_value.update({"cred": "DATA-CRED"})
    message.parts.append(part)
    context = SimpleNamespace(message=message, context_id="c1", task_id="t1", current_task=None)
    await layer._turn("WRAP:hi", context)
    assert ran == ["hi"]
    kept = " ".join(str(p) for p in message.parts)
    assert "DATA-CRED" not in kept and "WRAP" not in kept and 'text: "hi"' in kept, kept
