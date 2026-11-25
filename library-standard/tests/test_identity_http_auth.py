#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""What ``IdentityAuth`` does to a request: present, carry, answer one 401, refuse a collision or a hop, never leak.

The auth is run on a real ``httpx`` client over a ``MockTransport``, sync and async, with a scripted wallet: no
server, no clock, no sleep.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, ClassVar

import httpx
import pytest
from mas.library.standard.lib.identity import observe
from mas.library.standard.lib.identity.caller import Caller
from mas.library.standard.lib.identity.contract import CAP_DELEGATE, CAP_SIGN, CAPABILITIES, Evidence, IdentityError
from mas.library.standard.lib.identity.principal import bind
from mas.library.standard.plugins.identity.http_auth import (
    PLACE_KEY,
    IdentityAuth,
    origin,
    parse_auth_params,
    task_of,
)
from mas.library.standard.testing.identity_scripted import Scripted
from mas.runtime.boundary.agentcomm.protocol import AgentCommError

RPC = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "echo"}}).encode()


class Presents(Scripted):
    """A wallet that presents and challenges but does not sign."""

    capabilities: ClassVar[frozenset[str]] = CAPABILITIES - {CAP_DELEGATE, CAP_SIGN}


class Signs(Scripted):
    """A wallet that also signs the request it presents for."""

    capabilities: ClassVar[frozenset[str]] = CAPABILITIES - {CAP_DELEGATE}


def ev(**headers: str) -> Evidence:
    return Evidence(headers=headers)


def caller_of(*items: Evidence | Exception, signs: tuple[Evidence, ...] = (), cls: type = Presents) -> Caller:
    return Caller("rec", cls(list(items), signs=list(signs), repeat_last=True))


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(observe, "publish_event", seen.append)
    monkeypatch.setattr(observe, "emit_to_channel", lambda event: None)
    return seen


class Wire:
    """A ``MockTransport`` that records what reached it and answers from a function."""

    def __init__(self, answer: Callable[[httpx.Request], httpx.Response] | None = None) -> None:
        self.seen: list[httpx.Request] = []
        self._answer = answer or (lambda request: httpx.Response(200, json={}))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        return self._answer(request)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


def first_then_401(request: httpx.Request) -> httpx.Response:
    """401 to the first presentation, 200 to the answer."""
    if request.headers.get("authorization") == "first":
        return httpx.Response(401, headers={"www-authenticate": 'Bearer realm="x" nonce="n-1"'})
    return httpx.Response(200, json={})


async def post(wire: Wire, auth: IdentityAuth, url: str = "http://a.test/mcp", body: bytes | None = RPC) -> Any:
    async with httpx.AsyncClient(transport=wire.transport(), auth=auth) as client:
        return await client.post(url, content=body)


def post_sync(wire: Wire, auth: IdentityAuth, url: str = "http://a.test/mcp", body: bytes | None = RPC) -> Any:
    with httpx.Client(transport=wire.transport(), auth=auth) as client:
        return client.post(url, content=body)


# --- small readers ---------------------------------------------------------------------------------


def test_the_operation_is_read_from_the_json_rpc_envelope_and_nothing_else() -> None:
    def op(body: bytes | None) -> str:
        return task_of(httpx.Request("POST", "http://a.test/", content=body))

    assert op(RPC) == "tools/call:echo"
    assert op(json.dumps({"method": "tools/list"}).encode()) == "tools/list"
    assert op(json.dumps({"method": "tools/call", "params": {"name": 3}}).encode()) == "tools/call"
    assert op(b"") == "" and op(b"not json") == "" and op(b"[1]") == "" and op(b'{"result": 1}') == ""


def test_challenge_parameters_keep_only_what_a_challenge_can_hold() -> None:
    value = (
        'Bearer realm="r", nonce="n\\"1", audience="https://a.test", requirements="a b", schemes="s", '
        'expires_at="12.5", ext/key="v", error="invalid_token", scope="x", nonce2="", '
        f'realm2="{"y" * 300}", expires_at="nan"'
    )
    got = parse_auth_params(value)
    assert got == {
        "realm": "r",
        "nonce": 'n"1',
        "audience": "https://a.test",
        "requirements": ["a", "b"],
        "schemes": ["s"],
        "expires_at": 12.5,
        "ext/key": "v",
    }
    assert parse_auth_params("") == {} and parse_auth_params('expires_at="soon"') == {}


def test_the_origin_is_scheme_and_netloc() -> None:
    assert origin(httpx.URL("https://a.test:8443/x?y=1")) == "https://a.test:8443"


# --- presenting ------------------------------------------------------------------------------------


async def test_a_request_carries_the_evidence_and_is_stamped_with_its_place() -> None:
    wire = Wire()
    scheme = Presents([ev(authorization="Bearer t")], repeat_last=True)
    await post(wire, IdentityAuth(Caller("rec", scheme), protocol="mcp"))
    (sent,) = wire.seen
    assert sent.headers["authorization"] == "Bearer t"
    assert sent.extensions[PLACE_KEY] == "http://a.test:80"
    (asked,) = scheme.requests
    assert asked["phase"] == "initiate" and asked["task"] == "tools/call:echo"
    assert asked["audience"] == {"protocol": "mcp", "target": "http://a.test"}


async def test_the_row_audience_the_actor_and_the_named_identity_reach_the_scheme() -> None:
    scheme = Presents([ev(authorization="x")], repeat_last=True)
    auth = IdentityAuth(
        Caller("rec", scheme),
        protocol="mcp",
        audience={"protocol": "mcp", "target": "https://row.example"},
        actor="desk",
        use="scripted",
    )
    await post(Wire(), auth)
    with bind({"actor": "other", "subject": "u"}):
        await post(Wire(), auth)
    first, second = scheme.requests
    assert first["audience"]["target"] == "https://row.example" and first["identity"] == "scripted"
    assert first["principal"]["actor"] == "desk"
    assert second["principal"] == {"actor": "other", "subject": "u"}


async def test_what_a_held_session_already_learned_of_the_peer_rides_on_the_request() -> None:
    scheme = Presents([ev(authorization="x")], repeat_last=True)
    auth = IdentityAuth(Caller("rec", scheme), protocol="mcp", known_peer=lambda: {"skills": ["pay"]})
    await post(Wire(), auth)
    assert scheme.requests[0]["peer"] == {"skills": ["pay"]}


async def test_get_and_delete_with_no_body_carry_the_headers_only() -> None:
    wire = Wire()
    auth = IdentityAuth(
        caller_of(Evidence(headers={"authorization": "a"}, meta={"io.t/x": 1})), protocol="mcp", meta_carried=True
    )
    async with httpx.AsyncClient(transport=wire.transport(), auth=auth) as client:
        await client.get("http://a.test/mcp")
        await client.delete("http://a.test/mcp")
    assert [r.headers["authorization"] for r in wire.seen] == ["a", "a"]
    assert all(r.content == b"" for r in wire.seen)


async def test_meta_goes_into_the_params_of_a_json_rpc_request_and_the_length_follows() -> None:
    wire = Wire()
    auth = IdentityAuth(
        caller_of(Evidence(headers={"authorization": "a"}, meta={"io.t/x": 1})), protocol="mcp", meta_carried=True
    )
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"_meta": {"mine": 2}}}).encode()
    await post(wire, auth, body=body)
    (sent,) = wire.seen
    assert json.loads(sent.content)["params"]["_meta"] == {"io.t/x": 1, "mine": 2}
    assert int(sent.headers["content-length"]) == len(sent.content)


@pytest.mark.parametrize(
    "body",
    [
        json.dumps({"method": "m", "params": [1]}).encode(),
        json.dumps({"method": "m", "params": {"_meta": "text"}}).encode(),
    ],
)
async def test_a_request_that_cannot_hold_meta_is_refused_not_dropped(body: bytes) -> None:
    wire = Wire()
    auth = IdentityAuth(caller_of(Evidence(meta={"io.t/x": 1})), protocol="mcp", meta_carried=True)
    with pytest.raises(AgentCommError, match="meta_unsupported"):
        await post(wire, auth, body=body)
    assert wire.seen == []


async def test_a_response_the_client_posts_back_and_a_non_json_body_are_left_alone() -> None:
    wire = Wire()
    auth = IdentityAuth(
        caller_of(Evidence(headers={"authorization": "a"}, meta={"io.t/x": 1})), protocol="mcp", meta_carried=True
    )
    for body in (json.dumps({"jsonrpc": "2.0", "id": 7, "result": {}}).encode(), b"plain text"):
        await post(wire, auth, body=body)
    assert [r.content for r in wire.seen] == [
        json.dumps({"jsonrpc": "2.0", "id": 7, "result": {}}).encode(),
        b"plain text",
    ]


async def test_meta_on_a_call_with_no_meta_field_is_refused_with_nothing_sent() -> None:
    wire = Wire()
    auth = IdentityAuth(caller_of(Evidence(meta={"io.t/x": 1})), protocol="llm", meta_carried=False)
    with pytest.raises(AgentCommError, match="meta_unsupported"):
        await post(wire, auth)
    assert wire.seen == []


async def test_a_chunked_body_is_presented_for_and_sent_with_one_length() -> None:
    async def chunks():
        yield RPC

    wire = Wire()
    auth = IdentityAuth(
        caller_of(Evidence(headers={"authorization": "a"}, meta={"io.t/x": 1})), protocol="mcp", meta_carried=True
    )
    async with httpx.AsyncClient(transport=wire.transport(), auth=auth) as client:
        await client.post("http://a.test/mcp", content=chunks())
    (sent,) = wire.seen
    assert "transfer-encoding" not in sent.headers and int(sent.headers["content-length"]) == len(sent.content)
    assert json.loads(sent.content)["params"]["_meta"] == {"io.t/x": 1}


# --- refusal before sending ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (IdentityError("revoked"), "revoked"),
        (IdentityError("no_credential"), "no_credential"),
        (RuntimeError("secret-canary-value"), "unavailable"),
    ],
)
async def test_a_wallet_that_cannot_present_stops_the_request_with_a_value_free_code(
    error: Exception, code: str
) -> None:
    wire, told = Wire(), []
    auth = IdentityAuth(
        Caller("rec", Presents([error])), protocol="mcp", refused=lambda task, denied: told.append((task, str(denied)))
    )
    with pytest.raises(AgentCommError) as err:
        await post(wire, auth)
    assert wire.seen == [] and code in str(err.value) and "canary" not in str(err.value)
    assert told == [("tools/call:echo", str(err.value))]
    assert err.value.__cause__ is None


async def test_an_evidence_header_the_row_also_sets_is_a_collision_and_nothing_is_sent() -> None:
    wire = Wire()
    auth = IdentityAuth(caller_of(ev(Authorization="Bearer t")), protocol="mcp", static_headers=["authorization"])
    with pytest.raises(AgentCommError, match="header_collision"):
        await post(wire, auth)
    assert wire.seen == []


# --- signing ---------------------------------------------------------------------------------------


async def test_a_signing_scheme_sees_the_request_not_the_body_and_its_signature_is_sent() -> None:
    wire = Wire()
    scheme = Signs([ev(authorization="a")], signs=[ev(signature="sig-1")], repeat_last=True)
    await post(wire, IdentityAuth(Caller("rec", scheme), protocol="mcp"), url="http://a.test:8080/mcp?q=1#frag")
    (sent,) = wire.seen
    assert sent.headers["signature"] == "sig-1"
    (signed,) = scheme.sign_requests
    view = signed["request"]
    assert signed["phase"] == "sign" and view["method"] == "POST"
    assert view["url"] == "http://a.test:8080/mcp?q=1"  # no fragment: it never reaches the server
    assert "authorization" not in view["headers"] and "body_text" not in view
    assert len(view["body_sha256"]) == 64 and view["request_id"]
    assert sent.extensions[PLACE_KEY] == "http://a.test:8080/mcp?q=1"  # a signing scheme binds the path too


async def test_signing_sees_the_static_headers_that_were_already_on_the_request() -> None:
    wire = Wire()
    scheme = Signs([ev(authorization="a")], signs=[ev(signature="s")], repeat_last=True)
    auth = IdentityAuth(Caller("rec", scheme), protocol="mcp", static_headers=["x-static"])
    async with httpx.AsyncClient(transport=wire.transport(), auth=auth, headers={"x-static": "1"}) as client:
        await client.post("http://a.test/mcp", content=RPC)
    assert scheme.sign_requests[0]["request"]["headers"]["x-static"] == "1"


# --- one 401, answered once ------------------------------------------------------------------------


async def test_a_401_is_answered_once_with_the_peer_s_challenge_and_only_the_answer_is_sent_again() -> None:
    wire = Wire(first_then_401)
    scheme = Presents([ev(authorization="first", dpop="p"), ev(authorization="answer")], repeat_last=True)
    out = await post(wire, IdentityAuth(Caller("rec", scheme), protocol="mcp"))
    assert out.status_code == 200
    first, again = wire.seen
    assert first.headers["dpop"] == "p"
    assert again.headers["authorization"] == "answer" and "dpop" not in again.headers
    init, answer = scheme.requests
    assert answer["phase"] == "challenge"
    assert answer["peer"]["challenge"] == {"realm": "x", "nonce": "n-1"}
    assert answer["peer"]["wire"] == 'Bearer realm="x" nonce="n-1"'
    assert answer["peer"]["transport"]["status"] == 401


async def test_a_second_401_is_a_denial_and_a_closed_event(events: list[dict[str, Any]]) -> None:
    wire = Wire(lambda request: httpx.Response(401, headers={"www-authenticate": 'Bearer realm="x"'}))
    auth = IdentityAuth(caller_of(ev(authorization="a")), protocol="mcp", actor="desk")
    with pytest.raises(AgentCommError, match="unauthorized"):
        await post(wire, auth)
    assert len(wire.seen) == 2
    (event,) = [e for e in events if e["kind"] == "identity.challenge"]
    assert (event["outcome"], event["code"], event["direction"], event["actor"]) == (
        "denied",
        "refused:unauthorized",
        "answered",
        "desk",
    )


async def test_an_answered_challenge_is_a_closed_ok_event(events: list[dict[str, Any]]) -> None:
    wire = Wire(first_then_401)
    auth = IdentityAuth(caller_of(ev(authorization="first"), ev(authorization="answer")), protocol="mcp")
    await post(wire, auth)
    assert [(e["kind"], e["outcome"]) for e in events if e["kind"] == "identity.challenge"] == [
        ("identity.challenge", "ok")
    ]


async def test_a_403_is_refused_at_once_and_is_a_denied_present_event(events: list[dict[str, Any]]) -> None:
    wire = Wire(lambda request: httpx.Response(403))
    auth = IdentityAuth(caller_of(ev(authorization="a")), protocol="mcp")
    with pytest.raises(AgentCommError, match="forbidden"):
        await post(wire, auth)
    assert len(wire.seen) == 1
    (event,) = [e for e in events if e["outcome"] == "denied"]
    assert (event["kind"], event["outcome"], event["code"], event["operation"]) == (
        "identity.present",
        "denied",
        "refused:forbidden",
        "tools/call:echo",
    )


async def test_a_403_after_the_answer_is_a_denied_challenge(events: list[dict[str, Any]]) -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        return first_then_401(request) if request.headers["authorization"] == "first" else httpx.Response(403)

    auth = IdentityAuth(caller_of(ev(authorization="first"), ev(authorization="answer")), protocol="mcp")
    with pytest.raises(AgentCommError, match="forbidden"):
        await post(Wire(answer), auth)
    assert events[-1]["kind"] == "identity.challenge" and events[-1]["code"] == "refused:forbidden"


async def test_what_a_session_began_with_is_sent_once_and_not_asked_for_again() -> None:
    scheme = Presents([ev(authorization="asked")], repeat_last=True)
    begun = {"tools/call:echo": ev(authorization="begun")}
    auth = IdentityAuth(Caller("rec", scheme), protocol="mcp", begun=lambda task: begun.pop(task, None))
    wire = Wire()
    await post(wire, auth)
    await post(wire, auth)
    assert [r.headers["authorization"] for r in wire.seen] == ["begun", "asked"]
    assert len(scheme.requests) == 1


async def test_the_begun_evidence_is_not_used_for_the_answer_to_a_challenge() -> None:
    scheme = Presents([ev(authorization="answer")], repeat_last=True)
    asked: list[str] = []

    def begun(task: str) -> Evidence | None:
        asked.append(task)
        return ev(authorization="first")

    await post(Wire(first_then_401), IdentityAuth(Caller("rec", scheme), protocol="mcp", begun=begun))
    assert asked == ["tools/call:echo"] and scheme.requests[0]["phase"] == "challenge"


# --- redirects -------------------------------------------------------------------------------------


def hop_to(url: str) -> Callable[[httpx.Request], httpx.Response]:
    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/mcp":
            return httpx.Response(307, headers={"location": url})
        return httpx.Response(200, json={})

    return answer


async def test_a_redirect_to_another_origin_is_refused_before_it_is_sent() -> None:
    wire = Wire(hop_to("http://evil.test/mcp2"))
    auth = IdentityAuth(caller_of(ev(authorization="a")), protocol="mcp")
    async with httpx.AsyncClient(
        transport=wire.transport(),
        auth=auth,
        follow_redirects=True,
        event_hooks={"request": [_awaited(auth.guard_request)]},
    ) as client:
        with pytest.raises(AgentCommError, match="redirect"):
            await client.post("http://a.test/mcp", content=RPC)
    assert [r.url.host for r in wire.seen] == ["a.test"]


async def test_without_the_hook_a_hop_that_was_sent_is_reported_not_returned() -> None:
    wire = Wire(hop_to("http://evil.test/mcp2"))
    auth = IdentityAuth(caller_of(ev(authorization="a")), protocol="mcp")
    async with httpx.AsyncClient(transport=wire.transport(), auth=auth, follow_redirects=True) as client:
        with pytest.raises(AgentCommError, match="redirect"):
            await client.post("http://a.test/mcp", content=RPC)


async def test_a_same_origin_redirect_is_fine_for_a_bearer_and_refused_for_a_signature() -> None:
    bearer = IdentityAuth(caller_of(ev(authorization="a")), protocol="mcp")
    async with httpx.AsyncClient(
        transport=Wire(hop_to("http://a.test/other")).transport(), auth=bearer, follow_redirects=True
    ) as client:
        assert (await client.post("http://a.test/mcp", content=RPC)).status_code == 200
    signer = IdentityAuth(caller_of(ev(authorization="a"), signs=(ev(signature="s"),), cls=Signs), protocol="mcp")
    async with httpx.AsyncClient(
        transport=Wire(hop_to("http://a.test/other")).transport(), auth=signer, follow_redirects=True
    ) as client:
        with pytest.raises(AgentCommError, match="redirect"):
            await client.post("http://a.test/mcp", content=RPC)


def test_a_request_with_credentials_in_its_url_never_carries_evidence() -> None:
    auth = IdentityAuth(caller_of(ev(authorization="a")), protocol="mcp")
    request = httpx.Request("GET", "http://user:pw@a.test/mcp")
    with pytest.raises(AgentCommError, match="redirect"):
        auth.guard_request(request)
    plain = httpx.Request("GET", "http://a.test/mcp")
    auth.guard_request(plain)  # a request of no flow of this auth is not its business


def _awaited(call: Callable[[httpx.Request], None]) -> Callable[[httpx.Request], Any]:
    async def hook(request: httpx.Request) -> None:
        call(request)

    return hook


# --- the sync twin ---------------------------------------------------------------------------------


def test_the_sync_client_gets_the_same_presentation_and_the_same_one_answer() -> None:
    wire = Wire(first_then_401)
    scheme = Presents([ev(authorization="first"), ev(authorization="answer")], repeat_last=True)
    out = post_sync(wire, IdentityAuth(Caller("rec", scheme), protocol="mcp"))
    assert out.status_code == 200
    assert [r.headers["authorization"] for r in wire.seen] == ["first", "answer"]
    assert [r["phase"] for r in scheme.requests] == ["initiate", "challenge"]


def test_the_sync_client_uses_what_the_session_began_with_and_signs() -> None:
    scheme = Signs([ev(authorization="asked")], signs=[ev(signature="s")], repeat_last=True)
    begun = {"tools/call:echo": ev(authorization="begun")}
    auth = IdentityAuth(Caller("rec", scheme), protocol="mcp", begun=lambda task: begun.pop(task, None))
    wire = Wire()
    post_sync(wire, auth)
    (sent,) = wire.seen
    assert sent.headers["authorization"] == "begun" and sent.headers["signature"] == "s"
    assert scheme.requests == []  # the single-use credential was not asked for a second time


def test_a_sync_refusal_sends_nothing_and_a_second_401_is_a_denial() -> None:
    wire = Wire()
    with pytest.raises(AgentCommError, match="revoked"):
        post_sync(wire, IdentityAuth(Caller("rec", Presents([IdentityError("revoked")])), protocol="mcp"))
    assert wire.seen == []
    stubborn = Wire(lambda request: httpx.Response(401))
    with pytest.raises(AgentCommError, match="unauthorized"):
        post_sync(stubborn, IdentityAuth(caller_of(ev(authorization="a")), protocol="mcp"))
    forbidden = Wire(lambda request: httpx.Response(403))
    with pytest.raises(AgentCommError, match="forbidden"):
        post_sync(forbidden, IdentityAuth(caller_of(ev(authorization="a")), protocol="mcp"))
