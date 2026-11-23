#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The identity layer on an HTTP call: an ``httpx.Auth`` that asks the caller before each request.

For every request it builds a ``PresentRequest`` (who is acting, the audience, the operation read from the JSON-RPC
envelope of the body), asks the caller, and applies the evidence's headers. It never gives a scheme the body, only the
digest (``sign`` schemes), the operation name and a ``request_id`` that is unique to the request. When the peer answers
401 it asks once more with ``phase="challenge"`` and the peer shape of :meth:`IdentityAuth._peer` (the
``WWW-Authenticate`` parameters as ``challenge``, the raw value as ``wire``), and sends once more; a second 401 is a
denial. A 403 is a denial at once, told as ``refused:forbidden``.

Fail closed: any failure before the request is sent raises an ``AgentCommError`` whose text is a value-free code, and
nothing is sent. The sync flow serves the sync client through :meth:`Caller.present_blocking`; it refuses a call made
from a thread that runs an event loop.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import time
import uuid
from collections.abc import AsyncGenerator, Callable, Collection, Generator, Mapping
from typing import Any
from urllib.parse import urlsplit

import httpx
from mas.library.standard.lib.identity import observe
from mas.library.standard.lib.identity.caller import Caller
from mas.library.standard.lib.identity.contract import (
    CAP_SIGN,
    Audience,
    Evidence,
    IdentityError,
    PresentRequest,
    Principal,
    RequestView,
)
from mas.library.standard.lib.identity.principal import current_principal
from mas.library.standard.lib.identity.refusal import denial
from mas.runtime.boundary.agentcomm.protocol import AgentCommError

PLACE_KEY = "mas.identity.place"
_MAX_PARAM = 256
_DEFAULT_PORTS = {"http": 80, "https": 443}
#: The statuses, other than the 401 a challenge answers, in which a peer refuses the caller's authority, and the code.
_WIRE_REFUSALS = {403: "forbidden"}


def task_of(request: httpx.Request) -> str:
    """The operation a request carries: an MCP ``method`` (``tools/call:<tool>`` for a tool call), else ``""``.

    Read from the JSON-RPC envelope of the body. The body is never handed on.
    """
    body = request.content
    if not body:
        return ""
    try:
        data = json.loads(body)
    except ValueError:
        return ""
    if not isinstance(data, dict) or not isinstance(data.get("method"), str):
        return ""
    method = data["method"]
    params = data.get("params")
    if method == "tools/call" and isinstance(params, dict) and isinstance(params.get("name"), str):
        return f"tools/call:{params['name']}"
    return method


def _with_meta(body: bytes, meta: Mapping[str, Any]) -> bytes | None:
    """``body`` with ``meta`` in ``params._meta``, when it is a JSON-RPC request; else ``None`` (nothing to carry it).

    A field the request already has in ``_meta`` wins. An empty body (the stream GET, the session DELETE) and a
    JSON-RPC response or notification-less object a client posts back (it has no ``method``) are not requests: the
    evidence's headers are all they carry, and their bodies are never touched. A request whose ``params`` cannot
    hold ``_meta`` is a refusal, not a silent drop.
    """
    if not body:
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("method"), str):
        return None
    params = data.get("params", {})
    if not isinstance(params, dict) or not isinstance(params.get("_meta", {}), dict):
        raise IdentityError("unsatisfiable", "meta_unsupported")
    params["_meta"] = {**meta, **params.get("_meta", {})}
    data["params"] = params
    return json.dumps(data, separators=(",", ":")).encode()


_AUTH_PARAM = re.compile(r'([A-Za-z_][A-Za-z0-9_./-]*)="((?:[^"\\]|\\.)*)"')


def parse_auth_params(value: str) -> dict[str, Any]:
    """The quoted ``key="value"`` parameters of a ``WWW-Authenticate`` value, as a mapping (none: empty).

    Only what the contract's challenge can hold is kept: its reserved keys with their types (``requirements`` and
    ``schemes`` are space-separated lists, ``expires_at`` a number) and keys namespaced with ``/``. A peer's other
    parameters (``error``, ``scope``) are not a challenge the wallet reads, and a value past 256 characters is dropped.
    """
    out: dict[str, Any] = {}
    for key, text in _AUTH_PARAM.findall(value):
        text = re.sub(r"\\(.)", r"\1", text)
        if not text or len(text) > _MAX_PARAM:
            continue
        if key in ("requirements", "schemes"):
            out[key] = text.split()
        elif key == "expires_at":
            try:
                number = float(text)
            except ValueError:
                continue
            if math.isfinite(number):
                out[key] = number
        elif key in ("nonce", "audience", "realm") or "/" in key:
            out[key] = text
    return out


def _signed_url(url: httpx.URL) -> str:
    """The url a signature covers: scheme, host with its brackets and a non-default port, path and query.

    No credentials and no fragment (a fragment never reaches the server).
    """
    return f"{url.scheme}://{url.netloc.decode('ascii')}{url.raw_path.decode('ascii')}"


def origin(url: httpx.URL) -> str:
    parts = urlsplit(str(url))
    return f"{parts.scheme}://{parts.netloc}"


class IdentityAuth(httpx.Auth):
    """Authorize each request of an HTTP call transport through ``caller``."""

    requires_request_body = True
    requires_response_body = False

    def __init__(
        self,
        caller: Caller,
        *,
        protocol: str,
        audience: Mapping[str, str] | None = None,
        actor: str = "",
        use: str = "",
        meta_carried: bool = False,
        sections: Mapping[str, str] | None = None,
        known_peer: Callable[[], Mapping[str, Any]] | None = None,
        begun: Callable[[str], Evidence | None] | None = None,
        refused: Callable[[str, BaseException], None] | None = None,
        static_headers: Collection[str] = (),
    ) -> None:
        self._caller = caller
        self._static = frozenset(name.lower() for name in static_headers)
        self._protocol = protocol
        self._audience = dict(audience) if audience else None
        self._actor = actor
        self._use = use
        self._meta_carried = meta_carried
        self._sections = dict(sections or {})
        self._known_peer = known_peer
        self._begun = begun
        self._refused = refused

    @property
    def _signs(self) -> bool:
        return CAP_SIGN in self._caller.capabilities

    def _deny(self, exc: BaseException, task: str) -> AgentCommError:
        """The value-free denial for ``exc``, also told to whoever hears refusals (the hook of a held session).

        The refusal is kept under the ``task`` of the request that was refused, so the call that made it, and no other,
        is the one that gets it back.
        """
        denied = denial(exc)
        if self._refused is not None:
            self._refused(task, denied)
        return denied

    # --- building the request to the caller ---------------------------------------------------

    def _principal(self) -> Principal:
        principal = current_principal()
        if "actor" not in principal and self._actor:
            principal["actor"] = self._actor
        return principal

    def _request(self, request: httpx.Request, *, phase: str, peer: Mapping[str, Any] | None = None) -> PresentRequest:
        audience = (
            Audience(protocol=self._audience["protocol"], target=self._audience["target"])
            if self._audience
            else Audience(protocol=self._protocol, target=origin(request.url))
        )
        req = PresentRequest(principal=self._principal(), audience=audience, task=task_of(request), phase=phase)
        if self._use:
            req["identity"] = self._use
        if peer is not None:
            req["peer"] = dict(peer)
        elif self._known_peer is not None:
            # what a held session already learned of the peer (an MCP server's initialize declarations)
            known = self._known_peer()
            if known:
                req["peer"] = dict(known)
        return req

    def _signed(self, request: httpx.Request, req: PresentRequest) -> PresentRequest:
        body = request.content or b""
        signed = PresentRequest(**req)
        signed["phase"] = "sign"
        signed["request"] = RequestView(
            method=request.method,
            url=_signed_url(request.url),
            headers={k.lower(): v for k, v in request.headers.items() if k.lower() != "authorization"},
            body_sha256=hashlib.sha256(body).hexdigest(),
            request_id=uuid.uuid4().hex,
        )
        return signed

    def _apply(self, request: httpx.Request, evidence: Evidence) -> httpx.Request:
        """A new request: ``request`` with the evidence's headers, and its ``meta`` in the ``_meta`` of a JSON-RPC body.

        ``request`` itself is never changed. A resend after a challenge is built from the original again, so it
        carries only the answer's evidence and none of the first. ``meta`` is a protocol field the MCP request
        carries in ``_meta``; a call with no such field (an LLM vendor) has nowhere to put it. A request that is not
        a JSON-RPC request (an empty body, a client's answer to a server request) carries the headers only.
        """
        meta = evidence.get("meta")
        if meta and not self._meta_carried:
            raise IdentityError("unsatisfiable", "meta_unsupported")
        headers = httpx.Headers(request.headers)
        for name, value in evidence.get("headers", {}).items():
            if name.lower() in self._static:  # the row's own header would replace the evidence after it was signed
                raise IdentityError("invalid", "header_collision")
            headers[name] = value
        body = request.content
        carried = _with_meta(body, meta) if meta else None
        if carried is not None:
            body = carried
        if body:
            # httpx writes the new length: a chunked body has no length to drop, and must not keep its chunking
            headers.pop("content-length", None)
            headers.pop("transfer-encoding", None)
        return httpx.Request(
            request.method, request.url, headers=headers, content=body or None, extensions=dict(request.extensions)
        )

    @staticmethod
    def _peer(response: httpx.Response) -> dict[str, Any]:
        """The one shape of a challenge phase: ``challenge`` (the ``WWW-Authenticate`` parameters), ``wire`` (the raw
        header value, when there is one) and ``transport`` (what HTTP itself said)."""
        raw = response.headers.get("www-authenticate", "")
        peer: dict[str, Any] = {"challenge": parse_auth_params(raw)}
        if raw:
            peer["wire"] = raw
        peer["transport"] = {"status": response.status_code, "headers": {"www-authenticate": raw}}
        return peer

    # --- redirects ----------------------------------------------------------------------------

    def _place(self, url: httpx.URL) -> str:
        """Where a request goes, as far as the evidence is bound to it: scheme, host and port (lower case, a default
        port made explicit), and the path too for a scheme that signs, whose signature covers it."""
        scheme = url.scheme.lower()
        port = url.port or _DEFAULT_PORTS.get(scheme, 0)
        place = f"{scheme}://{url.host.lower()}:{port}"
        return f"{place}{url.raw_path.decode('ascii')}" if self._signs else place

    def _stamp(self, sent: httpx.Request) -> None:
        """Write the place this evidence was made for on the request. httpx hands a request's ``extensions`` to the
        requests a redirect makes from it, so every hop carries the place of the flow that presented, and no other."""
        sent.extensions[PLACE_KEY] = self._place(sent.url)

    def _check(self, request: httpx.Request) -> str:
        """``""`` when ``request`` may carry the evidence it has; else why not (``userinfo``, ``place``)."""
        if request.url.userinfo:
            return "userinfo"
        stamp = request.extensions.get(PLACE_KEY)
        return "" if stamp is None or stamp == self._place(request.url) else "place"

    def guard_request(self, request: httpx.Request) -> None:
        """A ``request`` event hook: refuse a request to another place than the evidence was made for (a redirect hop).

        The credentials this auth put on a request would go with a redirect to wherever the answer names. Add it to a
        client that follows redirects (``event_hooks={"request": [auth.guard_request]}``; the HTTP call transport
        installs it on every client it makes, an async client through a coroutine that calls it). Each hop is compared
        with the place of its own flow, written on the request when it was presented for, so a place presented for
        earlier does not let evidence travel there.
        A signing scheme refuses a redirect to another path too, a same-origin one included: the signature covers the
        path, and one is not made again for a place the caller did not ask for. A request that carries no evidence of
        this auth is not its business. Over a URL with credentials in it the evidence is refused too, as the call
        transport does.
        """
        if self._check(request):
            raise denial(IdentityError("refused", "redirect"))

    def _no_hop_left(self, response: httpx.Response) -> None:
        """Without the hook the hop was already sent; say so instead of returning what it answered."""
        for hop in (*response.history, response):
            self.guard_request(hop.request)

    # --- async flow ---------------------------------------------------------------------------

    async def _acquire(
        self, request: httpx.Request, *, phase: str, peer: Mapping[str, Any] | None = None
    ) -> httpx.Request:
        """The request to send: ``request`` with one presentation's headers and ``_meta`` (the signature last)."""
        # one thread of control: nothing below sends until every failure has been turned into a denial
        task = task_of(request)
        try:
            req = self._request(request, phase=phase, peer=peer)
            begun = self._begun(req["task"]) if phase == "initiate" and self._begun is not None else None
            evidence = begun or await self._caller.present(req, sections=self._sections)
            out = self._apply(request, evidence)
            if self._signs:
                out = self._apply(out, await self._caller.present(self._signed(out, req)))
            return out
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise self._deny(exc, task) from None

    async def async_auth_flow(self, request: httpx.Request) -> AsyncGenerator[httpx.Request, httpx.Response]:
        await request.aread()  # overriding the flow means reading the body ourselves (a streamed body is not read yet)
        sent = await self._acquire(request, phase="initiate")
        self._stamp(sent)
        began = time.monotonic()
        response = yield sent
        self._no_hop_left(response)
        self._wire_refused(response, task_of(request), began)
        if response.status_code != 401:
            return
        sent = await self._acquire(request, phase="challenge", peer=self._peer(response))
        self._stamp(sent)
        again = yield sent
        self._answered(again, task_of(request))

    def _seen(self, direction: str) -> dict[str, str]:
        return {
            "actor": self._principal().get("actor", ""),
            "record": self._caller.record,
            "scheme": self._caller.scheme.scheme,
            "protocol": self._protocol,
            "direction": direction,
        }

    def _wire_refused(self, response: httpx.Response, task: str, began: float) -> None:
        """The first request was refused outright (a 403): the presentation is denied, as an ``identity.present`` event
        with the closed code."""
        code = _WIRE_REFUSALS.get(response.status_code)
        if code is None:
            return
        observe.emit(
            "identity.present",
            outcome="denied",
            code=f"refused:{code}",
            phase="initiate",
            latency_ms=round((time.monotonic() - began) * 1000, 2),
            operation=task,
            actor=self._principal().get("actor", ""),
            record=self._caller.record,
            scheme=self._caller.scheme.scheme,
            protocol=self._protocol,
        )
        raise self._deny(IdentityError("refused", code), task)

    def _answered(self, again: httpx.Response, task: str) -> None:
        """Publish how the peer took the answer to its challenge; a second 401 (or a 403) is a denial."""
        seen = self._seen("answered")
        code = _WIRE_REFUSALS.get(again.status_code)
        if again.status_code == 401:
            code = "unauthorized"
        if code is not None:
            observe.emit("identity.challenge", outcome="denied", code=f"refused:{code}", **seen)
            raise self._deny(IdentityError("refused", code), task)
        observe.emit("identity.challenge", outcome="ok", **seen)

    # --- sync flow ----------------------------------------------------------------------------

    def _acquire_blocking(
        self, request: httpx.Request, *, phase: str, peer: Mapping[str, Any] | None = None
    ) -> httpx.Request:
        task = task_of(request)
        try:
            req = self._request(request, phase=phase, peer=peer)
            begun = self._begun(req["task"]) if phase == "initiate" and self._begun is not None else None
            evidence = begun or self._caller.present_blocking(req, sections=self._sections)
            out = self._apply(request, evidence)
            if self._signs:
                out = self._apply(out, self._caller.present_blocking(self._signed(out, req)))
            return out
        except Exception as exc:
            raise self._deny(exc, task) from None

    def sync_auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        request.read()
        sent = self._acquire_blocking(request, phase="initiate")
        self._stamp(sent)
        began = time.monotonic()
        response = yield sent
        self._no_hop_left(response)
        self._wire_refused(response, task_of(request), began)
        if response.status_code != 401:
            return
        sent = self._acquire_blocking(request, phase="challenge", peer=self._peer(response))
        self._stamp(sent)
        again = yield sent
        self._answered(again, task_of(request))
