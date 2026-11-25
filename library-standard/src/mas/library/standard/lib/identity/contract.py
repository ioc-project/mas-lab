#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The identity contract (:data:`CONTRACT_VERSION`, provisional): types and Protocols only, no logic.

A record builds a **wallet** (:class:`IdentityScheme`): a holder of identities. It lists them (``identities``), says
which one it would show to an audience for a task (``select``) and shows it (``present``). A wallet of one identity is
the common case. The optional interfaces add what a scheme can also do: ``Verifier`` (check an inbound credential),
``Describer`` (a value-free public view), ``Revoker`` (stop using an identity), ``Handshaker`` (an exchange inside an
open session) and ``Delegator`` (derive a narrower identity from one). A scheme lists the capability names it adds in
``capabilities``; ``sign`` is a phase of ``present``. How a scheme obtains, delegates or exposes an identity on a
protocol is the scheme's own business.

Values are plain dicts. ``Evidence`` is the one place credential material lives; it goes to the wire and nowhere else.
A secret parameter reaches ``from_spec`` as a ``SecretRef`` and is revealed only inside the call that uses it. The
reference page is ``docs/references/identity.md``.
"""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Mapping, Sequence
from typing import Any, ClassVar, Literal, NotRequired, Protocol, TypedDict

#: The version of this contract. ``/0`` is provisional: it may change before the first release.
CONTRACT_VERSION = "identity-contract/0 (provisional)"

# --- capabilities -------------------------------------------------------------------------------

CAP_SIGN = "sign"
CAP_VERIFY = "verify"
CAP_DESCRIBE = "describe"
CAP_REVOKE = "revoke"
CAP_DELEGATE = "delegate"
CAP_HANDSHAKE = "handshake"

#: Every capability name a scheme may declare. ``identities``, ``select`` and ``present`` are implicit.
CAPABILITIES: frozenset[str] = frozenset({CAP_SIGN, CAP_VERIFY, CAP_DESCRIBE, CAP_REVOKE, CAP_DELEGATE, CAP_HANDSHAKE})

# --- limits and closed vocabularies -------------------------------------------------------------

#: The closed set of ``IdentityError.code`` values.
ERROR_CODES: frozenset[str] = frozenset(
    {
        "no_credential",
        "revoked",
        "expired",
        "unsatisfiable",
        "refused",
        "unavailable",
        "invalid",
        "challenge_required",
    }
)

#: Most characters of a message payload (``Inbound.payload``, ``Verified.payload``, ``body_text``, ...).
MAX_PAYLOAD = 65536

#: Most characters of a serialized challenge a peer expects (``ChallengeRequired.wire``, ``peer["wire"]``).
MAX_WIRE = 8192

#: ``Verified.source`` values.
VERIFIED_SOURCES: frozenset[str] = frozenset({"token", "certificate", "badge", "signature", "other"})

#: ``IdentityInfo.status`` values.
IDENTITY_STATUSES: frozenset[str] = frozenset({"active", "expired", "revoked"})

#: The four axes of a :class:`Grant`. The set is closed: any other axis is refused.
GRANT_AXES: frozenset[str] = frozenset({"scopes", "audience", "valid_until", "depth"})

#: A delegation chain is refused beyond this depth (``Grant.depth``).
MAX_DELEGATION_DEPTH = 4

#: Most entries in a delegation chain (``Verified.actors``, ``Principal.chain``), in ``credential_keys`` and in
#: ``IdentityInfo.labels``.
MAX_CHAIN = MAX_DELEGATION_DEPTH + 4

#: Most scopes in a grant, and most characters of one scope.
MAX_GRANT_SCOPES = 64
MAX_SCOPE = 256

# --- errors -------------------------------------------------------------------------------------

_SLUG_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_.:-")


def _is_slug(value: str) -> bool:
    return 0 < len(value) <= 64 and all(ch in _SLUG_CHARS for ch in value)


class IdentityError(Exception):
    """A typed, value-free failure.

    ``code`` is one of :data:`ERROR_CODES`:

    * ``no_credential``: nothing to present for this audience and task.
    * ``revoked``: the identity was revoked.
    * ``expired``: the credential is past (or within the skew of) its ``valid_until``.
    * ``unsatisfiable``: the scheme cannot do what was asked (capability missing, parent evidence missing).
    * ``refused``: the peer or the scheme refused (wrong audience, replayed challenge, a delegation that widens).
    * ``unavailable``: the scheme failed for a reason it does not expose (network, bug).
    * ``invalid``: evidence or an input broke the contract.
    * ``challenge_required``: see :class:`ChallengeRequired` (raised by ``verify``).

    ``reason`` is an optional short ASCII slug (``[a-z0-9_.:-]``, at most 64 characters) such as
    ``scope_uncovered:read`` for a policy to show. It is never a value. ``str(error)`` is the code, plus
    ``:reason`` when there is one, and nothing else: no message, no cause.
    """

    code: str
    reason: str

    def __init__(self, code: str, reason: str = "") -> None:
        if code not in ERROR_CODES:
            raise ValueError("unknown identity error code")
        if reason and not _is_slug(reason):
            raise ValueError("reason must be a short ascii slug")
        self.code = code
        self.reason = reason
        super().__init__(f"{code}:{reason}" if reason else code)

    def __reduce__(self) -> tuple[Any, ...]:
        return (type(self), (self.code, self.reason))


class InteractionRequired(IdentityError):
    """A human or out-of-band step is needed before a credential exists (device code, OTP approval).

    ``handle`` is an opaque id the caller uses to find the pending step again. ``hint`` is text for a
    human (where to go, which code to type). Neither is part of ``str(error)``. The code is always
    ``no_credential``. Routes to a human-in-the-loop responder.
    """

    handle: str
    hint: str

    def __init__(self, handle: str = "", hint: str = "") -> None:
        super().__init__("no_credential")
        self.handle = handle
        self.hint = hint

    def __reduce__(self) -> tuple[Any, ...]:
        return (type(self), (self.handle, self.hint))


class ChallengeRequired(IdentityError):
    """Raised by ``Verifier.verify`` when the caller showed no (usable) credential: "here is what I need".

    ``challenge`` is meant for the peer, so it holds no secret (see :class:`Challenge`). ``carry`` optionally names
    how a protocol carries it (the keys a protocol adapter declares); ``wire`` is the exact serialized challenge a
    peer with its own shape expects, at most :data:`MAX_WIRE` characters. None of them is echoed by ``str(error)``,
    which is the code only.
    """

    challenge: Mapping[str, Any]
    carry: Mapping[str, str]
    wire: str | None

    def __init__(
        self,
        challenge: Mapping[str, Any],
        carry: Mapping[str, str] | None = None,
        wire: str | None = None,
    ) -> None:
        super().__init__("challenge_required")
        self.challenge = dict(challenge)
        self.carry = dict(carry or {})
        self.wire = wire

    def __reduce__(self) -> tuple[Any, ...]:
        return (type(self), (self.challenge, self.carry, self.wire))


class Challenge(TypedDict, total=False):
    """What ``ChallengeRequired.challenge`` carries. Meant for the peer, so never a secret.

    * ``nonce``: single use; never issued twice.
    * ``audience``: the audience the challenge is bound to; an answer for another audience is refused.
    * ``requirements``: what the answer must show (claim names, scopes).
    * ``realm``: a protection space name (HTTP ``WWW-Authenticate`` realm).
    * ``schemes``: the scheme names the verifier accepts.
    * ``expires_at``: epoch seconds after which the challenge is void.

    Extra keys are allowed when namespaced with ``/`` (``myscheme/hint``).
    """

    nonce: str
    audience: str
    requirements: list[str]
    realm: str
    schemes: list[str]
    expires_at: float


#: The challenge keys the contract reserves. All are optional; any other key must be namespaced with ``/``.
CHALLENGE_KEYS: frozenset[str] = frozenset(Challenge.__annotations__)

# --- carrier types ------------------------------------------------------------------------------


class Principal(TypedDict, total=False):
    """Who is acting in this session. Ids and opaque references only, never a secret.

    * ``actor``: the agent (or component) making the call.
    * ``subject``: on whose behalf, as an opaque reference. ``""`` or absent means nobody in particular.
    * ``session``: the session id.
    * ``chain``: the delegation chain the call came through, nearest actor first (ids only, at most
      :data:`MAX_CHAIN`). Absent means unknown, never "direct".
    """

    actor: str
    subject: str
    session: str
    chain: list[str]


class Audience(TypedDict):
    """What the evidence is for.

    * ``protocol``: the protocol name (``mcp``, ``a2a``, ``llm``, ``slim``, ``http``; others are allowed).
    * ``target``: the origin for HTTP protocols, a logical name otherwise.
    """

    protocol: str
    target: str


class RequestView(TypedDict, total=False):
    """The request a scheme binds a signature to on phase ``sign``. An HTTP body is never given, only its digest.

    * ``method``: HTTP method or RPC method.
    * ``url``: the request URL.
    * ``headers``: the request headers a signature may cover.
    * ``body_sha256``: lowercase hex SHA-256 of the body, ``""`` when there is none.
    * ``body_text``: the outgoing message text, only for message protocols (A2A). ``Evidence.payload`` replaces it.
    * ``request_id``: an id the host makes unique for this request, so a scheme that needs a unique value per request
      (a DPoP ``jti``, a signature nonce) derives it from the id and the clock, never from randomness.
    """

    method: str
    url: str
    headers: Mapping[str, str]
    body_sha256: str
    body_text: str
    request_id: str


class Grant(TypedDict, total=False):
    """A value-free summary of what a credential authorises: never a token, never a key.

    The vocabulary is closed (four axes). Each axis is optional, and an absent axis means *unknown*, not
    *everything*.

    * ``scopes``: at most :data:`MAX_GRANT_SCOPES` scope names, each at most :data:`MAX_SCOPE` characters, with no
      whitespace or control character.
    * ``audience``: the audience target the credential is bound to.
    * ``valid_until``: epoch seconds after which the authority is void.
    * ``depth``: how many delegations the authority went through (0 = original, at most
      :data:`MAX_DELEGATION_DEPTH`).

    A delegation may only narrow: see ``check_narrowing`` in the rules.
    """

    scopes: list[str]
    audience: str
    valid_until: float
    depth: int


class IdentityInfo(TypedDict):
    """One identity of a wallet, as it may be listed: value-free.

    * ``id``: the identity's id inside its wallet (a slug).
    * ``kind``: what sort of identity it is (``agent``, ``workload``, ``delegated``, ...), a slug the scheme picks.
    * ``subject``: the opaque reference of who it stands for, ``""`` when nobody in particular.
    * ``labels``: at most :data:`MAX_CHAIN` slugs a policy may select on.
    * ``status``: one of :data:`IDENTITY_STATUSES`.
    * ``grant``: what it authorises, when known.
    * ``parent``: the id of the identity it was derived from, absent for an original one.
    """

    id: str
    kind: str
    subject: str
    labels: list[str]
    status: str
    grant: NotRequired[Grant]
    parent: NotRequired[str]


class Delegation(TypedDict, total=False):
    """What ``Delegator.delegate`` is asked: derive a narrower identity from ``identity``.

    * ``identity``: the identity to derive from (absent: the wallet's selected one).
    * ``grant``: what the new identity may do. It may only narrow its parent's authority.
    * ``principal``: who the new identity acts for.
    * ``audience``: the audience it will be used for, when known.
    * ``parent_evidence``: what a parent that is not in this wallet presented for the same audience (the verified
      caller of a session, another record): credential material to exchange or attenuate, never to echo. A scheme
      that needs it and gets none refuses with ``unsatisfiable:needs_parent_evidence``; it never presents its own
      credential in the parent's place (a confused deputy).
    * ``now``: the host clock: a scheme uses it, never the wall clock.
    """

    identity: str
    grant: Grant
    principal: Principal
    audience: Audience
    parent_evidence: Evidence
    now: float


class PresentRequest(TypedDict, total=False):
    """What a wallet is asked to present.

    ``record`` is the id of the record the wallet was built from; ``identity`` the identity to show (absent: the wallet
    selects); ``principal`` who is acting; ``audience`` what the evidence is for; ``task`` the operation (``""`` for
    none); ``phase`` is ``"initiate"`` (default), ``"challenge"`` (answering a peer challenge; ``peer`` is then
    ``{"challenge": ..., "wire": ..., "transport": ...}``, and a scheme reads ``challenge`` and ``wire``) or ``"sign"``
    (bind ``request``; never cached, last on the call). On ``initiate`` ``peer`` may carry facts known before the call
    (extension URIs, skill ids, ``identifier``). ``now`` is the host clock: a scheme uses it, never the wall clock.
    """

    record: str
    identity: str
    principal: Principal
    audience: Audience
    task: str
    phase: str
    peer: Mapping[str, Any]
    request: RequestView
    now: float


class Evidence(TypedDict, total=False):
    """What a wallet returns. This is the only place credential material lives.

    ``headers`` pass the header caps and the deny-list; every ``meta`` key contains ``/``; ``valid_until`` (epoch
    seconds) absent means not cached; ``reusable`` defaults to ``False``; ``scope`` is ``"task"`` (default) or
    ``"audience"`` (shared across tasks); ``advertise`` declares capabilities on phase ``initiate`` only, under the
    sections the protocol adapter supports; ``payload`` replaces the outgoing message text on a message protocol,
    never ``reusable``; ``grant`` says what the evidence authorises, so a delegation can be checked without reading a
    token.
    """

    headers: Mapping[str, str]
    meta: Mapping[str, Any]
    valid_until: float
    reusable: bool
    scope: Literal["task", "audience"]
    advertise: Mapping[str, Mapping[str, Any] | Sequence[str]]
    payload: str
    grant: Grant


class Inbound(TypedDict, total=False):
    """What a server (or a client checking the peer it called) saw. The host fills every field, never the caller.

    ``headers`` and ``meta`` are what arrived: claims to verify, not facts. ``session``, ``request_id``, ``task_id``
    (ids of at most 256 characters; a retry after a challenge is a new message on the same task, so bind a nonce to
    the task) and ``now`` come from the transport and the host. ``payload`` is the received message
    text, at most :data:`MAX_PAYLOAD` characters.
    """

    direction: Literal["request", "response"]
    protocol: str
    headers: Mapping[str, str]
    meta: Mapping[str, Any]
    session: str
    request_id: str
    task_id: str
    payload: str
    now: float
    audience: Audience


class Verified(TypedDict):
    """A verification that passed every check.

    * ``subject``: the verified subject, an opaque reference.
    * ``claims``: what the verification established.
    * ``source``: one of :data:`VERIFIED_SOURCES`.
    * ``payload`` (optional): the received text with the identity wrapper removed. When set, the host hands THAT to the
      handler, so identity material never reaches the model as prompt text. It must not contain the verified
      credential.
    * ``actors`` (optional): the delegation chain of the credential, nearest actor first (an RFC 8693 ``act`` chain, a
      UCAN or biscuit proof chain). Opaque ids, at most :data:`MAX_CHAIN`. Absent means unknown, never "direct".
    * ``grant`` (optional): what the caller's credential authorises.
    * ``credential_keys`` (optional): the NAMES of the headers and ``meta`` keys that carried the credential, so a host
      can build ``Delegation.parent_evidence`` from exactly those. Never values. Absent or empty means the credential
      cannot be re-delegated.
    """

    subject: str
    claims: Mapping[str, Any]
    source: str
    payload: NotRequired[str | None]
    actors: NotRequired[list[str]]
    grant: NotRequired[Grant]
    credential_keys: NotRequired[list[str]]


class PublicView(TypedDict, total=False):
    """What a policy may know about an identity without a proof.

    * ``facts``: grade, limits, scopes, expiry.
    * ``attestation``: a verifiable statement about the identity (badge, credential summary).
    * ``advertise``: what to put on an agent card.
    """

    facts: Mapping[str, Any]
    attestation: Mapping[str, Any]
    advertise: Mapping[str, Any]


class Channel(Protocol):
    """An in-session request path the adapter supplies to a handshake.

    ``request`` sends ``method`` with ``params`` to the peer inside the session already open
    (an MCP session, an A2A task) and returns the result mapping.

    ``data_key`` lets a scheme match a peer's own wire shape. On a JSON-RPC style channel (MCP) it is
    ignored. On a message channel (A2A) the follow-up data part is ``{"method": method, "params": params}``
    by default and ``{data_key: params}`` when ``data_key`` is set.
    """

    async def request(
        self, method: str, params: Mapping[str, Any], *, data_key: str | None = None
    ) -> Mapping[str, Any]: ...


class HandshakeContext(TypedDict):
    """What a handshaking wallet gets.

    * ``record``: the id of the record the wallet was built from.
    * ``principal``: who is acting.
    * ``audience``: the peer the session is with.
    * ``task``: the operation, ``""`` for none.
    * ``peer``: what the peer declared (the ``initialize`` result, or the challenge peer shape of an A2A task).
    * ``channel``: the in-session request path.
    * ``identity`` (optional): the wallet identity the session was opened with; absent, the wallet selects.
    * ``now``: the host clock (set by the caller, not by the host that builds the context).
    A handshake returns ``meta`` for the session; ``headers`` are refused (a session has no header to carry them).
    """

    record: str
    principal: Principal
    audience: Audience
    task: str
    peer: Mapping[str, Any]
    channel: Channel
    identity: NotRequired[str]
    now: NotRequired[float]


# --- the interfaces ------------------------------------------------------------------------------


class IdentityScheme(Protocol):
    """The required contract: a wallet. Subclass it, set the class attributes, implement the three methods.

    * ``scheme``: the registry name.
    * ``capabilities``: a subset of :data:`CAPABILITIES`. Each optional capability is declared here **and** by
      subclassing its interface; the conformance kit fails when the two disagree.
    * ``PARAMS_SCHEMA``: a JSON schema for the record ``params``. A property marked ``"x-secret": true`` holds a
      secret: it arrives in ``from_spec`` as a ``SecretRef``, never as text.
    * ``from_spec``: builds the wallet from the record's params. It never reads the environment, a file or a secret
      store, and it keeps no revealed secret: a scheme reveals a ``SecretRef`` only inside the call that uses it, into
      a local variable (``current_resolver().reveal(ref)``).
    * ``identities``: the identities of the wallet, value-free. A wallet of one lists one.
    * ``select``: the id of the identity it would show for this request, or ``None`` when it has none for it. It does
      not present and it reaches no network.
    * ``present``: what to show this audience for this task, as the identity named by ``req["identity"]`` or, when
      absent, the selected one. The same instance answers the same request (same ``now``) the same way: state (a
      cache, a refresh token) may speed it up and a real token endpoint may hand out a new token, but under a fake
      clock and a fake endpoint the answer does not vary, so scripted runs replay. The clock is ``req["now"]``, never
      the wall clock.
    """

    scheme: ClassVar[str]
    capabilities: ClassVar[frozenset[str]]
    PARAMS_SCHEMA: ClassVar[Mapping[str, Any]]

    @classmethod
    @abstractmethod
    def from_spec(cls, params: Mapping[str, Any]) -> IdentityScheme: ...

    @abstractmethod
    def identities(self) -> Sequence[IdentityInfo]: ...

    @abstractmethod
    def select(self, req: PresentRequest) -> str | None: ...

    @abstractmethod
    async def present(self, req: PresentRequest) -> Evidence: ...


class Verifier(Protocol):
    """Capability ``verify``: who is calling, and is it valid?

    Returns :class:`Verified` only when every check passed. Raises :class:`IdentityError` to deny, and
    :class:`ChallengeRequired` when the caller showed nothing usable and the peer should be told what to send.

    An inbound that shows no credential is refused or answered with a challenge, never verified. A scheme that
    verifies everyone on purpose (the ``none`` scheme verifies anonymous callers) sets ``ANONYMOUS_OK = True``: the
    conformance kit then accepts it (it still asks for a valid inbound), and a policy reads the choice from the scheme.

    A verifier that unwraps message text (returns :attr:`Verified.payload`, the received text with the identity
    wrapper removed) sets ``RETURNS_PAYLOAD = True`` and then MUST supply ``message_inbound`` to the conformance kit,
    which checks the unwrapped text. A verifier that leaves it ``False`` must never return a payload.

    A verifier that tells a caller what to present without issuing anything (an RFC 6750 ``Bearer`` challenge with a
    realm and scope, an RFC 9421 ``Accept-Signature`` hint naming the accepted algorithms) sets
    ``ADVISORY_CHALLENGE = True`` and may raise :class:`ChallengeRequired` with a static hint and no ``nonce``. Such a
    challenge is advice: it is not answered through a handshake and it claims no single use. A verifier that leaves
    it ``False`` raises only nonce-bound challenges: single use, bound to the audience, answered once.
    """

    ANONYMOUS_OK: ClassVar[bool] = False
    RETURNS_PAYLOAD: ClassVar[bool] = False
    ADVISORY_CHALLENGE: ClassVar[bool] = False

    @abstractmethod
    async def verify(self, inbound: Inbound) -> Verified: ...


class Describer(Protocol):
    """Capability ``describe``: what a policy may know about the identity ``ref`` without a proof.

    The view's ``facts`` are opaque to the contract: a policy engine reads them with a vocabulary of its own.
    """

    @abstractmethod
    async def describe(self, ref: str) -> PublicView: ...


class Revoker(Protocol):
    """Capability ``revoke``: stop using the identity ``ref`` now. Everything derived from it stops too."""

    @abstractmethod
    async def revoke(self, ref: str) -> None: ...


class Handshaker(Protocol):
    """Capability ``handshake``: an interactive exchange inside the session (challenge, same-task reply, step-up).

    Returns an ``Evidence`` whose ``meta`` rides the later calls of the session.
    """

    @abstractmethod
    async def handshake(self, ctx: HandshakeContext) -> Evidence: ...


class Delegator(Protocol):
    """Capability ``delegate``: derive a narrower identity inside the wallet.

    Returns the new identity (``IdentityInfo.parent`` names its parent, and its ``grant`` is inside the parent's).
    It is then shown with ``present`` like any other and revoked with its parent. A delegation only narrows: one that
    widens is refused (``refused:delegation_widens``).
    """

    @abstractmethod
    async def delegate(self, delegation: Delegation) -> IdentityInfo: ...


#: capability name -> the interface a scheme must subclass to declare it.
#: ``sign`` is a phase of ``present``, so it has no interface of its own.
CAPABILITY_INTERFACES: Mapping[str, type] = {
    CAP_VERIFY: Verifier,
    CAP_DESCRIBE: Describer,
    CAP_REVOKE: Revoker,
    CAP_HANDSHAKE: Handshaker,
    CAP_DELEGATE: Delegator,
}
