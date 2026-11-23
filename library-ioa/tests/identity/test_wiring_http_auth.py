#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""What ``IdentityAuth`` puts on a request: headers always, ``_meta`` only in a JSON-RPC request."""

from __future__ import annotations

import json
from typing import Any, ClassVar

import httpx
import pytest
from mas.library.standard.lib.identity.caller import Caller
from mas.library.standard.lib.identity.contract import Evidence, IdentityScheme
from mas.library.standard.plugins.identity.http_auth import IdentityAuth
from mas.runtime.boundary.agentcomm.protocol import AgentCommError
from wiring_schemes import OneIdentity


class _TwoPhase(OneIdentity, IdentityScheme):
    """Different headers and different ``meta`` in the initiate and the challenge phase."""

    scheme: ClassVar[str] = "two_phase"
    capabilities: ClassVar[frozenset[str]] = frozenset()
    PARAMS_SCHEMA: ClassVar[dict] = {"type": "object"}

    @classmethod
    def from_spec(cls, params: Any) -> _TwoPhase:
        return cls()

    async def present(self, req: Any) -> Evidence:
        if req.get("phase") == "challenge":
            return Evidence(headers={"authorization": "Bearer answer"}, meta={"io.test/phase": "challenge"})
        return Evidence(
            headers={"authorization": "Bearer first", "dpop": "proof-initiate"},
            meta={"io.test/phase": "initiate", "io.test/first-only": "x"},
        )


def _auth(*, meta_carried: bool = True) -> IdentityAuth:
    return IdentityAuth(Caller("r", _TwoPhase()), protocol="mcp", meta_carried=meta_carried)


async def _send(method: str, url: str, *, content: bytes | None = None, auth: IdentityAuth | None = None):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/401" and request.headers.get("authorization") == "Bearer first":
            return httpx.Response(401, headers={"www-authenticate": 'Bearer realm="x"'})
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), auth=auth or _auth()) as client:
        await client.request(method, url, content=content)
    return seen


@pytest.mark.parametrize("method", ["GET", "DELETE"])
async def test_a_get_or_delete_with_no_body_carries_the_headers_and_is_not_refused(method: str) -> None:
    """Kills: meta_unsupported raised for the stream GET and the session DELETE (they have no body to carry meta)."""
    (sent,) = await _send(method, "http://a.test/mcp")
    assert sent.headers["authorization"] == "Bearer first" and sent.content == b""


async def test_a_json_rpc_response_the_client_posts_is_not_touched() -> None:
    """Kills: ``params._meta`` added to a response (a client answering a server request) or a refusal."""
    body = json.dumps({"jsonrpc": "2.0", "id": 7, "result": {}}).encode()
    (sent,) = await _send("POST", "http://a.test/mcp", content=body)
    assert sent.content == body and sent.headers["authorization"] == "Bearer first"


async def test_a_json_rpc_request_carries_the_meta_in_its_params_on_the_layer_alone() -> None:
    """Kills: ``meta_carried`` set but ``meta`` never written (dropped on the way to the server)."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"_meta": {"mine": 1}}}).encode()
    (sent,) = await _send("POST", "http://a.test/mcp", content=body)
    meta = json.loads(sent.content)["params"]["_meta"]
    assert meta["io.test/phase"] == "initiate" and meta["mine"] == 1
    assert int(sent.headers["content-length"]) == len(sent.content)


async def test_a_resend_after_a_challenge_carries_only_the_answers_evidence() -> None:
    """Kills: the first evidence's headers and meta left on the resend (the request was changed in place)."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode()
    first, again = await _send("POST", "http://a.test/401", content=body)
    assert first.headers["dpop"] == "proof-initiate"
    assert again.headers["authorization"] == "Bearer answer" and "dpop" not in again.headers
    assert json.loads(again.content)["params"]["_meta"] == {"io.test/phase": "challenge"}


async def test_a_call_with_no_meta_field_still_refuses_meta() -> None:
    """An LLM vendor call has nowhere to put ``meta``: refused, as before."""
    with pytest.raises(AgentCommError, match="meta_unsupported"):
        await _send("POST", "http://a.test/v1", content=b"{}", auth=_auth(meta_carried=False))


async def test_a_chunked_body_is_presented_for_and_sent_with_one_length() -> None:
    """Kills: ``del headers['content-length']`` raising KeyError on a body with no length (it was chunked)."""

    async def body():
        yield json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode()

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), auth=_auth()) as client:
        await client.post("http://a.test/mcp", content=body())
    (sent,) = seen
    assert "transfer-encoding" not in sent.headers and int(sent.headers["content-length"]) == len(sent.content)
    assert json.loads(sent.content)["params"]["_meta"]["io.test/phase"] == "initiate"


def _awaited(call: Any) -> Any:
    async def hook(request: httpx.Request) -> None:
        call(request)

    return hook


def _redirecting(auth: IdentityAuth, *, hooks: bool):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        match request.url.path:
            case "/other":
                return httpx.Response(307, headers={"location": "http://evil.test/steal"})
            case "/same":
                return httpx.Response(307, headers={"location": "/mcp/"})
        return httpx.Response(200, json={})

    options: dict[str, Any] = {"event_hooks": {"request": [_awaited(auth.guard_request)]}} if hooks else {}
    return seen, httpx.AsyncClient(transport=httpx.MockTransport(handler), auth=auth, follow_redirects=True, **options)


async def test_a_bare_auth_with_the_guard_never_sends_to_another_origin() -> None:
    """Kills: protection only in the call transport (a bare IdentityAuth forwarded to the redirect target)."""
    seen, client = _redirecting(_auth(), hooks=True)
    async with client:
        with pytest.raises(AgentCommError, match="refused:redirect"):
            await client.post("http://a.test/other", json={"jsonrpc": "2.0", "id": 1, "method": "m"})
        assert seen == ["http://a.test/other"]
        await client.post("http://a.test/same", json={"jsonrpc": "2.0", "id": 1, "method": "m"})  # same origin


async def test_a_bare_auth_without_the_hook_still_refuses_the_answer_of_a_foreign_hop() -> None:
    seen, client = _redirecting(_auth(), hooks=False)
    async with client:
        with pytest.raises(AgentCommError, match="refused:redirect"):
            await client.post("http://a.test/other", json={"jsonrpc": "2.0", "id": 1, "method": "m"})


class _Places(OneIdentity, IdentityScheme):
    """Evidence that says where it was made for: ``x-ev: for:<audience target>``; a signature names the path."""

    scheme: ClassVar[str] = "places"
    capabilities: ClassVar[frozenset[str]] = frozenset()
    PARAMS_SCHEMA: ClassVar[dict] = {"type": "object"}

    @classmethod
    def from_spec(cls, params: Any) -> _Places:
        return cls()

    async def present(self, req: Any) -> Evidence:
        if req.get("phase") == "sign":
            return Evidence(headers={"x-sig": "sig:" + req["request"]["url"]})
        return Evidence(headers={"x-ev": "for:" + req["audience"]["target"]})


class _PlacesSigning(_Places):
    scheme: ClassVar[str] = "places_signing"
    capabilities: ClassVar[frozenset[str]] = frozenset({"sign"})


def _places(cls: type[IdentityScheme] = _Places, caps: frozenset[str] = frozenset()) -> IdentityAuth:
    return IdentityAuth(Caller("r", cls()), protocol="http")


def _hopping(auth: IdentityAuth, answers: dict[str, str]):
    seen: list[tuple[str, str | None, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), request.headers.get("x-ev"), request.headers.get("x-sig")))
        where = answers.get(f"{request.url.host}{request.url.path}")
        return httpx.Response(307, headers={"location": where}) if where else httpx.Response(200)

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        auth=auth,
        follow_redirects=True,
        event_hooks={"request": [_awaited(auth.guard_request)]},
    )
    return seen, client


async def test_evidence_made_for_one_place_never_follows_a_redirect_to_a_place_presented_for_earlier() -> None:
    """Kills: a guard that remembered places across requests (evidence for A reached B once B had been used)."""
    seen, client = _hopping(_places(), {"a.test/r": "http://b.test/mcp"})
    async with client:
        await client.post("http://b.test/mcp", content=b"{}")  # b.test is a place this auth presented for
        seen.clear()
        with pytest.raises(AgentCommError, match="refused:redirect"):
            await client.post("http://a.test/r", content=b"{}")
        assert [url for url, *_ in seen] == ["http://a.test/r"]


async def test_hosts_and_ports_are_compared_as_places_not_as_spelled() -> None:
    """Kills: a case or default-port spelling that made the same place look like another (or another look the same)."""
    seen, client = _hopping(_places(), {"a.test/R": "HTTP://A.TEST:80/x", "a.test/P": "http://a.test:8080/x"})
    async with client:
        await client.post("http://a.test/R", content=b"{}")
        assert seen[-1][0].endswith("/x") and seen[-1][1] == "for:http://a.test"
        with pytest.raises(AgentCommError, match="refused:redirect"):
            await client.post("http://a.test/P", content=b"{}")


async def test_credentials_in_a_redirect_url_are_refused_by_both_guards() -> None:
    """Kills: a userinfo url that the call transport refused and the auth guard let through (or the other way)."""
    from library_ioa.plugins.http_layer.transport import HttpCallTransport

    seen, client = _hopping(_places(), {"a.test/U": "http://user:pw@a.test/mcp"})
    async with client:
        with pytest.raises(AgentCommError, match="refused:redirect"):
            await client.post("http://a.test/U", content=b"{}")
    transport = HttpCallTransport(url="http://a.test/mcp")
    transport.use_auth(_places())
    assert transport._same_origin in transport._options(sync=True)["event_hooks"]["request"]
    guard = transport._same_origin
    with pytest.raises(AgentCommError):
        guard(httpx.Request("POST", "http://user:pw@a.test/mcp"))
    guard(httpx.Request("POST", "HTTP://A.TEST:80/mcp"))  # the same place as the row's url


async def test_a_signing_scheme_refuses_a_same_origin_redirect_too() -> None:
    """Kills: a signature sent to a path the caller did not ask for, or made again for it (the re-sign was removed)."""
    signing = _places(_PlacesSigning, frozenset({"sign"}))
    seen, client = _hopping(signing, {"a.test/mcp": "/mcp/"})
    async with client:
        with pytest.raises(AgentCommError, match="refused:redirect"):
            await client.post("http://a.test/mcp", content=b"{}")
    assert [url for url, *_ in seen] == ["http://a.test/mcp"]


async def test_a_signing_scheme_is_still_refused_a_cross_origin_redirect() -> None:
    signing = _places(_PlacesSigning, frozenset({"sign"}))
    seen, client = _hopping(signing, {"a.test/mcp": "http://b.test/mcp"})
    async with client:
        with pytest.raises(AgentCommError, match="refused:redirect"):
            await client.post("http://a.test/mcp", content=b"{}")
    assert [url for url, *_ in seen] == ["http://a.test/mcp"]


def test_a_challenge_value_that_could_split_the_header_is_left_out_of_it() -> None:
    """Kills: a CR/LF or non-ASCII challenge value that reaches the WWW-Authenticate header (response splitting)."""
    from library_ioa.plugins.http_layer.challenge import www_authenticate

    value = www_authenticate({"nonce": "abc", "realm": "x\r\nSet-Cookie: a=b", "audience": "café"}, "Challenge")
    assert value == 'Challenge nonce="abc"' and "\r" not in value and "\n" not in value


class _Header(_Places):
    scheme: ClassVar[str] = "header"

    async def present(self, req: Any) -> Evidence:
        return Evidence(headers={"X-Row": "evidence"})


async def test_a_row_header_that_names_an_evidence_header_is_refused_not_applied_over_it() -> None:
    """Kills: a static header that replaces the identity evidence after the auth layer signed it."""
    auth = IdentityAuth(Caller("r", _Header()), protocol="http", static_headers=["x-row"])
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)), auth=auth) as client:
        with pytest.raises(AgentCommError, match="header_collision"):
            await client.post("http://a.test/mcp", content=b"{}")


async def test_the_rows_headers_are_on_the_request_before_the_auth_layer_signs_it() -> None:
    """Kills: static headers added after signing, so a signature never covers them."""
    from library_ioa.plugins.http_layer.transport import HttpCallTransport

    covered: list[dict[str, str]] = []

    class Signing(_PlacesSigning):
        async def present(self, req: Any) -> Evidence:
            if req.get("phase") == "sign":
                covered.append(dict(req["request"]["headers"]))
            return await super().present(req)

    transport = HttpCallTransport(url="http://a.test/mcp", headers={"X-Static": "kept"})
    transport.use_auth(IdentityAuth(Caller("r", Signing()), protocol="http"))
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200)),
        **{k: v for k, v in transport._options(sync=False).items() if k in ("auth", "event_hooks")},
    ) as client:
        await client.post("http://a.test/mcp", content=b"{}")
    assert covered and covered[0].get("x-static") == "kept"


def test_a_second_auth_layer_on_one_transport_is_refused() -> None:
    """Kills: two identity layers on one row, the second silently replacing the first."""
    from library_ioa.plugins.http_layer.transport import HttpCallTransport

    transport = HttpCallTransport(url="http://a.test/mcp")
    transport.use_auth(_places())
    with pytest.raises(AgentCommError, match="installed already"):
        transport.use_auth(_places())


class _Signer(_Places):
    """A signing scheme: an `authorization` bearer on the request, and a signature over what it was asked to sign."""

    scheme: ClassVar[str] = "signer"
    capabilities: ClassVar[frozenset[str]] = frozenset({"sign"})
    VIEWS: ClassVar[list[dict[str, Any]]] = []

    async def present(self, req: Any) -> Evidence:
        if req.get("phase") == "sign":
            view = dict(req["request"])
            self.VIEWS.append(view)
            return Evidence(headers={"x-signature": f"{view['body_sha256']}@{view['url']}"})
        return Evidence(headers={"authorization": "Bearer evidence"})


def _signed_transport(url: str, headers: dict[str, str], *, follow_redirects: bool = False):
    from library_ioa.plugins.http_layer.transport import HttpCallTransport
    from mas.library.standard.plugins.identity.layer import IdentityLayer

    transport = HttpCallTransport(url=url, headers=headers, follow_redirects=follow_redirects)
    IdentityLayer.wrap(
        transport,
        {"identity": "r", "scheme": "signer", "params": {}, "schemes": {"signer": _Signer}},
        call_kinds=("llm",),
    )
    return transport


async def test_a_static_authorization_header_on_a_row_is_refused_and_nothing_is_sent() -> None:
    """Kills: a row's own `authorization` replacing the identity's after the layer presented (and signed) it."""
    from library_ioa.plugins.http_layer.transport import HttpCallTransport
    from mas.runtime.secrets import SecretRef
    from mas.runtime.secrets.errors import SecretRefError
    from wiring_servers import FakeMcp

    with pytest.raises(SecretRefError):  # a literal credential header is refused when the row is built
        HttpCallTransport(url="http://a.test/mcp", headers={"Authorization": "Bearer static"})
    from mas.library.standard.testing.identity_fakes import bound_secrets

    with FakeMcp() as server, bound_secrets({"row-auth": "Bearer static"}):
        transport = _signed_transport(server.url + "/mcp", {"Authorization": SecretRef(name="row-auth")})
        try:
            with pytest.raises(AgentCommError, match="header_collision"):
                await transport.aclient.post(server.url + "/mcp", content=b'{"jsonrpc":"2.0","id":1,"method":"m"}')
        finally:
            transport.stop()
        assert server.posts() == []


async def test_a_signed_request_reaches_the_server_with_the_evidence_the_signature_and_the_rows_headers() -> None:
    """Kills: evidence or signature lost on the way to a real server; the row's headers dropped or signed too late."""
    import hashlib

    from wiring_servers import FakeMcp

    body = b'{"jsonrpc":"2.0","id":1,"method":"m"}'
    _Signer.VIEWS.clear()
    with FakeMcp() as server:
        transport = _signed_transport(server.url + "/mcp", {"X-Static": "kept"})
        try:
            await transport.aclient.post(server.url + "/mcp", content=body)
        finally:
            transport.stop()
        (seen,) = server.posts()
    assert seen.headers["authorization"] == "Bearer evidence" and seen.headers["x-static"] == "kept"
    digest = hashlib.sha256(body).hexdigest()
    assert seen.headers["x-signature"] == f"{digest}@{server.url}/mcp"
    assert _Signer.VIEWS[0]["headers"]["x-static"] == "kept"  # the signature was made over a request that has them


async def test_the_call_transport_never_sends_a_signed_request_to_another_path_of_the_same_origin() -> None:
    """Kills: a signing scheme's evidence (made for `/mcp`) sent on to `/mcp/` by a followed redirect."""
    from wiring_servers import FakeMcp, Reply, Seen

    def gate(request: Seen) -> Reply | None:
        return Reply(307, headers={"location": "/mcp/"}) if request.path == "/mcp" else None

    with FakeMcp(gate=gate) as server:
        transport = _signed_transport(server.url + "/mcp", {}, follow_redirects=True)
        try:
            with pytest.raises(AgentCommError, match="refused:redirect"):
                await transport.aclient.post(server.url + "/mcp", content=b'{"jsonrpc":"2.0","id":1,"method":"m"}')
        finally:
            transport.stop()
        assert [post.path for post in server.posts()] == ["/mcp"]  # the hop was not sent
