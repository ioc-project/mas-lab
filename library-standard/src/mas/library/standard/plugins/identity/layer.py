#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The ``identity`` layer: one layer that puts the identity contract on a stack, at the protocol edge.

A row names it in a stack and a Flavour binds the stack to an agent. The runtime, the protocol libraries and the other
layers never import this module: they know a layer, a session hook and a peer challenge, nothing about identity.

Where it sits
=============

* **Send stack** (``agent_comm``, A2A over HTTP or slimrpc): above the protocol layer. ``asend`` asks the caller and
  forwards the envelope with the evidence's headers and metadata, and answers a peer's challenge once.
  ``serve(handler)`` verifies a caller before the handler runs.
* **Call stack** (``tool`` MCP, ``llm``): below the protocol layer. ``wrap(inner, ...)`` installs an ``httpx.Auth``
  on the HTTP call transport, so each request is presented for, and a 401 is answered once. For MCP it also offers
  the MCP layer above a session hook: ``begin`` (the scheme's ``advertise`` sections) and ``established`` (the scheme's
  ``handshake`` on the session).

Layer parameters (the host inlines them from the identity record): ``identity`` (the record id), ``scheme``,
``params`` (the scheme's parameters; a secret parameter is a ``SecretRef``), ``audience`` (``{protocol, target}``,
optional), ``use`` (the wallet identity to present, optional: absent, the wallet selects), ``scope`` (``agent``, the
default: the layers of one agent built from equal parameters share one wallet; ``process``: layers of any agent do, and
the wallet is held by no agent in particular, so no agent's tools manage it; either way one revocation reaches every
layer, within one ``holder_scope``), ``holder_scope`` (the token the host stamps once per bind,
which keeps the wallets of two runs in one process apart; empty outside a host).

Fail closed
===========

If anything goes wrong while an evidence is acquired (a scheme error of any kind, a malformed evidence, a timeout) no
call below is made, and the caller gets an ``AgentCommError`` whose text is a value-free code
(``identity: refused:wrong_audience``).
Nothing about a credential is in an error, an event or a log.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
import uuid
import weakref
from collections.abc import Mapping
from typing import Any, ClassVar
from urllib.parse import urlsplit

from mas.library.standard.lib.identity import holders, observe
from mas.library.standard.lib.identity.caller import DEFAULT_TIMEOUT, Caller
from mas.library.standard.lib.identity.contract import (
    CAP_HANDSHAKE,
    CAP_SIGN,
    CAP_VERIFY,
    CHALLENGE_KEYS,
    Audience,
    ChallengeRequired,
    Evidence,
    HandshakeContext,
    IdentityError,
    IdentityScheme,
    Inbound,
    PresentRequest,
    Principal,
    RequestView,
)
from mas.library.standard.lib.identity.principal import CapturedPrincipal, bind, current_principal
from mas.library.standard.lib.identity.refusal import denial
from mas.library.standard.lib.identity.rules import validate
from mas.library.standard.lib.stack import (
    SESSION_HOOK_META,
    SESSION_TOKEN,
    AuthorizableTransport,
    Envelope,
    Handler,
    Layer,
    PeerChallenge,
    SessionHook,
    SessionInfo,
    Unauthenticated,
    conversation_id,
)
from mas.library.standard.plugins.identity.http_auth import IdentityAuth
from mas.runtime.boundary.agentcomm.protocol import AgentCommError

SCHEME_TYPE = "identity_scheme"
HEADERS_KEY = "mas.headers"
EXTENSIONS_HEADER = "a2a-extensions"

# What the protocol adapters carry. The contract names none of them: a protocol declares its own.
_MCP_SECTIONS: dict[str, str] = {"mcp.capabilities.experimental": "map", "mcp.capabilities.extensions": "map"}
_A2A_SECTIONS: dict[str, str] = {"a2a.extensions": "list"}
_SHAPES = {**_MCP_SECTIONS, **_A2A_SECTIONS}
_TEXT_PROTOCOLS = frozenset({"a2a"})
_CARRY_KEYS = frozenset({"a2a.metadata_key", "a2a.data_key", "http.auth_scheme"})
_PROTOCOL_BY_KIND = {"tool": "mcp", "llm": "llm"}

MAX_ID = 256
MAX_FACT_SCOPES = 64
#: How long what ``begin`` presented waits for the request that opens the session, and how long a refusal is kept.
_KEEP_S = 30.0

# a wallet lives as long as something presents with it (a transport, a layer); the table never keeps one alive
_SHARED: weakref.WeakValueDictionary[str, Caller] = weakref.WeakValueDictionary()
_SHARED_LOCK = threading.Lock()


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def reset_shared() -> None:
    """Forget every process-wide wallet (tests)."""
    with _SHARED_LOCK:
        _SHARED.clear()


def lookup_scheme(name: str, extra: Mapping[str, type] | None = None) -> type | None:
    """The scheme class named ``name``: ``extra`` first, then the ``identity_scheme`` plugin registry."""
    if extra is not None and name in extra:
        return extra[name]
    from mas.runtime.registry import PluginUnavailable, get_registry

    info = get_registry().resolve_by_type(SCHEME_TYPE, name)
    if info is None:
        return None
    try:
        return info.load_class()
    except (PluginUnavailable, ImportError):
        return None


def _refusal_task(task: str) -> str:
    """The key a refusal is kept under. A request that sets a session up (``initialize``, ``server/discover``, a
    notification, the stream ``GET``, the ``DELETE``: no method of a call) belongs to whatever call opens or holds the
    session, so they share one key (``""``) that every call asks for after its own."""
    return "" if task in {"", "initialize", "server/discover"} or task.startswith("notifications/") else task


class _Facts:
    """What the peers declared, per scope: the next request's ``peer``. Bounded."""

    def __init__(self) -> None:
        self._facts: dict[str, dict[str, Any]] = {}

    def get(self, scope: str) -> dict[str, Any] | None:
        return self._facts.get(scope)

    def put(self, scope: str, peer: Mapping[str, Any]) -> None:
        self._facts[scope] = dict(peer)
        while len(self._facts) > MAX_FACT_SCOPES:
            del self._facts[next(iter(self._facts))]

    def clear(self) -> None:
        self._facts.clear()


class _Hook(SessionHook):
    """What the identity layer contributes to a protocol's session setup.

    ``begin`` advertises what the scheme declares; ``established`` runs the scheme's handshake on the session and
    returns the metadata for its later calls; ``scope`` keys a session to the acting principal. A hook made for one send
    takes the principal then (``captured``); one made for a transport takes it on every call. ``required`` says the peer
    asked for the handshake (an auth-required task), so a scheme that cannot is a denial; a held MCP session just has
    nothing to settle. ``facts`` records what the peer declared, per scope, for the next request's ``peer``;
    ``known`` is what the send already knew of the peer (an agent card's extension URIs).
    """

    def __init__(
        self,
        caller: Caller,
        audience: Audience,
        *,
        task: str,
        actor: str,
        use: str,
        captured: CapturedPrincipal | None,
        handshakes: bool,
        required: bool = False,
        facts: _Facts | None = None,
        known: Mapping[str, Any] | None = None,
        owner: IdentityLayer | None = None,
    ) -> None:
        # a hook the transport holds keeps its layer alive, so the layer is there to hear a revocation
        self._owner = owner
        self._caller = caller
        self._audience = audience
        self._task = task
        self._actor = actor
        self._use = use
        self._captured = captured
        self._handshakes = handshakes
        self._required = required
        self._facts = facts
        self._known = dict(known or {})
        self._lock = (
            threading.Lock()
        )  # a hook is shared by the calls of a session: its two small tables change together
        self._begun: dict[str, tuple[float, Evidence]] = {}
        self._refusals: dict[str, tuple[float, BaseException]] = {}

    def _principal(self) -> Principal:
        principal = Principal(**(self._captured.principal if self._captured is not None else current_principal()))
        if "actor" not in principal and self._actor:
            principal["actor"] = self._actor
        return principal

    def _present_request(self, task: str) -> PresentRequest:
        req = PresentRequest(principal=self._principal(), audience=self._audience, task=task)
        if self._use:
            req["identity"] = self._use
        known = self._facts.get(self.scope()) if self._facts is not None else None
        if known:
            req["peer"] = dict(known)
        return req

    async def begin(self, sections: frozenset[str], task: str) -> Mapping[str, Any]:
        wanted = {name: _SHAPES[name] for name in sections if name in _SHAPES}
        try:
            evidence = await self._caller.present(self._present_request(task), sections=wanted)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise denial(exc) from None
        # the request that opens the session is this presentation (see ``take_begun``): it is not made twice
        now = time.monotonic()
        with self._lock:
            self._begun = {k: v for k, v in self._begun.items() if now - v[0] < _KEEP_S}
            self._begun[_canonical([self.scope(), task])] = (now, evidence)
            while len(self._begun) > MAX_FACT_SCOPES:
                del self._begun[next(iter(self._begun))]
        return dict(evidence.get("advertise", {}))

    def _refusal_key(self, task: str) -> str:
        """A call's refusal is kept under its operation; a setup request's under the session it set up (the token the
        protocol layer set), so two calls opening sessions at once never pick up each other's."""
        kept = _refusal_task(task)
        return _canonical([self.scope(), kept, id(SESSION_TOKEN.get()) if not kept else 0])

    def refused(self, task: str, refusal: BaseException) -> None:
        """Keep the refusal the transport's auth raised for ``task`` in this scope (see :meth:`take_refusal`)."""
        now = time.monotonic()
        key = self._refusal_key(task)
        with self._lock:
            self._refusals = {k: v for k, v in self._refusals.items() if now - v[0] < _KEEP_S}
            self._refusals[key] = (now, refusal)
            while len(self._refusals) > MAX_FACT_SCOPES:
                del self._refusals[next(iter(self._refusals))]

    def take_refusal(self, task: str = "") -> BaseException | None:
        with self._lock:
            found = self._refusals.pop(self._refusal_key(task), None)
        return found[1] if found is not None and time.monotonic() - found[0] < _KEEP_S else None

    def take_begun(self, task: str) -> Evidence | None:
        """The evidence ``begin`` presented for ``task`` in this scope, once, if it is still fresh; else ``None``.

        A scheme with a single-use credential is asked once for the request that opens a session, not once to advertise
        and once to send.
        """
        with self._lock:
            found = self._begun.pop(_canonical([self.scope(), task]), None)
        if found is None or time.monotonic() - found[0] >= _KEEP_S or not self._caller.is_fresh(found[1]):
            return None  # a credential past its own expiry is asked for again, not sent
        return found[1]

    async def established(self, session: SessionInfo) -> Mapping[str, Any]:
        try:
            if self._facts is not None:
                self._facts.put(self.scope(), session.peer)
            if not self._handshakes:
                if self._required:
                    raise IdentityError("unsatisfiable", "no_handshake")
                return {}
            ctx = HandshakeContext(
                record=self._caller.record,
                principal=self._principal(),
                audience=self._audience,
                task=self._task,
                peer={**self._known, **session.peer},
                channel=session,
            )
            if self._use:
                ctx["identity"] = self._use
            evidence = await self._caller.handshake(ctx)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise denial(exc) from None
        return dict(evidence.get("meta", {}))

    def scope(self) -> str:
        principal = self._principal()
        return _canonical([principal.get("actor", ""), principal.get("subject", ""), principal.get("session", "")])


class IdentityLayer(Layer):
    """Present an identity on the way down, verify one on the way up. See the module text."""

    needs_lower: ClassVar[bool] = True
    # a call stack: below the protocol layer, above the http transport that has priority 0
    protocol_layer: ClassVar[bool] = True
    per: ClassVar[str] = "destination"
    call_kinds: ClassVar[tuple[str, ...]] = ("llm", "tool")
    priority: ClassVar[int | None] = 10
    # over a transport that has no HTTP (slimrpc) the headers it presents cross as message metadata
    HEADERS_IN_METADATA: ClassVar[bool] = True

    def __init__(
        self,
        *,
        identity: str,
        scheme: str,
        params: Mapping[str, Any] | None = None,
        audience: Mapping[str, str] | None = None,
        agent_id: str = "",
        use: str = "",
        scope: str = "agent",
        holder_scope: str = "",
        timeout: float = DEFAULT_TIMEOUT,
        schemes: Mapping[str, type] | None = None,
        caller: Caller | None = None,
        **unknown: Any,
    ) -> None:
        super().__init__()
        if unknown:
            raise AgentCommError(f"identity: unknown parameter {sorted(unknown)[0]!r}")
        if scope not in ("agent", "process"):
            raise AgentCommError("identity: scope is agent or process")
        self.identity = str(identity)
        self.scheme = str(scheme)
        self.agent_id = str(agent_id or "")
        self.holder_scope = str(holder_scope or "")
        self._use = str(use or "")
        self._audience = dict(audience) if audience else None
        self._facts_stores: list[_Facts] = []
        if caller is not None:
            self._caller = caller
        else:
            self._caller = self._build(dict(params or {}), scope, timeout, schemes)
        self._capabilities: frozenset[str] = self._caller.capabilities
        # a wallet shared by agents (``process``) belongs to none of them: no agent's model manages it, the gate reads
        # it
        self._unhold = holders.register(
            self.agent_id if scope == "agent" else "", self.identity, self._caller, self.holder_scope
        )
        # a session opened with a credential outlives its revocation unless it is closed: tell every hook of this layer
        # when an identity is revoked. Held weakly, so a layer that is gone is not kept alive by the caller.
        self._hooks: weakref.WeakSet[_Hook] = weakref.WeakSet()
        me = weakref.ref(self)

        def revoked(_identity: str) -> None:
            layer = me()
            if layer is not None:
                layer.invalidate_sessions()

        self._forget = self._caller.on_revoked(revoked)
        # a layer that is collected without ``stop`` must not leave its listener behind; its wallet stays held for as
        # long as a transport presents with it (the table keeps it weakly)
        weakref.finalize(self, self._forget)

    def _build(self, params: dict[str, Any], scope: str, timeout: float, schemes: Mapping[str, type] | None) -> Caller:
        cls = lookup_scheme(self.scheme, schemes)
        if cls is None:
            raise AgentCommError(f"identity: unknown scheme {self.scheme!r}")
        problems = validate(params, cls.PARAMS_SCHEMA)
        if problems:
            raise AgentCommError(f"identity: scheme {self.scheme!r} params are invalid ({len(problems)} problems)")
        self._require_secrets(params)

        def make() -> Caller:
            try:
                wallet: IdentityScheme = cls.from_spec(params)
            except Exception as exc:  # a scheme that cannot be built: say so without echoing its params
                raise AgentCommError(
                    f"identity: scheme {self.scheme!r} cannot be built ({type(exc).__name__})"
                ) from None
            return Caller(self.identity, wallet, timeout=timeout)

        # one wallet per (bind, record, parameters), and per agent unless the record says ``process``: a revocation
        # reaches every layer that presents with it, and the gate sees one wallet, not several
        owner = self.agent_id if scope == "agent" else None
        key = hashlib.sha256(
            _canonical([self.holder_scope, owner, self.identity, self.scheme, params]).encode()
        ).hexdigest()
        with _SHARED_LOCK:
            held = _SHARED.get(key)
            if held is None:
                held = _SHARED[key] = make()
        return held

    def _require_secrets(self, params: Mapping[str, Any]) -> None:
        """Refuse to start when a secret the record names is absent: one ``identity.startup`` event, then the refusal.

        Presence is asked of the store without revealing anything; an optional reference may be absent.
        """
        from mas.runtime.secrets import current_resolver
        from mas.runtime.secrets.errors import SecretError
        from mas.runtime.secrets.ref import iter_secret_refs

        resolver = current_resolver()
        for ref in iter_secret_refs(params):
            if ref.optional:
                continue
            try:
                present = resolver.describe(ref).present
            except SecretError:
                present = False
            if not present:
                observe.emit(
                    "identity.startup",
                    actor=self.agent_id,
                    record=self.identity,
                    scheme=self.scheme,
                    outcome="denied",
                    code="refused:missing_secret",
                )
                raise AgentCommError("identity: refused:missing_secret")

    @property
    def caller(self) -> Caller:
        return self._caller

    def stop(self) -> None:
        """Stop hearing revocations and stop being listed as holding the wallet."""
        self._forget()
        self._unhold()
        for facts in self._facts_stores:
            facts.clear()
        super().stop()

    def invalidate_sessions(self) -> None:
        """Close every held session and open channel that used this layer's identity (called on revocation)."""
        for facts in self._facts_stores:
            facts.clear()
        for hook in list(self._hooks):
            hook.invalidate()

    @property
    def wraps_inbound(self) -> bool:
        """Only a scheme that can verify changes what the layers above receive."""
        return CAP_VERIFY in self._capabilities

    # --- call stacks (below the protocol layer) ---------------------------------------------------

    @classmethod
    def wrap(
        cls, inner: Any, params: dict[str, Any] | None = None, *, call_kinds: tuple[str, ...] | None = None
    ) -> Any:
        """Install the layer on the HTTP call transport ``inner`` and return it; the protocol layer above uses it."""
        if not isinstance(inner, AuthorizableTransport):
            raise AgentCommError("identity: the layer needs an http transport below it (not stdio, not sse)")
        layer = cls(**dict(params or {}))
        layer.attach(inner, call_kinds=call_kinds)
        return inner

    def attach(self, transport: AuthorizableTransport, *, call_kinds: tuple[str, ...] | None = None) -> None:
        kind = next(iter(call_kinds or ()), "tool")
        protocol = _PROTOCOL_BY_KIND.get(kind, "http")
        hook: _Hook | None = None
        facts = _Facts()
        self._facts_stores.append(facts)
        if protocol == "mcp":
            parts = urlsplit(transport.url)
            origin = self._audience["target"] if self._audience else f"{parts.scheme}://{parts.netloc}"
            hook = _Hook(
                self._caller,
                Audience(protocol=protocol, target=origin),
                task="",
                actor=self.agent_id,
                use=self._use,
                captured=None,
                handshakes=CAP_HANDSHAKE in self._capabilities,
                facts=facts,
                owner=self,
            )
        known = (lambda: facts.get(hook.scope()) or {}) if hook is not None else None
        auth = IdentityAuth(
            self._caller,
            protocol=protocol,
            audience=self._audience,
            actor=self.agent_id,
            use=self._use,
            meta_carried=protocol == "mcp",
            sections=_MCP_SECTIONS if protocol == "mcp" else {},
            known_peer=known,
            begun=hook.take_begun if hook is not None else None,
            refused=hook.refused if hook is not None else None,
            static_headers=getattr(transport, "static_header_names", lambda: ())(),
        )
        transport.use_auth(auth, guard=auth.guard_request)
        if hook is not None:
            self._hooks.add(hook)
            transport.add_session_hook(hook)

    # --- send stacks (above the protocol layer) ---------------------------------------------------

    def _audience_for(self, envelope: Envelope) -> Audience:
        if self._audience:
            return Audience(protocol=self._audience["protocol"], target=self._audience["target"])
        lower = self._require()
        target = str(envelope.meta.get("url") or lower.endpoint or envelope.meta.get("target") or "")
        return Audience(protocol="a2a", target=target)

    def _principal(self) -> Principal:
        principal = current_principal()
        if "actor" not in principal and self.agent_id:
            principal["actor"] = self.agent_id
        return principal

    async def asend(self, envelope: Envelope) -> Any:
        lower = self._require()
        # one message, one id: a verifier binds a challenge to the message and its context, so the resent message,
        # the answer to that challenge, must carry the same ones
        envelope = envelope.with_(
            meta={
                "message_id": str(envelope.meta.get("message_id") or uuid.uuid4().hex),
                "context_id": str(envelope.meta.get("context_id") or uuid.uuid4().hex),
            }
        )
        out = await self._prepared(lower, envelope, refused=None)
        try:
            return await lower.asend(out)
        except Unauthenticated as asked:
            refused = asked
        # the peer wants proof: answer its challenge and send the same message once more; no loop
        out = await self._prepared(lower, envelope, refused=refused)
        audience = self._audience_for(envelope)
        seen = {
            "actor": self._principal().get("actor", ""),
            "scheme": self.scheme,
            "record": self.identity,
            "protocol": audience["protocol"],
            "direction": "answered",
        }
        try:
            reply = await lower.asend(out)
        except Unauthenticated:
            observe.emit("identity.challenge", outcome="denied", code="refused:unauthorized", **seen)
            raise denial(IdentityError("refused", "unauthorized")) from None
        observe.emit("identity.challenge", outcome="ok", **seen)
        return reply

    async def _prepared(self, lower: Layer, envelope: Envelope, *, refused: Unauthenticated | None) -> Envelope:
        """``envelope`` carrying what the wallet presents; for ``refused`` (a peer's challenge), its answer to it."""
        try:
            audience = self._audience_for(envelope)
            # what the peer's published description declares (the card's extension URIs and skills), so a scheme can
            # decide whether to send its extension header at all
            known = dict(await lower.peer_facts(envelope))
            task = _task_name(envelope, known)
            principal = self._principal()
            text = envelope.payload if isinstance(envelope.payload, str) else None
            req = PresentRequest(principal=principal, audience=audience, task=task)
            if self._use:
                req["identity"] = self._use
            if text is not None:
                req["request"] = _message_view(audience, text, {})
            if refused is not None:
                # the one shape of a challenge phase: what the peer sent, as received (see ``PresentRequest.peer``)
                req["phase"] = "challenge"
                peer: dict[str, Any] = {"challenge": dict(refused.challenge)}
                if refused.wire is not None:
                    peer["wire"] = refused.wire
                req["peer"] = peer
            elif known:
                req["peer"] = known
            evidence = await self._caller.present(req, sections=_A2A_SECTIONS, text_protocols=_TEXT_PROTOCOLS)
            headers = dict(evidence.get("headers", {}))
            _extensions_header(headers, evidence)
            if "payload" in evidence:
                text = evidence["payload"]  # the message text, wrapped in the scheme's own envelope
            payload: Any = text if text is not None else envelope.payload
            if CAP_SIGN in self._capabilities:
                body = payload if isinstance(payload, (str, bytes)) else _canonical(payload)
                raw = body if isinstance(body, bytes) else body.encode()
                signed = PresentRequest(**req)
                signed["phase"] = "sign"
                signed["request"] = _message_view(audience, payload if isinstance(payload, str) else None, headers, raw)
                signature = await self._caller.present(signed, text_protocols=_TEXT_PROTOCOLS)
                headers.update(signature.get("headers", {}))
                if "payload" in signature:
                    payload = signature["payload"]
            out = self._carry(lower, envelope, evidence, headers, audience, principal, known)
            return out.with_(payload=payload) if payload is not envelope.payload else out
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise denial(exc) from None

    def _carry(
        self,
        lower: Layer,
        envelope: Envelope,
        evidence: Evidence,
        headers: dict[str, str],
        audience: Audience,
        principal: Principal,
        known: Mapping[str, Any],
    ) -> Envelope:
        """The envelope with the evidence put where the layer below carries it."""
        metadata = {**dict(envelope.meta.get("metadata") or {}), **dict(evidence.get("meta", {}))}
        if headers and not lower.wire_headers_below:
            metadata[HEADERS_KEY] = headers
            headers = {}
        hook = _Hook(
            self._caller,
            audience,
            task=_task_name(envelope, known),
            actor=self.agent_id,
            use=self._use,
            captured=CapturedPrincipal(principal),
            handshakes=CAP_HANDSHAKE in self._capabilities,
            required=True,
            known=known,
            owner=self,
        )
        self._hooks.add(hook)
        extra: dict[str, Any] = {SESSION_HOOK_META: hook}
        if metadata:
            extra["metadata"] = metadata
        return envelope.with_(headers=headers, meta=extra)

    # --- inbound ------------------------------------------------------------------------------

    def serve(self, handler: Handler) -> Handler:
        """Verify the caller before ``handler`` runs; it never runs when verification fails."""
        if CAP_VERIFY not in self._capabilities:
            return handler

        async def verified(envelope: Envelope) -> Any:
            principal, text, owner, credential_keys = await self._verify(envelope)
            if text is not None and text != envelope.payload:
                replace = envelope.meta.get("replace_text")
                if callable(replace):  # a transport that keeps the message keeps what the agent got, not the wrapper
                    replace(text)
                envelope = envelope.with_(payload=text)  # the text with the identity layer removed
            # the verified principal (ids only) for what runs above, so a server can bind its own state to it. The
            # conversation the caller names becomes one that belongs to its verified owner: what runs above sees that id
            # and never the caller's own, so two owners that use one id are two conversations
            meta: dict[str, Any] = {"owner": owner}
            if credential_keys:
                meta["credential_keys"] = credential_keys  # the carriers a transport must not keep (names, no values)
                forget = envelope.meta.get("forget_metadata")
                if callable(forget):  # a transport that keeps the message offers to drop them from it
                    forget(credential_keys)
            client = str(envelope.meta.get("context_id") or "")
            if client:
                principal["session"] = meta["context_id"] = conversation_id(owner, client)
            envelope = envelope.with_(meta={**meta, "principal": dict(principal)})
            with bind(principal):
                return await handler(envelope)

        return verified

    async def _verify(self, envelope: Envelope) -> tuple[Principal, str | None, str, list[str]]:
        protocol = str(envelope.meta.get("protocol") or "a2a")
        target = self._audience["target"] if self._audience else str(self._lower_endpoint())
        audience = (
            Audience(protocol=self._audience["protocol"], target=self._audience["target"])
            if self._audience
            else Audience(protocol=protocol, target=target)
        )
        session = str(envelope.meta.get("context_id") or "")
        inbound = Inbound(direction="request", protocol=protocol, headers=dict(envelope.headers), audience=audience)
        if session:
            inbound["session"] = session[:MAX_ID]
        request_id = envelope.meta.get("message_id")
        if isinstance(request_id, str) and request_id:
            inbound["request_id"] = request_id[:MAX_ID]
        task_id = envelope.meta.get("task_id")
        if isinstance(task_id, str) and task_id:
            inbound["task_id"] = task_id[:MAX_ID]
        if isinstance(envelope.payload, str) and protocol in _TEXT_PROTOCOLS:
            inbound["payload"] = envelope.payload
        if envelope.meta.get("metadata"):
            inbound["meta"] = dict(envelope.meta["metadata"])
        try:
            verified = await self._caller.verify(inbound, carry_keys=_CARRY_KEYS)
        except ChallengeRequired as asked:
            observe.emit(
                "identity.challenge",
                direction="issued",
                outcome="ok",
                actor=self.agent_id,
                scheme=self.scheme,
                record=self.identity,
                protocol=protocol,
            )
            # what travels to the caller is the reserved challenge keys and the scheme's namespaced ones
            sent = {key: value for key, value in asked.challenge.items() if key in CHALLENGE_KEYS or "/" in str(key)}
            raise PeerChallenge(sent, code=str(asked), carry=asked.carry or None, wire=asked.wire) from None
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise PeerChallenge({}, code=str(exc) if isinstance(exc, IdentityError) else "unavailable") from None
        principal = Principal(subject=verified["subject"])
        if self.agent_id:
            principal["actor"] = self.agent_id
        if session:
            principal["session"] = session
        if verified.get("actors"):
            principal["chain"] = list(verified["actors"])  # who the call came through, nearest actor first
        return (
            principal,
            verified.get("payload"),
            owner_key(self.identity, verified),
            list(verified.get("credential_keys") or []),
        )

    def _lower_endpoint(self) -> str:
        return self._lower.endpoint if self._lower is not None else ""


def owner_key(record_id: str, verified: Mapping[str, Any]) -> str:
    """Who owns what a verified caller makes: a digest of the record, the issuer the scheme reports and the subject.

    A subject is only unique within whoever issued it: ``alice`` at one identity provider is not ``alice`` at another. A
    scheme whose subject comes from a token must therefore report ``claims.iss``; it is part of the key whenever it is
    there (a non-string one is a failed verification, not a guess). The subject alone is never the key.

    It is the owner part of every conversation id a server below this layer sees (``stack.owner.conversation_id``): the
    quality of the key is the quality of the isolation between callers, no better and no worse. It is a pseudonym, not
    anonymity: a digest without a salt, so a guessable subject can be confirmed against it.
    """
    claims = verified.get("claims") or {}
    issuer = claims.get("iss", "") if isinstance(claims, Mapping) else ""
    if not isinstance(issuer, str):
        raise PeerChallenge({}, code="refused:bad_issuer")
    return "own:" + hashlib.sha256(_canonical([record_id, issuer, verified["subject"]]).encode()).hexdigest()[:40]


def _task_name(envelope: Envelope, known: Mapping[str, Any]) -> str:
    """The operation a send is for: the envelope's own, else the skill id of a peer that declares exactly one, else the
    intent (``task``). A peer with several skills leaves the choice to the caller, so the intent stays."""
    named = str(envelope.meta.get("task") or "")
    if named:
        return named
    skills = known.get("skills") or []
    if len(skills) == 1:
        return str(skills[0])
    return str(envelope.meta.get("intent") or "")


def _message_view(
    audience: Audience, text: str | None, headers: Mapping[str, str], raw: bytes | None = None
) -> RequestView:
    """The request a scheme sees on an A2A send: the outgoing message, its digest and its text."""
    body = raw if raw is not None else (text or "").encode()
    view = RequestView(method="SendMessage", url=audience["target"], headers=dict(headers), request_id=uuid.uuid4().hex)
    view["body_sha256"] = hashlib.sha256(body).hexdigest()
    if text is not None:
        view["body_text"] = text
    return view


def _extensions_header(headers: dict[str, str], evidence: Evidence) -> None:
    """Declare the extension URIs the scheme advertised in the ``A2A-Extensions`` header."""
    uris = evidence.get("advertise", {}).get("a2a.extensions", [])
    if uris:
        headers[EXTENSIONS_HEADER] = ", ".join(str(u) for u in uris)
