#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The scheme conformance kit.

Subclass :class:`SchemeConformance` in a test module, set ``make_scheme``, and pytest runs every check:

    class TestMyScheme(SchemeConformance):
        def make_scheme(self):
            return MyScheme.from_spec({"token": SecretRef(name="token")})

        def secrets(self):
            return {"token": self.canary}

        def good_params(self):
            return {"token": SecretRef(name="token")}

The kit binds ``secrets()`` as the process secret store around every check, so a scheme built from ``SecretRef`` params
reveals ``self.canary`` when it uses its secret, and the checks look for it in every error, repr and public view.

Hooks a scheme author supplies, and the kit REQUIRES them (a missing hook is a failed check, never a skipped one):

* always: ``make_scheme`` and ``good_params``; ``secrets`` when the scheme has a secret parameter;
* a scheme that declares ``verify``: ``valid_inbound(scheme, audience=None)``, an inbound that verifies, built from the
  scheme's params (for ``audience``, the same inbound made for another audience). When a ``verify`` with no credential
  raises ``ChallengeRequired`` the kit also requires ``challenge_inbound``, ``answer_challenge`` and
  ``oversized_answer``, unless the scheme sets ``ADVISORY_CHALLENGE = True`` (a static hint with no nonce: no hooks);
* ``sign``: ``make_sign_request``; ``handshake``: ``make_handshake_context``;
* ``delegate``: ``delegation`` (a valid request to derive an identity), ``widening_grant`` and, with ``verify``,
  ``evidence_inbound``; ``needs_parent_evidence`` says whether the scheme derives from a parent outside its wallet.

Optional: ``requests`` (the requests every check runs; add a challenge-phase one), ``message_inbound`` (an inbound
message wrapped with an identity wrapper; REQUIRED when the scheme sets ``RETURNS_PAYLOAD = True``), ``parent_grants``,
``parent_evidence`` and ``other_parent_evidence`` (defaults exist), and ``sections``, ``text_protocols`` and
``carry_keys``, the vocabulary of the protocol adapter the checks assume (the defaults are the MCP and A2A ones). A
``verify`` refuses an inbound with no credential, or answers with a challenge, unless the scheme sets
``ANONYMOUS_OK = True``.

The checks, each one an assumption of the formal model of the contract:

* the wallet lists value-free identities with unique ids, ``select`` names a listed one (or none) and does not
  present, and ``present`` shows the identity it is asked for, or the selected one;
* ``present`` is deterministic: two fresh instances answer alike and so does one instance asked twice, the request is
  not mutated, the clock is never read (``valid_until`` is derived from ``request["now"]``), and ``verify`` is
  deterministic too. The wall clock is refused by a guard on ``time.time`` while the checks run;
* what a scheme returns is valid (headers, meta, ``advertise``, ``payload``, ``grant`` through the rules);
* a verifier accepts its valid inbound (a positive control), refuses it with the credential removed and made for
  another audience, and echoes no credential;
* evidence is bound to the challenge and the audience: a challenge is issued once, a replay or a wrong audience gets
  one uniform refusal, an oversized answer is refused, a grant never names another audience;
* there are two kinds of challenge. A nonce-bound one (``ADVISORY_CHALLENGE`` False, the default) has a nonce, is
  single use and audience-bound. An advisory one (``ADVISORY_CHALLENGE = True``) must carry NO nonce, is not answered
  through a handshake, and must be valid, value-free (no echo of a credential or of an inbound header value), name only
  its own audience, be the same for the same request and fit the size cap;
* a delegating scheme delegates into its own wallet, refuses without a parent it needs (never its own credential),
  derives from the parent (two different parents give two different evidences), only narrows, and a revoked parent
  takes what it derived with it;
* no secret in an error, repr, public view, verified result, challenge, wire or grant, and none kept on the object;
* capability honesty, both ways; ``from_spec`` rejects bad params; a revoked identity refuses everything.
"""

from __future__ import annotations

import asyncio
import copy
import inspect
import sys
import time
from collections.abc import Callable, Coroutine, Iterator, Mapping
from contextlib import contextmanager
from typing import Any, TypeVar, cast

import pytest
from mas.library.standard.lib.identity.contract import (
    CAP_DELEGATE,
    CAP_DESCRIBE,
    CAP_HANDSHAKE,
    CAP_REVOKE,
    CAP_SIGN,
    CAP_VERIFY,
    CAPABILITIES,
    CAPABILITY_INTERFACES,
    MAX_WIRE,
    ChallengeRequired,
    Delegation,
    Delegator,
    Describer,
    Evidence,
    Grant,
    HandshakeContext,
    Handshaker,
    IdentityError,
    IdentityScheme,
    Inbound,
    PresentRequest,
    Revoker,
    Verifier,
)
from mas.library.standard.lib.identity.rules import (
    check_carry,
    check_challenge,
    check_evidence,
    check_identity_info,
    check_narrowing,
    check_params_schema,
    check_public_view,
    check_verified,
    check_wire,
    validate,
)
from mas.library.standard.lib.identity.verifier_only import VerifierOnly
from mas.library.standard.testing.identity_fakes import CANARY, FAKE_NOW, bound_secrets, make_request

_T = TypeVar("_T")

#: The target of the parent's grant in the delegation checks (the child re-targets to the request's audience).
PARENT_AUDIENCE = "https://parent.example"

#: The parent's credential in the delegation checks, distinct from the scheme's own ``canary``.
PARENT_CANARY = "parent-8c2e91b4-not-a-real-secret"

#: A second, different parent for the delegation checks: evidence derived from the parent must tell the two apart.
OTHER_PARENT_CANARY = "parent-3f7a52d9-another-not-a-real-secret"

#: Another audience: evidence or an inbound made for it must be refused by a verifier of the real audience.
ELSEWHERE = "https://elsewhere.example"

#: An inbound header value an advisory challenge must never echo.
ECHO_MARK = "echo-mark-4d1e7c93-not-a-credential"

#: The parts of an inbound that carry a credential: removing them must make a verifier refuse.
_NO_NONCE = (
    "a challenge needs a nonce (single use, bound to the audience); a static hint with no nonce (a Bearer realm and "
    "scope) is an advisory challenge: set ADVISORY_CHALLENGE = True on the verifier"
)

_CREDENTIAL_KEYS = ("headers", "meta", "payload")


def run(coro: Coroutine[Any, Any, _T]) -> _T:
    """Run a coroutine to completion on a fresh event loop."""
    return asyncio.run(coro)


@contextmanager
def forbid_wall_clock(*modules: str) -> Iterator[None]:
    """For the block, ``time.time`` and ``time.time_ns`` raise when called from any of ``modules`` (or a submodule).

    Other callers (the event loop, logging, pytest) are unaffected, so a scheme that logs is not flagged. A clock read
    through ``datetime`` is not seen by this guard; the determinism checks (two fresh instances answer alike under a
    fake ``now``) catch what it lets through.
    """
    real_time, real_ns = time.time, time.time_ns

    def guard(real: Callable[[], Any]) -> Callable[[], Any]:
        def inner() -> Any:
            caller = sys._getframe(1).f_globals.get("__name__", "")
            if any(caller == module or caller.startswith(module + ".") for module in modules):
                raise AssertionError("the scheme read the wall clock; use request['now']")
            return real()

        return inner

    time.time = guard(real_time)
    time.time_ns = guard(real_ns)
    try:
        yield
    finally:
        time.time = real_time
        time.time_ns = real_ns


def _scheme_modules(scheme: object) -> tuple[str, ...]:
    """The modules of every class in the scheme's MRO, so a method inherited from another module is guarded too."""
    return tuple({cls.__module__ for cls in type(scheme).__mro__ if cls is not object})


def _kept_state(scheme: object) -> list[object]:
    """Everything the scheme could keep a revealed value in: its instance dict and slots, the dict of each class in its
    MRO (class attributes, not methods)."""
    held: list[object] = []
    cls = type(scheme)
    for klass in cls.__mro__:
        if klass is object:
            continue
        for name in getattr(klass, "__slots__", ()) or ():
            try:
                held.append(getattr(scheme, name))
            except (AttributeError, TypeError):
                continue
        held.extend(v for v in vars(klass).values() if not callable(v) and not isinstance(v, (property, staticmethod)))
    try:
        held.append(vars(scheme))
    except TypeError:  # a scheme with only slots has no instance dict
        pass
    return held


def _module_globals(scheme: object) -> dict[tuple[str, str], str]:
    """The ``repr`` of every plain value in the globals of the modules the scheme's classes live in."""
    out: dict[tuple[str, str], str] = {}
    for module_name in _scheme_modules(scheme):
        module = sys.modules.get(module_name)
        if module is None:
            continue
        for name, value in vars(module).items():
            if not name.startswith("__") and not callable(value) and not inspect.ismodule(value):
                out[(module_name, name)] = repr(value)
    return out


def _leaks(canary: str, *values: object) -> bool:
    return any(canary in str(v) or canary in repr(v) for v in values)


def _replace_text(value: Any, old: str, new: str) -> Any:
    """``value`` (JSON-shaped) with ``old`` replaced by ``new`` in every string."""
    if isinstance(value, str):
        return value.replace(old, new)
    if isinstance(value, Mapping):
        return {k: _replace_text(v, old, new) for k, v in value.items()}
    if isinstance(value, list):
        return [_replace_text(v, old, new) for v in value]
    return value


def _credentials(inbound: Inbound) -> list[str]:
    """The strings of an inbound that carried the credential: header and meta values, and the last word of each."""
    values = [
        *inbound.get("headers", {}).values(),
        *(v for v in inbound.get("meta", {}).values() if isinstance(v, str)),
    ]
    texts = {text for value in values for text in (value, value.split()[-1] if value.split() else value)}
    return sorted(text for text in texts if len(text) >= 8)


def _outcome(call: Callable[[], Any]) -> str:
    """The result of a call as comparable text: the repr of a value, or ``code:reason`` of a refusal."""
    try:
        return repr(call())
    except IdentityError as exc:
        return f"{exc.code}:{exc.reason}"


class SchemeConformance:
    """Mixin of conformance checks. Every ``test_*`` method is one check."""

    canary: str = CANARY
    #: Does the scheme sign what the request names? False only for a double that returns canned evidence.
    sign_binds_request: bool = True

    # --- hooks ----------------------------------------------------------------------------

    def make_scheme(self) -> IdentityScheme:
        raise NotImplementedError("set make_scheme() to build the scheme under test, secrets set to self.canary")

    def requests(self) -> list[PresentRequest]:
        return [make_request()]

    def good_params(self) -> Mapping[str, Any] | None:
        return None

    def make_sign_request(self) -> PresentRequest | None:
        return None

    def make_handshake_context(self) -> HandshakeContext | None:
        return None

    def valid_inbound(self, scheme: IdentityScheme, audience: str | None = None) -> Inbound | None:
        """An inbound that ``scheme.verify`` accepts, built from the scheme's params. Required for ``verify``.

        ``scheme`` is a fresh instance the kit hands over: a verifier that issues challenges is asked for one first and
        the inbound answers it. With ``audience`` the inbound is made for that audience instead of the scheme's own: the
        kit expects a refusal. Name the audience in ``Inbound.audience``.
        """
        return None

    def message_inbound(self, scheme: IdentityScheme) -> Inbound | None:
        """An inbound message whose text is wrapped with an identity wrapper built with ``self.canary``.

        ``scheme`` is the kit's own instance: obtain a challenge from it first if it needs one. Required for a scheme
        that sets ``RETURNS_PAYLOAD = True``: the verified payload must be the text with the wrapper removed and hold
        none of the credential.
        """
        return None

    def challenge_inbound(self) -> Inbound | None:
        """An inbound request with no credential. Required when a ``verify`` with no credential raises a challenge."""
        return None

    def answer_challenge(self, challenge: Mapping[str, Any], audience: str) -> Inbound | None:
        """The inbound a holder sends to answer ``challenge`` at ``audience``. Required with ``challenge_inbound``."""
        return None

    def oversized_answer(self, challenge: Mapping[str, Any], audience: str) -> Inbound | None:
        """An otherwise valid answer to ``challenge`` padded past any sane size. Required with ``challenge_inbound``."""
        return None

    def parent_grants(self) -> list[Grant]:
        """The grants of the parent evidence the delegation checks hand a delegating scheme.

        The default is a bounded grant and an empty one. A scheme that returns a fixed grant overrides this so its
        grant fits inside the parent's: one hop deeper, the request's audience, scopes and expiry no wider.
        """
        return [
            {
                "scopes": ["read", "write"],
                "audience": PARENT_AUDIENCE,
                "valid_until": FAKE_NOW + 3600.0,
                "depth": 0,
            },
            {},
        ]

    def parent_evidence(self, grant: Grant) -> Evidence:
        """What the parent presented: a credential, plus ``grant`` when it is not empty."""
        out: Evidence = {
            "headers": {"authorization": "Bearer " + PARENT_CANARY},
            "valid_until": FAKE_NOW + 3600.0,
        }
        if grant:
            out["grant"] = grant
        return out

    def other_parent_evidence(self, grant: Grant) -> Evidence:
        """What a DIFFERENT parent presented: another credential, same grant. Must differ from ``parent_evidence``."""
        return _replace_text(self.parent_evidence(grant), PARENT_CANARY, OTHER_PARENT_CANARY)

    def secrets(self) -> Mapping[str, str]:
        """``{secret name: value}`` the kit binds as the process secret store for every check. ``self.canary`` as the
        value of each secret the scheme holds."""
        return {}

    def sections(self) -> Mapping[str, str]:
        """The ``advertise`` sections the adapter supports, each ``"map"`` or ``"list"``."""
        return {
            "mcp.capabilities.experimental": "map",
            "mcp.capabilities.extensions": "map",
            "a2a.extensions": "list",
        }

    def text_protocols(self) -> frozenset[str]:
        """The protocols whose message text a scheme may replace with ``Evidence.payload``."""
        return frozenset({"a2a"})

    def carry_keys(self) -> frozenset[str]:
        """The keys a protocol adapter lets ``ChallengeRequired.carry`` use."""
        return frozenset({"a2a.metadata_key", "a2a.data_key", "http.auth_scheme"})

    def delegation(self, grant: Grant) -> Delegation | None:
        """A valid request to derive an identity under ``grant`` (the kit adds ``now`` and the parent's evidence when
        ``needs_parent_evidence``). Required for ``delegate``."""
        return None

    def needs_parent_evidence(self) -> bool:
        """True when the scheme derives from a parent OUTSIDE its wallet (an exchange of the caller's credential): it
        then refuses a delegation with no ``parent_evidence`` and its result depends on that evidence."""
        return False

    def widening_grant(self) -> Grant | None:
        """A grant that would WIDEN the parent's authority (a scope the parent lacks, a later expiry). Required for
        ``delegate``."""
        return None

    def evidence_inbound(self, evidence: Evidence, target: str) -> Inbound | None:
        """The inbound a peer at ``target`` would see shown ``evidence``. Required for ``delegate`` with ``verify``."""
        return None

    # --- helpers --------------------------------------------------------------------------

    @pytest.fixture(autouse=True)
    def _bound_secrets(self) -> Iterator[None]:
        with bound_secrets(self.secrets()):
            yield

    def _stamped(self, inbound: Inbound | None) -> Inbound:
        assert inbound is not None, "a hook that builds an inbound returned None"
        out = copy.deepcopy(inbound)
        out.setdefault("now", FAKE_NOW)  # a harness sets it in production
        return out

    def _requests(self) -> list[PresentRequest]:
        """The requests the present-based checks run: none for a ``VerifierOnly`` scheme, which cannot present."""
        return [] if isinstance(self.make_scheme(), VerifierOnly) else self.requests()

    def _present(self, scheme: IdentityScheme, req: PresentRequest) -> Evidence:
        with forbid_wall_clock(*_scheme_modules(scheme)):
            return run(scheme.present(copy.deepcopy(req)))

    def _valid(self, out: Any, req: PresentRequest | None = None, phase: str | None = None) -> Evidence:
        """What the scheme returned, through the rulebook; a refusal becomes a failed check naming the reason."""
        try:
            return check_evidence(
                out,
                sections=self.sections(),
                text_protocols=self.text_protocols(),
                phase=phase or (req.get("phase", "initiate") if req else "initiate"),
                protocol=req.get("audience", {}).get("protocol", "") if req else "",
            )
        except IdentityError as exc:
            raise AssertionError(f"the rulebook refuses the evidence: {exc.code}:{exc.reason}") from None

    def _verify(self, scheme: IdentityScheme, inbound: Inbound) -> Any:
        with forbid_wall_clock(*_scheme_modules(scheme)):
            return run(cast(Verifier, scheme).verify(inbound))

    def _verifies(self) -> bool:
        return CAP_VERIFY in type(self.make_scheme()).capabilities

    def _own_inbound(self, scheme: IdentityScheme, audience: str | None = None) -> Inbound:
        """The scheme's ``valid_inbound`` (for ``audience`` when given), built on the instance that will verify it."""
        inbound = self.valid_inbound(scheme, audience)
        assert inbound is not None, (
            "declares verify: implement valid_inbound(scheme, audience=None), an inbound that verifies"
        )
        return self._stamped(inbound)

    def _bare(self) -> Inbound:
        return self._stamped(
            Inbound(direction="request", protocol="http", headers={}, session="session-1", request_id="request-1")
        )

    def _delegates(self) -> bool:
        return CAP_DELEGATE in type(self.make_scheme()).capabilities

    def _delegation(self, grant: Grant, *, parent: bool = True, other: bool = False) -> Delegation:
        """The author's ``delegation(grant)`` with the clock and, for a scheme that needs it, the parent's evidence."""
        base = self.delegation(grant)
        assert base is not None, "declares delegate: implement delegation(grant), a valid request to derive an identity"
        out = cast(Delegation, copy.deepcopy(base))
        out["now"] = FAKE_NOW
        if self.needs_parent_evidence() and parent:
            out["parent_evidence"] = self.other_parent_evidence(grant) if other else self.parent_evidence(grant)
        return out

    def _delegate(self, scheme: IdentityScheme, delegation: Delegation) -> Any:
        with forbid_wall_clock(*_scheme_modules(scheme)):
            return run(cast(Delegator, scheme).delegate(copy.deepcopy(delegation)))

    def _inbound_with_credential(self) -> Inbound:
        return cast(
            Inbound,
            {
                "direction": "request",
                "protocol": "http",
                "headers": {"authorization": f"Bearer {self.canary}"},
                "meta": {"x/token": self.canary},
                "session": "session-1",
                "request_id": "request-1",
            },
        )

    # --- declaration and honesty ------------------------------------------------------------

    def test_declares_the_contract(self) -> None:
        cls = type(self.make_scheme())
        assert isinstance(cls.scheme, str) and cls.scheme, "scheme must be a non-empty registry name"
        assert isinstance(cls.capabilities, frozenset), "capabilities must be a frozenset"
        assert cls.capabilities <= CAPABILITIES, "capabilities must come from the contract"
        assert isinstance(cls.PARAMS_SCHEMA, Mapping) and cls.PARAMS_SCHEMA.get("type") == "object"
        try:
            check_params_schema(cls.PARAMS_SCHEMA)
        except IdentityError:
            raise AssertionError("PARAMS_SCHEMA is not a valid JSON schema") from None
        assert inspect.iscoroutinefunction(cls.present), "present must be async"

    def test_capability_honesty_both_ways(self) -> None:
        scheme = self.make_scheme()
        cls = type(scheme)
        declared = set(cls.capabilities)
        for capability, contract in CAPABILITY_INTERFACES.items():
            subclassed = contract in cls.__mro__
            assert (capability in declared) == subclassed, (
                f"capability {capability!r}: declared={capability in declared}, contract subclassed={subclassed}"
            )
        if CAP_VERIFY in declared:
            assert inspect.iscoroutinefunction(cast(Verifier, scheme).verify)
        if CAP_DESCRIBE in declared:
            assert inspect.iscoroutinefunction(cast(Describer, scheme).describe)
        if CAP_REVOKE in declared:
            assert inspect.iscoroutinefunction(cast(Revoker, scheme).revoke)
        if CAP_HANDSHAKE in declared:
            assert inspect.iscoroutinefunction(cast(Handshaker, scheme).handshake)

    def test_from_spec_rejects_bad_params(self) -> None:
        cls = type(self.make_scheme())
        schema = cls.PARAMS_SCHEMA
        base = self.good_params()
        assert base is not None, "implement good_params(): params that build the scheme"
        assert not validate(base, schema), "good_params() does not satisfy PARAMS_SCHEMA"
        cls.from_spec(base)
        candidates: list[dict[str, Any]] = []
        props: Mapping[str, Any] = schema.get("properties") or {}
        if schema.get("required"):
            candidates.append({})
        if schema.get("additionalProperties") is False:
            candidates.append({**base, "zz_unknown_key": self.canary})
        for name, sub in props.items():
            wrong: Any = (
                [self.canary] if isinstance(sub, Mapping) and sub.get("type") == "object" else {"v": self.canary}
            )
            candidates.append({**base, name: wrong})
        assert candidates, "the schema gives the kit nothing to reject; add required or additionalProperties"
        for params in candidates:
            try:
                cls.from_spec(params)
            except IdentityError as exc:
                assert exc.code == "invalid", "from_spec must raise IdentityError('invalid')"
                assert not _leaks(self.canary, exc), "from_spec error shows a value"
            else:
                raise AssertionError("from_spec accepted params that break PARAMS_SCHEMA")

    # --- the wallet ---------------------------------------------------------------------------------

    def _listed(self, scheme: IdentityScheme) -> list[Any]:
        try:
            return [check_identity_info(info) for info in scheme.identities()]
        except IdentityError as bad:
            raise AssertionError(f"malformed identity: {bad.reason}") from None

    def test_the_wallet_lists_value_free_identities(self) -> None:
        scheme = self.make_scheme()
        cls = type(scheme)
        assert not inspect.iscoroutinefunction(cls.identities), "identities is a plain method"
        assert not inspect.iscoroutinefunction(cls.select), "select is a plain method"
        listed = self._listed(scheme)
        if isinstance(scheme, VerifierOnly):
            assert listed == [], "a scheme that only verifies holds no identity"
            return
        assert listed, "a scheme that presents lists at least one identity"
        ids = [info["id"] for info in listed]
        assert len(set(ids)) == len(ids), "identity ids must be unique inside a wallet"
        assert not _leaks(self.canary, listed), "identities show a secret"
        assert listed == self._listed(self.make_scheme()), "identities differ between two fresh instances"

    def test_select_names_a_listed_identity_and_presents_nothing(self) -> None:
        for req in self._requests():
            scheme = self.make_scheme()
            before = copy.deepcopy(req)
            chosen = scheme.select(req)
            assert req == before, "select mutated its request"
            assert chosen is None or chosen in {info["id"] for info in self._listed(scheme)}, (
                "select named an identity the wallet does not list"
            )
            assert chosen == self.make_scheme().select(req), "select differs between two fresh instances"
            assert not _leaks(self.canary, chosen), "select shows a secret"

    def test_present_shows_the_identity_it_is_asked_for(self) -> None:
        for req in self._requests():
            chosen = self.make_scheme().select(req)
            if chosen is None:
                continue
            by_name = self._present(self.make_scheme(), {**req, "identity": chosen})
            assert by_name == self._present(self.make_scheme(), req), "naming the selected identity changes the answer"
            self._valid(by_name, req)
            try:
                self._present(self.make_scheme(), {**req, "identity": "no-such-identity"})
            except IdentityError as exc:
                assert exc.code in {"no_credential", "refused", "unsatisfiable"}, f"an unknown identity got {exc.code}"
            else:
                raise AssertionError("present showed an identity the wallet does not hold")

    def test_no_revealed_secret_is_kept_on_the_object(self) -> None:
        """A scheme reveals a ``SecretRef`` into a local variable only: the object holds the ref, never the value."""
        scheme = self.make_scheme()
        before = _module_globals(scheme)  # the module's own constants (a test's canary) are not a leak: what changed is
        for req in self._requests():
            try:
                self._present(scheme, req)
            except IdentityError:
                continue
        assert not _leaks(self.canary, *_kept_state(scheme)), "the scheme keeps a revealed secret on itself"
        changed = [
            key for key, text in _module_globals(scheme).items() if before.get(key) != text and self.canary in text
        ]
        assert not changed, "the scheme keeps a revealed secret in a module global"

    # --- present: determinism and output -----------------------------------------------------

    def test_present_output_is_valid(self) -> None:
        for req in self._requests():
            out = self._present(self.make_scheme(), req)
            clean = self._valid(out, req)
            assert not _leaks(self.canary, clean.get("advertise", {})), "advertise shows a secret"
            until = out.get("valid_until")
            if until is not None:
                now = float(req["now"])
                assert now < until <= now + 366 * 86400, "valid_until must be derived from request['now']"

    def test_a_holder_does_not_answer_a_challenge_bound_to_another_audience(self) -> None:
        """The host side of a challenge: a peer's challenge names the audience it is for, and an answer for any other is
        refused before the scheme is asked (the credential would go to the wrong peer)."""
        from mas.library.standard.lib.identity.caller import Caller

        scheme = self.make_scheme()
        for req in self._requests():
            challenged = {
                **req,
                "phase": "challenge",
                "peer": {"challenge": {"nonce": "n-kit", "audience": "https://foreign.invalid"}},
            }
            with pytest.raises(IdentityError) as refused:
                run(Caller("kit", scheme).present(cast(PresentRequest, challenged)))
            assert refused.value.code == "refused", "a foreign-audience challenge was answered or failed another way"

    def test_present_is_pure_given_params_and_request(self) -> None:
        for req in self._requests():
            first = self._present(self.make_scheme(), req)
            assert first == self._present(self.make_scheme(), req), "present differs for identical params and request"
            scheme = self.make_scheme()
            mine = copy.deepcopy(req)
            with forbid_wall_clock(*_scheme_modules(scheme)):
                same = run(scheme.present(mine))
            assert mine == req, "present mutated its request"
            assert same == first, "present differs for identical params and request"
            assert self._present(scheme, req) == first, "present differs when the same instance is asked twice"

    def test_sign_is_exercised(self) -> None:
        scheme = self.make_scheme()
        if CAP_SIGN in type(scheme).capabilities:
            req = self.make_sign_request()
            assert req is not None, "declares sign: implement make_sign_request()"
            base = self._valid(self._present(scheme, {**req, "phase": "sign"}), req, "sign")
            view = req.get("request", {}) if self.sign_binds_request else {}
            for field, other in (
                ("body_sha256", "0" * 64),
                ("url", "https://elsewhere.invalid/x"),
                ("request_id", "r-other"),
            ):
                if field not in view:
                    continue
                changed = {**req, "phase": "sign", "request": {**view, field: other}}
                assert self._present(self.make_scheme(), changed) != base, (
                    f"sign evidence does not change when the request's {field} does: it signs nothing it was given"
                )

    def test_sign_phase_is_honest(self) -> None:
        """A scheme without the ``sign`` capability refuses phase ``sign``."""
        if CAP_SIGN in type(self.make_scheme()).capabilities:
            return
        try:
            self._present(self.make_scheme(), make_request(phase="sign", request={"method": "GET", "url": "https://x"}))
        except IdentityError:
            return
        raise AssertionError("a scheme without the sign capability presented on phase sign")

    def test_handshake_is_exercised(self) -> None:
        scheme = self.make_scheme()
        if CAP_HANDSHAKE in type(scheme).capabilities:
            ctx = self.make_handshake_context()
            assert ctx is not None, "declares handshake: implement make_handshake_context()"
            self._valid(run(cast(Handshaker, scheme).handshake(ctx)), None, "handshake")

    # --- no secret anywhere --------------------------------------------------------------------

    def test_errors_are_value_free(self) -> None:
        scheme = self.make_scheme()
        hostile: list[PresentRequest] = [
            cast(PresentRequest, {"record": "rec1", "identity": "id1", "now": 1_000_000.0}),
            cast(PresentRequest, {"record": "rec1", "identity": self.canary, "now": 1_000_000.0}),
            make_request(phase="challenge", peer={"challenge": self.canary}),
            make_request(phase="challenge", peer={"challenge": {"nonce": self.canary, "audience": self.canary}}),
            make_request(target=self.canary, task=self.canary),
            *self.requests(),
        ]
        assert not _leaks(self.canary, scheme), "repr of the scheme shows a secret"
        for req in hostile:
            try:
                run(scheme.present(copy.deepcopy(req)))
            except IdentityError as exc:
                assert not _leaks(self.canary, exc, exc.code, exc.reason), "an IdentityError shows a value"
            except Exception as exc:
                assert not _leaks(self.canary, exc), "an exception message shows a value"

    def test_describe_is_public(self) -> None:
        scheme = self.make_scheme()
        if CAP_DESCRIBE not in type(scheme).capabilities:
            return
        view = check_public_view(run(cast(Describer, scheme).describe("id1")))
        assert not _leaks(self.canary, view), "describe shows a secret"

    def test_verify_denies_or_verifies_cleanly(self) -> None:
        scheme = self.make_scheme()
        if CAP_VERIFY not in type(scheme).capabilities:
            return
        try:
            verified = self._verify(scheme, self._inbound_with_credential())
        except IdentityError as exc:
            assert not _leaks(self.canary, exc), "a verify refusal shows the credential"
            return
        assert not _leaks(self.canary, check_verified(verified)), "verify echoed the credential it was shown"

    def test_verify_refuses_an_inbound_without_a_credential(self) -> None:
        """An inbound that shows nothing is refused or answered with a challenge, unless ``ANONYMOUS_OK``."""
        scheme = self.make_scheme()
        if CAP_VERIFY not in type(scheme).capabilities:
            return
        inbound = self.challenge_inbound()
        try:
            verified = self._verify(scheme, self._bare() if inbound is None else self._stamped(inbound))
        except ChallengeRequired as exc:
            self._check_challenge(exc)
            if cast(Verifier, scheme).ADVISORY_CHALLENGE:
                return  # advisory: no hooks; test_an_advisory_challenge_is_static_and_value_free runs its checks
            assert "nonce" in exc.challenge, _NO_NONCE
            assert inbound is not None, (
                "verify answers with a challenge: implement challenge_inbound(), answer_challenge() and "
                "oversized_answer()"
            )
        except IdentityError:
            return
        else:
            if not cast(Verifier, scheme).ANONYMOUS_OK:
                raise AssertionError(f"verify accepted an inbound that showed no credential: {sorted(verified)}")

    def test_a_verifier_accepts_its_valid_inbound(self) -> None:
        """The positive control: verify accepts the inbound the scheme's author built, with a valid ``Verified``."""
        if not self._verifies():
            return

        def once() -> tuple[Inbound, Any]:
            scheme = self.make_scheme()
            inbound = self._own_inbound(scheme)
            return inbound, self._verify(scheme, copy.deepcopy(inbound))

        try:
            inbound, verified = once()
        except IdentityError as exc:
            raise AssertionError(f"verify refused its own valid inbound: {exc.code}:{exc.reason}") from None
        try:
            clean = check_verified(verified)
        except IdentityError as bad:
            raise AssertionError(f"malformed Verified: {bad.reason}") from None
        for text in [self.canary, *_credentials(inbound)]:
            assert not _leaks(text, clean), "verify echoed the credential it was shown"
        assert _outcome(lambda: once()[1]) == repr(verified), "verify differs between two fresh instances"

    def test_a_verifier_refuses_what_is_not_valid(self) -> None:
        """Its valid inbound with the credential removed, or made for another audience, is refused."""
        if not self._verifies() or cast(Verifier, self.make_scheme()).ANONYMOUS_OK:
            return
        scheme = self.make_scheme()
        stripped = {k: v for k, v in self._own_inbound(scheme).items() if k not in _CREDENTIAL_KEYS}
        stripped["headers"] = {}
        try:
            self._verify(scheme, cast(Inbound, stripped))
        except IdentityError:
            pass
        else:
            raise AssertionError("verify accepted its valid inbound with the credential removed")
        scheme = self.make_scheme()
        try:
            self._verify(scheme, self._own_inbound(scheme, ELSEWHERE))
        except IdentityError:
            pass
        else:
            raise AssertionError(
                "verify accepted an inbound made for another audience (or valid_inbound() ignores its audience)"
            )

    def test_verify_is_deterministic(self) -> None:
        if not self._verifies():
            return

        def once(valid: bool) -> str:
            scheme = self.make_scheme()
            inbound = self._own_inbound(scheme) if valid else self._inbound_with_credential()
            return _outcome(lambda: self._verify(scheme, inbound))

        for valid in (False, True):
            assert once(valid) == once(valid), "verify differs between two fresh instances for the same inbound"

    def test_a_verified_payload_never_holds_the_credential(self) -> None:
        """``RETURNS_PAYLOAD`` is declared truthfully: a payload only when declared, and then a clean one."""
        if not self._verifies():
            return
        scheme = self.make_scheme()
        if not cast(Verifier, scheme).RETURNS_PAYLOAD:
            probe = self.make_scheme()
            text = {**self._own_inbound(probe), "payload": "a message with no identity wrapper"}
            try:
                probed = self._verify(probe, cast(Inbound, text))
            except IdentityError:
                return
            assert probed.get("payload") is None, "verify returns a payload: declare RETURNS_PAYLOAD = True"
            return
        inbound = self.message_inbound(scheme)
        assert inbound is not None, "declares RETURNS_PAYLOAD: implement message_inbound(scheme), a wrapped message"
        verified = self._verify(scheme, self._stamped(inbound))
        payload = verified.get("payload")
        assert payload is not None, "declares RETURNS_PAYLOAD but message_inbound() yields no verified payload"
        assert not _leaks(self.canary, payload), "the verified payload carries the credential"
        assert payload != inbound.get("payload"), "the verified payload still has the identity wrapper"
        try:
            check_verified(verified)
        except IdentityError as bad:
            raise AssertionError(f"malformed Verified: {bad.reason}") from None

    # --- challenges: bound to the audience and used once ------------------------------------------

    def test_challenges_are_single_use_and_audience_bound(self) -> None:
        scheme = self.make_scheme()
        if CAP_VERIFY not in type(scheme).capabilities or cast(Verifier, scheme).ADVISORY_CHALLENGE:
            return
        hook = self.challenge_inbound()
        if hook is None:
            try:
                self._verify(self.make_scheme(), self._bare())
            except ChallengeRequired as exc:
                self._check_challenge(exc)
                assert "nonce" in exc.challenge, _NO_NONCE
                raise AssertionError(
                    "verify answers with a challenge: implement challenge_inbound(), answer_challenge() and "
                    "oversized_answer()"
                ) from None
            except IdentityError:
                return
            return
        inbound = self._stamped(hook)
        audience = inbound.get("audience", {}).get("target", "")
        issued: list[Mapping[str, Any]] = []
        for _ in range(2):
            try:
                self._verify(scheme, copy.deepcopy(inbound))
            except ChallengeRequired as exc:
                assert exc.code == "challenge_required" and str(exc) == "challenge_required", (
                    "str(error) must be its code only"
                )
                assert not _leaks(self.canary, exc, exc.challenge), "a challenge shows a secret"
                self._check_challenge(exc)
                issued.append(exc.challenge)
            else:
                raise AssertionError("verify accepted an inbound request that showed no credential")
        nonces = [c.get("nonce") for c in issued]
        assert all(isinstance(n, str) and n for n in nonces), _NO_NONCE
        assert nonces[0] != nonces[1], "a challenge nonce was issued twice"
        assert all(c.get("audience") == audience for c in issued), "a challenge must name the audience it is bound to"
        first = self.answer_challenge(issued[0], audience)
        assert first is not None, "verify answers with a challenge: implement answer_challenge()"
        first = self._stamped(first)
        self._verify(scheme, copy.deepcopy(first))  # the right answer is accepted once
        refusals: list[str] = []
        for replay in (first, self._stamped(self.answer_challenge(issued[0], audience))):
            try:
                self._verify(scheme, copy.deepcopy(replay))
            except IdentityError as exc:
                refusals.append(f"{exc.code}:{exc.reason}")
                continue
            raise AssertionError("a challenge answer was accepted twice")
        elsewhere = self._stamped(self.answer_challenge(issued[1], ELSEWHERE))
        try:
            self._verify(scheme, elsewhere)
        except IdentityError as exc:
            refusals.append(f"{exc.code}:{exc.reason}")
        else:
            raise AssertionError("a challenge answer was accepted for another audience")
        assert len(set(refusals)) == 1, "a replayed or wrongly bound challenge must get one uniform refusal"
        try:
            self._verify(scheme, copy.deepcopy(inbound))
        except ChallengeRequired as exc:
            fresh = exc.challenge
        else:
            raise AssertionError("verify accepted an inbound request that showed no credential")
        huge = self.oversized_answer(fresh, audience)
        assert huge is not None, "verify answers with a challenge: implement oversized_answer()"
        try:
            self._verify(scheme, self._stamped(huge))
        except IdentityError:
            pass
        else:
            raise AssertionError("an oversized answer was accepted")

    def _ask(self, scheme: IdentityScheme, audience: str, **headers: str) -> ChallengeRequired | None:
        """An advisory verifier's challenge to a request with no credential for ``audience``, or None."""
        inbound = self._stamped(self.challenge_inbound() or self._bare())
        inbound["audience"] = {"protocol": inbound.get("audience", {}).get("protocol", "http"), "target": audience}
        inbound["headers"] = {**inbound.get("headers", {}), **headers}
        try:
            self._verify(scheme, inbound)
        except ChallengeRequired as exc:
            return exc
        except IdentityError:
            return None
        return None

    def test_an_advisory_challenge_is_static_and_value_free(self) -> None:
        """``ADVISORY_CHALLENGE``: a hint with no nonce, nothing secret, only its own audience, the same each time."""
        if not self._verifies() or not cast(Verifier, self.make_scheme()).ADVISORY_CHALLENGE:
            return
        audience = self._own_inbound(self.make_scheme()).get("audience", {}).get("target", "")
        exc = self._ask(self.make_scheme(), audience)
        if exc is None:
            return  # it refuses instead; test_verify_refuses_an_inbound_without_a_credential holds the rest
        assert "nonce" not in exc.challenge, (
            "an advisory challenge carries a nonce, which claims single use: clear ADVISORY_CHALLENGE (a "
            "nonce-bound challenge) or drop the nonce from the hint"
        )
        self._check_challenge(exc)
        assert not _leaks(self.canary, exc, exc.challenge), "an advisory challenge shows a secret"
        assert len(repr((exc.challenge, exc.carry, exc.wire))) <= MAX_WIRE, "an advisory challenge hint is too large"
        echoed = self._ask(self.make_scheme(), audience, **{"x-echo-mark": ECHO_MARK})
        if echoed is not None:
            assert not _leaks(ECHO_MARK, echoed, echoed.challenge, echoed.carry, echoed.wire), (
                "an advisory challenge echoes an inbound header value"
            )
        credentialed = self._stamped(self._inbound_with_credential())
        credentialed["audience"] = {"protocol": "http", "target": audience}
        try:
            self._verify(self.make_scheme(), credentialed)
        except ChallengeRequired as again:
            assert not _leaks(self.canary, again, again.challenge, again.carry, again.wire), (
                "an advisory challenge echoes the credential it was shown"
            )
        except IdentityError:
            pass
        same = [self._ask(self.make_scheme(), audience) for _ in range(2)]
        scheme = self.make_scheme()
        same += [self._ask(scheme, audience), self._ask(scheme, audience)]
        shapes = {repr((e.challenge, e.carry, e.wire)) if e else None for e in same}
        assert len(shapes) == 1, "an advisory challenge differs between calls for the same request"
        other = self._ask(self.make_scheme(), ELSEWHERE)
        for mine, theirs in ((exc, ELSEWHERE), (other, audience)):
            if mine is not None and theirs:
                assert theirs not in repr((mine.challenge, mine.carry, mine.wire)), (
                    "an advisory challenge names another audience"
                )

    def _check_challenge(self, exc: ChallengeRequired) -> None:
        try:
            check_challenge(exc.challenge)  # reserved keys typed, extra keys namespaced with "/"
            check_carry(exc.carry, keys=self.carry_keys())  # carrier hints from the adapter's set only
            check_wire(exc.wire)  # bounded text
        except IdentityError as bad:
            raise AssertionError(f"malformed challenge: {bad.reason}") from None
        assert not _leaks(self.canary, exc.carry), "a challenge carry shows a secret"
        if exc.wire is not None:
            assert exc.wire not in str(exc) + repr(exc), "the wire challenge is echoed by the error"
            assert not _leaks(self.canary, exc.wire), "the wire challenge shows a secret"

    # --- delegation -------------------------------------------------------------------------------

    def _parent_grant(self, grant: Grant, scheme: IdentityScheme, delegation: Delegation) -> Grant:
        """The grant the derived identity must fit inside: the parent evidence's, or the parent identity's own."""
        if self.needs_parent_evidence():
            return grant
        parent_id = delegation.get("identity") or scheme.select(self.requests()[0])
        for info in scheme.identities():
            if info["id"] == parent_id:
                return info.get("grant", {})
        return {}

    def _outcomes(self) -> list[tuple[Grant, Delegation, Any, IdentityError | None]]:
        """Every parent grant's delegation, with the identity it made or the refusal."""
        got: list[tuple[Grant, Delegation, Any, IdentityError | None]] = []
        for grant in self.parent_grants():
            delegation = self._delegation(grant)
            try:
                got.append((grant, delegation, self._delegate(self.make_scheme(), delegation), None))
            except IdentityError as exc:
                got.append((grant, delegation, None, exc))
        return got

    def _present_as(self, scheme: IdentityScheme, identity: str) -> Evidence:
        req = copy.deepcopy(self._requests()[0])
        req["identity"] = identity
        return self._present(scheme, req)

    def test_a_delegating_scheme_delegates(self) -> None:
        """A scheme that declares ``delegate`` derives an identity into its wallet under a NON-EMPTY parent grant."""
        if not self._delegates():
            return
        assert any(self.parent_grants()), "parent_grants() needs a non-empty grant: the narrowing check proves nothing"
        assert self.widening_grant() is not None, "declares delegate: implement widening_grant()"
        done = [(grant, info) for grant, _, info, _ in self._outcomes() if info is not None]
        assert done, "a scheme that declares delegate refused every parent: it never delegates"
        assert any(grant for grant, _ in done), "it delegated only for an unknown parent grant, never for a bounded one"
        scheme = self.make_scheme()
        grant = next(g for g in self.parent_grants() if g)
        info = check_identity_info(self._delegate(scheme, self._delegation(grant)))
        assert info["status"] == "active", "a new delegated identity is active"
        assert info.get("parent"), "a delegated identity names its parent"
        assert info["id"] in {i["id"] for i in self._listed(scheme)}, "the derived identity is not in the wallet"
        self._valid(self._present_as(scheme, info["id"]), self._requests()[0])

    def test_delegation_without_a_needed_parent_refuses(self) -> None:
        if not self._delegates() or not self.needs_parent_evidence():
            return
        for grant in self.parent_grants():
            try:
                self._delegate(self.make_scheme(), self._delegation(grant, parent=False))
            except IdentityError as exc:
                assert (exc.code, exc.reason) == ("unsatisfiable", "needs_parent_evidence"), (
                    f"a delegation with no parent evidence must be unsatisfiable:needs_parent_evidence, got "
                    f"{exc.code}:{exc.reason}"
                )
            else:
                raise AssertionError("a delegation with no parent evidence made an identity (its own credential?)")

    def test_a_delegation_acts_as_the_parent(self) -> None:
        """Delegated evidence never holds the scheme's own credential."""
        if not self._delegates():
            return
        for _, _, info, _ in self._outcomes():
            if info is None:
                continue
            scheme = self.make_scheme()
            made = self._delegate(scheme, self._outcomes_delegation(info))
            text = repr(self._present_as(scheme, made["id"]))
            assert self.canary not in text, "a delegation presented the scheme's own credential"

    def _outcomes_delegation(self, info: Any) -> Delegation:
        for _, delegation, got, _ in self._outcomes():
            if got is not None and got["id"] == info["id"]:
                return delegation
        raise AssertionError("no delegation produced this identity")

    def test_delegation_only_narrows(self) -> None:
        if not self._delegates():
            return
        for grant, delegation, info, _ in self._outcomes():
            if info is None:
                continue  # refusing is always safe
            clean = check_identity_info(info)
            if grant and "grant" not in clean:
                raise AssertionError("the parent declares a grant: the derived identity must declare its own")
            if "grant" in clean:
                parent = self._parent_grant(grant, self.make_scheme(), delegation)
                try:
                    check_narrowing(parent, clean["grant"], target=delegation.get("audience", {}).get("target", ""))
                except IdentityError as exc:
                    raise AssertionError(f"the delegation widens its parent: {exc.code}:{exc.reason}") from None
        wide = self.widening_grant()
        if wide is not None:
            delegation = self._delegation(self.parent_grants()[0])
            delegation["grant"] = wide
            try:
                made = self._delegate(self.make_scheme(), delegation)
            except IdentityError as exc:
                assert exc.code in {"refused", "unsatisfiable"}, f"a widening grant got {exc.code}"
            else:
                raise AssertionError(f"a widening grant made an identity: {made['id']}")

    def test_a_delegation_is_deterministic(self) -> None:
        if not self._delegates():
            return
        for _, delegation, _, _ in self._outcomes():
            first, second = (_outcome(lambda: self._delegate(self.make_scheme(), delegation)) for _ in range(2))  # noqa: B023
            assert first == second, "a delegation differs between instances"
            scheme = self.make_scheme()
            again = [_outcome(lambda: self._delegate(scheme, delegation)) for _ in range(2)]  # noqa: B023
            assert again == [first, first], "the same delegation asked twice must give the same identity"

    def test_delegated_evidence_derives_from_the_parent(self) -> None:
        """Metamorphic: two different parents give two different evidences. A credential derived from the scheme's own
        (a hash, a re-signature with no parent input) is the same for both."""
        if not self._delegates() or not self.needs_parent_evidence():
            return
        for grant in self.parent_grants():
            assert self.other_parent_evidence(grant) != self.parent_evidence(grant), (
                "other_parent_evidence() must differ from parent_evidence()"
            )
            results: list[Evidence | None] = []
            for other in (False, True):
                scheme = self.make_scheme()
                try:
                    info = self._delegate(scheme, self._delegation(grant, other=other))
                    results.append(self._present_as(scheme, info["id"]))
                except IdentityError:
                    results.append(None)
            if results == [None, None]:
                continue  # refusing is always safe
            assert None not in results, "a delegation is refused for one parent and not for another"
            assert results[0] != results[1], "a delegation does not depend on the parent: two parents, one evidence"

    def test_delegated_evidence_is_bound_and_value_free(self) -> None:
        """The evidence of a derived identity names the request's audience, shows no secret, and evidence made for one
        audience is refused by the verifier of another."""
        verifies = CAP_VERIFY in type(self.make_scheme()).capabilities
        shown: list[tuple[PresentRequest, Evidence, bool]] = []
        for req in self._requests():
            try:
                shown.append((req, self._valid(self._present(self.make_scheme(), req), req), False))
            except IdentityError:
                continue
        if self._delegates() and self._requests():
            req = self._requests()[0]
            for _, delegation, info, exc in self._outcomes():
                if exc is not None:
                    assert not _leaks(self.canary, exc, exc.reason), "a refusal shows a secret"
                elif info is not None:
                    scheme = self.make_scheme()
                    made = self._delegate(scheme, delegation)
                    shown.append((req, self._valid(self._present_as(scheme, made["id"]), req), True))
        for req, out, delegated in shown:
            assert not _leaks(self.canary, out.get("grant", {})), "a grant shows a secret"
            audience = out.get("grant", {}).get("audience")
            assert audience is None or audience == req["audience"]["target"], "the grant names another audience"
            if delegated and verifies:  # a positive control: the check below proves nothing if verify refuses all
                own = self.evidence_inbound(out, req["audience"]["target"])
                assert own is not None, "declares delegate and verify: implement evidence_inbound()"
                try:
                    self._verify(self.make_scheme(), copy.deepcopy(own))
                except IdentityError as exc:
                    raise AssertionError(
                        f"verify refused the delegated evidence made for its own audience: {exc.code}:{exc.reason}"
                    ) from None
            inbound = self.evidence_inbound(out, ELSEWHERE)
            if inbound is None or not verifies:
                continue
            try:
                self._verify(self.make_scheme(), copy.deepcopy(inbound))
            except IdentityError:
                continue
            raise AssertionError("evidence made for one audience was accepted for another")

    # --- revocation ---------------------------------------------------------------------------------

    def test_a_revoked_identity_refuses_everything(self) -> None:
        scheme = self.make_scheme()
        if CAP_REVOKE not in type(scheme).capabilities or not self._requests():
            return
        listed = self._listed(scheme)
        ref = listed[0]["id"]
        run(cast(Revoker, scheme).revoke(ref))
        assert {info["id"]: info["status"] for info in self._listed(scheme)}[ref] == "revoked", (
            "a revoked identity must be listed as revoked"
        )
        for req in self._requests():
            try:
                self._present(scheme, {**req, "identity": ref})
            except IdentityError as exc:
                assert exc.code in {"revoked", "no_credential"}, f"a revoked identity answered {exc.code}"
            else:
                raise AssertionError("a revoked identity still presents")
        if len(listed) == 1 and CAP_HANDSHAKE in type(scheme).capabilities:
            ctx = self.make_handshake_context()
            if ctx is not None:
                try:
                    run(cast(Handshaker, scheme).handshake(ctx))
                except IdentityError:
                    pass
                else:
                    raise AssertionError("a revoked identity still handshakes")

    def test_revoking_a_parent_revokes_what_it_derived(self) -> None:
        if not self._delegates() or CAP_REVOKE not in type(self.make_scheme()).capabilities or not self._requests():
            return
        grant = next((g for g in self.parent_grants() if g), self.parent_grants()[0])
        scheme = self.make_scheme()
        info = self._delegate(scheme, self._delegation(grant))
        listed = {i["id"] for i in self._listed(scheme)}
        target = info["parent"] if info.get("parent") in listed else info["id"]
        run(cast(Revoker, scheme).revoke(target))
        try:
            self._present_as(scheme, info["id"])
        except IdentityError as exc:
            assert exc.code in {"revoked", "no_credential"}, f"a revoked delegation answered {exc.code}"
        else:
            raise AssertionError("an identity derived from a revoked identity still presents")
