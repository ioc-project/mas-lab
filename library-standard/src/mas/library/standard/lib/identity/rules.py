#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The rules a host applies to what a scheme returns, and helpers a scheme author uses. Errors are value-free.

Header limits repeat the documented comm-header constants (32 headers, 128-character names, 4096-byte values, 16384
bytes in all, token names, no control characters). The deny-list adds the framing headers a credential must never set.
Errors name a reason, never the offending value. What a protocol adapter supports (the ``advertise`` sections, the
protocols whose message text a scheme may wrap, the keys that carry a challenge) is the adapter's to declare: the checks
that need it take it as an argument.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from typing import Any, cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from mas.library.standard.lib.identity.contract import (
    GRANT_AXES,
    IDENTITY_STATUSES,
    MAX_CHAIN,
    MAX_DELEGATION_DEPTH,
    MAX_GRANT_SCOPES,
    MAX_PAYLOAD,
    MAX_SCOPE,
    MAX_WIRE,
    VERIFIED_SOURCES,
    Evidence,
    Grant,
    IdentityError,
    IdentityInfo,
    PublicView,
    Verified,
)
from mas.runtime.secrets import SecretRef

MAX_HEADER_COUNT = 32
MAX_HEADER_NAME = 128
MAX_HEADER_VALUE = 4096
MAX_HEADER_TOTAL = 16384
#: Names evidence may never set: they frame the message, they do not identify anyone.
DENIED_HEADERS: frozenset[str] = frozenset({"host", "content-length", "transfer-encoding", "connection"})
#: Seconds before ``valid_until`` at which evidence counts as expired (a clock may be a little behind the peer's).
DEFAULT_SKEW = 5.0

_EVIDENCE_KEYS: frozenset[str] = frozenset(Evidence.__annotations__)
_PUBLIC_VIEW_KEYS: frozenset[str] = frozenset(PublicView.__annotations__)
_VERIFIED_KEYS: frozenset[str] = frozenset(Verified.__annotations__)
_INFO_KEYS: frozenset[str] = frozenset(IdentityInfo.__annotations__)
_MAX_ADVERTISE_BYTES = 8192
_MAX_ADVERTISE_DEPTH = 6
_MAX_ADVERTISE_LIST = 32
_MAX_ADVERTISE_ITEM = 512
_SCOPES: frozenset[str] = frozenset({"task", "audience"})
_SLUG = re.compile(r"^[a-z0-9_.:-]{1,64}$")

TOKEN = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")


def invalid(reason: str) -> IdentityError:
    """A value-free ``invalid`` error with ``reason``."""
    return IdentityError("invalid", reason)


def _has_forbidden_char(value: str) -> bool:
    return any(ord(ch) < 32 or ord(ch) == 127 or ch in "  " for ch in value)


def finite_number(value: Any) -> bool:
    """True for a finite int or float (not a bool)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


# --- params -------------------------------------------------------------------------------------


def secret_params(schema: Mapping[str, Any]) -> set[str]:
    """The names of the properties a params schema marks ``"x-secret": true``."""
    props: Mapping[str, Any] = schema.get("properties") or {}
    return {name for name, sub in props.items() if isinstance(sub, Mapping) and sub.get("x-secret") is True}


def validate(value: Any, schema: Mapping[str, Any]) -> list[str]:
    """All violations of a JSON schema (draft 2020-12) by ``value``, as ``"<path>: <keyword>"``. Empty means valid.

    A scheme calls it in ``from_spec`` on its params. A property marked ``"x-secret": true`` must hold a
    :class:`~mas.runtime.secrets.SecretRef`; the schema says nothing else about it. An error names the path and the
    rule, never the value.
    """
    props: Mapping[str, Any] = schema.get("properties") or {}
    secret = secret_params(schema)
    out: list[str] = []
    if secret and isinstance(value, Mapping):
        out = [
            f"$.{name}: secret_ref"
            for name in sorted(secret)
            if name in value and not isinstance(value[name], SecretRef)
        ]
        value = {k: (None if k in secret else v) for k, v in value.items()}
        schema = {**schema, "properties": {k: ({} if k in secret else v) for k, v in props.items()}}
    for error in Draft202012Validator(dict(schema)).iter_errors(value):
        path = "$" + "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in error.absolute_path)
        out.append(f"{path}: {error.validator}")
    return out


def check_params_schema(schema: Mapping[str, Any]) -> None:
    """Raise ``invalid:params_schema`` (value-free) when ``schema`` is not a valid JSON schema."""
    try:
        Draft202012Validator.check_schema(dict(schema))
    except SchemaError:
        raise invalid("params_schema") from None


# --- headers, meta, evidence ----------------------------------------------------------------------


def check_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    """Validate and lowercase header names. Raises ``IdentityError("invalid", reason)``, never echoing a value."""
    if headers is None:
        return {}
    if not isinstance(headers, Mapping):
        raise invalid("headers_not_mapping")
    if len(headers) > MAX_HEADER_COUNT:
        raise invalid("headers_too_many")
    out: dict[str, str] = {}
    total = 0
    for key, value in headers.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise invalid("headers_not_strings")
        if not key or not TOKEN.match(key):
            raise invalid("header_name_not_token")
        if _has_forbidden_char(value):
            raise invalid("header_control_char")
        name = key.lower()
        if name in DENIED_HEADERS:
            raise invalid("header_denied")
        value_bytes = value.encode("utf-8")
        if len(name) > MAX_HEADER_NAME or len(value_bytes) > MAX_HEADER_VALUE:
            raise invalid("header_too_long")
        total += len(name) + len(value_bytes)
        if total > MAX_HEADER_TOTAL:
            raise invalid("headers_total_too_long")
        out[name] = value
    return out


def check_meta(meta: Mapping[str, Any] | None) -> dict[str, Any]:
    """Every ``meta`` key is a string containing ``/`` (namespaced), so schemes cannot collide."""
    if meta is None:
        return {}
    if not isinstance(meta, Mapping):
        raise invalid("meta_not_mapping")
    for key in meta:
        if not isinstance(key, str) or "/" not in key or key.startswith("/") or key.endswith("/"):
            raise invalid("meta_key_not_namespaced")
    return dict(meta)


#: Most characters of an inbound ``meta`` once serialized: a peer chose it, a verifier reads it.
MAX_INBOUND_META = 16384


def check_inbound_meta(meta: Any) -> dict[str, Any]:
    """An inbound ``meta`` (peer-chosen): a mapping of text keys to JSON values, bounded in depth and in size."""
    if not isinstance(meta, Mapping) or not all(isinstance(k, str) for k in meta):
        raise invalid("inbound_meta")
    if not _json_able(meta):
        raise invalid("inbound_meta")
    try:
        size = len(json.dumps(meta, separators=(",", ":")))
    except (TypeError, ValueError):
        raise invalid("inbound_meta") from None
    if size > MAX_INBOUND_META:
        raise invalid("inbound_meta_too_large")
    return dict(meta)


def _json_able(value: Any, depth: int = 0) -> bool:
    if depth > _MAX_ADVERTISE_DEPTH:
        return False
    if value is None or isinstance(value, (str, bool)):
        return True
    if isinstance(value, (int, float)):
        return finite_number(value)
    if isinstance(value, (list, tuple)):
        return all(_json_able(v, depth + 1) for v in value)
    if isinstance(value, Mapping):
        return all(
            isinstance(k, str) and bool(k) and len(k) <= MAX_HEADER_NAME and _json_able(v, depth + 1)
            for k, v in value.items()
        )
    return False


def check_advertise(
    advertise: Any, *, sections: Mapping[str, str] | None = None, phase: str = "initiate"
) -> dict[str, Any]:
    """Validate capability declarations. Fail closed: unknown sections, wrong shapes, oversize.

    ``sections`` maps each section name the adapter supports to its shape: ``"map"`` (a mapping) or ``"list"`` (a list
    of strings).
    """
    if not isinstance(advertise, Mapping):
        raise invalid("advertise_not_mapping")
    if phase != "initiate":
        raise invalid("advertise_phase")
    supported = sections or {}
    out: dict[str, Any] = {}
    for name, value in advertise.items():
        if name not in supported:
            raise invalid("advertise_section_unsupported")
        if supported[name] == "list":
            if (
                not isinstance(value, (list, tuple))
                or len(value) > _MAX_ADVERTISE_LIST
                or not all(isinstance(v, str) and 0 < len(v) <= _MAX_ADVERTISE_ITEM for v in value)
            ):
                raise invalid("advertise_bad_list")
            out[name] = list(value)
        else:
            if not isinstance(value, Mapping) or not _json_able(value):
                raise invalid("advertise_bad_map")
            out[name] = dict(value)
    if len(json.dumps(out, default=str)) > _MAX_ADVERTISE_BYTES:
        raise invalid("advertise_too_large")
    return out


def check_payload(payload: Any, *, protocol: str, phase: str, text_protocols: frozenset[str] = frozenset()) -> str:
    """A message payload: text protocols only, bounded, on initiate, challenge or sign."""
    if protocol not in text_protocols:
        raise invalid("payload_unsupported")
    if phase not in ("initiate", "challenge", "sign"):
        raise invalid("payload_phase")
    if not isinstance(payload, str):
        raise invalid("payload_not_text")
    if len(payload) > MAX_PAYLOAD:
        raise invalid("payload_too_large")
    return payload


def check_evidence(
    evidence: Any,
    *,
    sections: Mapping[str, str] | None = None,
    text_protocols: frozenset[str] = frozenset(),
    phase: str = "initiate",
    protocol: str = "",
) -> Evidence:
    """Validate a scheme's output and return a clean, copied ``Evidence``. A key outside the contract is refused.

    ``phase`` is the phase of the call (``advertise`` is valid on ``initiate`` only), ``protocol`` the audience's,
    ``sections`` the ``advertise`` sections the adapter supports and ``text_protocols`` the protocols whose message
    text a scheme may replace with ``payload``.
    """
    if not isinstance(evidence, Mapping):
        raise invalid("evidence_not_mapping")
    for key in evidence:
        if key not in _EVIDENCE_KEYS:
            raise invalid("evidence_unknown_key")
    out: Evidence = {}
    if "headers" in evidence:
        out["headers"] = check_headers(evidence["headers"])
    if "meta" in evidence:
        out["meta"] = check_meta(evidence["meta"])
    if "valid_until" in evidence:
        if not finite_number(evidence["valid_until"]):
            raise invalid("valid_until_not_number")
        out["valid_until"] = float(evidence["valid_until"])
    if "reusable" in evidence:
        if not isinstance(evidence["reusable"], bool):
            raise invalid("reusable_not_bool")
        out["reusable"] = evidence["reusable"]
    if "scope" in evidence:
        if evidence["scope"] not in _SCOPES:
            raise invalid("scope_unknown")
        out["scope"] = evidence["scope"]
    if "advertise" in evidence:
        out["advertise"] = check_advertise(evidence["advertise"], sections=sections, phase=phase)
    if "payload" in evidence:
        out["payload"] = check_payload(
            evidence["payload"], protocol=protocol, phase=phase, text_protocols=text_protocols
        )
        if evidence.get("reusable"):
            raise invalid("payload_reusable")
    if "grant" in evidence:
        out["grant"] = check_grant(evidence["grant"])
    return out


def check_verified(verified: Any) -> Verified:
    """A ``Verified`` needs a non-empty subject, a claims mapping and a known source; ``payload``, ``actors``,
    ``grant`` and ``credential_keys`` are optional and checked. A key outside the contract is refused."""
    if not isinstance(verified, Mapping):
        raise invalid("verified_not_mapping")
    for key in verified:
        if key not in _VERIFIED_KEYS:
            raise invalid("verified_unknown_key")
    subject = verified.get("subject")
    claims = verified.get("claims")
    source = verified.get("source")
    if not isinstance(subject, str) or not subject:
        raise invalid("verified_no_subject")
    if not isinstance(claims, Mapping):
        raise invalid("verified_claims_not_mapping")
    if source not in VERIFIED_SOURCES:
        raise invalid("verified_unknown_source")
    out = Verified(subject=subject, claims=dict(claims), source=source)
    if "payload" in verified:
        payload = verified["payload"]
        if payload is not None and (not isinstance(payload, str) or len(payload) > MAX_PAYLOAD):
            raise invalid("verified_payload_bad")
        out["payload"] = payload
    if "actors" in verified:
        out["actors"] = check_chain(verified["actors"], reason="actors")
    if "grant" in verified:
        out["grant"] = check_grant(verified["grant"])
    if "credential_keys" in verified:
        out["credential_keys"] = check_credential_keys(verified["credential_keys"])
    return out


def check_public_view(view: Any) -> PublicView:
    """A ``PublicView`` holds only ``facts``, ``attestation`` and ``advertise``, each a mapping."""
    if not isinstance(view, Mapping):
        raise invalid("view_not_mapping")
    fields: dict[str, Any] = {}
    for key, value in view.items():
        if key not in _PUBLIC_VIEW_KEYS:
            raise invalid("view_unknown_key")
        if not isinstance(value, Mapping):
            raise invalid("view_field_not_mapping")
        fields[key] = dict(value)
    return cast(PublicView, fields)


def check_identity_info(info: Any) -> IdentityInfo:
    """One listed identity: a slug id and kind, an opaque subject, slug labels, a known status, an optional grant and
    parent. A key outside the contract is refused."""
    if not isinstance(info, Mapping):
        raise invalid("identity_not_mapping")
    for key in info:
        if key not in _INFO_KEYS:
            raise invalid("identity_unknown_key")
    for key in ("id", "kind"):
        if not isinstance(info.get(key), str) or not _SLUG.match(info[key]):
            raise invalid(f"identity_{key}_bad")
    subject = info.get("subject", "")
    if not isinstance(subject, str):
        raise invalid("identity_subject_bad")
    labels = info.get("labels", [])
    if not isinstance(labels, (list, tuple)) or len(labels) > MAX_CHAIN:
        raise invalid("identity_labels_bad")
    if not all(isinstance(label, str) and _SLUG.match(label) for label in labels):
        raise invalid("identity_label_bad")
    if info.get("status") not in IDENTITY_STATUSES:
        raise invalid("identity_status_bad")
    out = IdentityInfo(id=info["id"], kind=info["kind"], subject=subject, labels=list(labels), status=info["status"])
    if "grant" in info:
        out["grant"] = check_grant(info["grant"])
    if "parent" in info:
        if not isinstance(info["parent"], str) or not _SLUG.match(info["parent"]):
            raise invalid("identity_parent_bad")
        out["parent"] = info["parent"]
    return out


# --- challenges ---------------------------------------------------------------------------------


def check_challenge(challenge: Any) -> dict[str, Any]:
    """Reserved keys have their documented types; every other key is namespaced with ``/``."""
    if not isinstance(challenge, Mapping):
        raise invalid("challenge_not_mapping")
    for key, value in challenge.items():
        if not isinstance(key, str):
            raise invalid("challenge_key_not_string")
        if key in ("nonce", "audience", "realm"):
            ok = isinstance(value, str) and bool(value)
        elif key in ("requirements", "schemes"):
            ok = isinstance(value, list) and all(isinstance(v, str) for v in value)
        elif key == "expires_at":
            ok = finite_number(value)
        else:
            if "/" not in key:
                raise invalid("challenge_key_not_namespaced")
            ok = True
        if not ok:
            raise invalid("challenge_key_bad_type")
    return dict(challenge)


def check_wire(wire: Any) -> str | None:
    """``ChallengeRequired.wire`` (or ``peer["wire"]``): ``None`` or bounded text."""
    if wire is None:
        return None
    if not isinstance(wire, str):
        raise invalid("wire_not_text")
    if len(wire) > MAX_WIRE:
        raise invalid("wire_too_large")
    return wire


def check_answerable(peer: Any, target: str, *, native: bool = False) -> None:
    """A peer's challenge (``peer["challenge"]``, ``peer["wire"]``) is checked like a verifier's, and a holder answers
    it only for the audience it names: another one is ``refused:wrong_audience``.

    ``native`` is the challenge a peer carries inside the session itself (an MCP ``initialize`` result, an A2A
    auth-required task): its shape is the scheme's own, so its keys are not held to the contract's ``Challenge``
    grammar. It must still be a mapping, and a string ``audience`` in it must be the target.
    """
    peer = peer if isinstance(peer, Mapping) else {}
    challenge = peer.get("challenge") or {}
    if native:
        if not isinstance(challenge, Mapping):
            raise invalid("challenge_not_mapping")
    else:
        challenge = check_challenge(challenge)
    check_wire(peer.get("wire"))
    bound = challenge.get("audience")
    if isinstance(bound, str) and bound != target:
        raise IdentityError("refused", "wrong_audience")


def check_carry(carry: Any, *, keys: frozenset[str] = frozenset()) -> dict[str, str]:
    """``ChallengeRequired.carry``: the keys the adapter declares, short token values."""
    if not isinstance(carry, Mapping):
        raise invalid("carry_not_mapping")
    for key, value in carry.items():
        if key not in keys:
            raise invalid("carry_unknown_key")
        if not isinstance(value, str) or not TOKEN.match(value) or len(value) > MAX_HEADER_NAME:
            raise invalid("carry_bad_value")
    return dict(carry)


# --- grants and delegation -------------------------------------------------------------------------


def check_grant(grant: Any) -> Grant:
    """Validate a :class:`~mas.library.standard.lib.identity.contract.Grant`. An unknown axis is refused."""
    if not isinstance(grant, Mapping):
        raise invalid("grant_not_mapping")
    for axis in grant:
        if axis not in GRANT_AXES:
            raise invalid("grant_unknown_axis")
    out: Grant = {}
    if "scopes" in grant:
        scopes = grant["scopes"]
        if not isinstance(scopes, (list, tuple)) or len(scopes) > MAX_GRANT_SCOPES:
            raise invalid("grant_scopes_bad")
        for scope in scopes:
            if not _is_ref(scope):
                raise invalid("grant_scope_bad")
        out["scopes"] = list(scopes)
    if "audience" in grant:
        if not _is_ref(grant["audience"]):
            raise invalid("grant_audience_bad")
        out["audience"] = grant["audience"]
    if "valid_until" in grant:
        if not finite_number(grant["valid_until"]):
            raise invalid("grant_valid_until_bad")
        out["valid_until"] = float(grant["valid_until"])
    if "depth" in grant:
        depth = grant["depth"]
        if isinstance(depth, bool) or not isinstance(depth, int) or not 0 <= depth <= MAX_DELEGATION_DEPTH:
            raise invalid("grant_depth_bad")
        out["depth"] = depth
    return out


def delegation_widens() -> IdentityError:
    """``refused:delegation_widens``: a child's authority is not inside its parent's."""
    return IdentityError("refused", "delegation_widens")


def needs_parent_evidence() -> IdentityError:
    """``unsatisfiable:needs_parent_evidence``: the scheme was asked to derive from a parent it was not given."""
    return IdentityError("unsatisfiable", "needs_parent_evidence")


def check_narrowing(parent: Any, child: Any, *, target: str = "") -> None:
    """Raise unless ``child`` is inside ``parent``: a delegation only narrows.

    Both grants are validated first (``invalid`` on a bad one). A child that omits an axis its parent declares is a
    widening: absent means unknown, not everything. An axis the parent leaves out is not compared.

    * ``scopes``: the child's are a subset of the parent's.
    * ``valid_until``: no later than the parent's.
    * ``depth``: exactly the parent's plus one (one hop), and never beyond
      :data:`~mas.library.standard.lib.identity.contract.MAX_DELEGATION_DEPTH`. A delegated grant never carries
      ``depth`` 0, which claims an original credential.
    * ``audience``: with a ``target`` (the request's audience) the child's equals it (``refused:wrong_audience``): an
      exchange may re-target a credential to the audience it is asked for, never to another. Without a ``target`` a
      child audience must equal the parent's, the only case provably not wider.
    """
    up = check_grant(parent)
    past = child.get("depth") if isinstance(child, Mapping) else None
    if isinstance(past, int) and not isinstance(past, bool) and past > MAX_DELEGATION_DEPTH:
        raise delegation_widens()  # a child past the cap is a widening, whatever the parent says
    down = check_grant(child)
    if "depth" in down and down["depth"] < 1:
        raise delegation_widens()  # a delegation is at least one hop deep
    if "scopes" in up and ("scopes" not in down or not set(down["scopes"]) <= set(up["scopes"])):
        raise delegation_widens()
    if "valid_until" in up and ("valid_until" not in down or down["valid_until"] > up["valid_until"]):
        raise delegation_widens()
    if "depth" in up and ("depth" not in down or down["depth"] != up["depth"] + 1):
        raise delegation_widens()
    if "audience" in up and "audience" not in down:
        raise delegation_widens()
    if "audience" in down:
        if target and down["audience"] != target:
            raise IdentityError("refused", "wrong_audience")
        if not target and "audience" in up and down["audience"] != up["audience"]:
            raise delegation_widens()


def _is_ref(value: Any) -> bool:
    """One opaque reference: non-empty text, no whitespace or control characters, bounded."""
    return (
        isinstance(value, str)
        and bool(value)
        and len(value) <= MAX_SCOPE
        and not any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in value)
    )


def check_chain(chain: Any, *, reason: str = "chain") -> list[str]:
    """A delegation chain (``Verified.actors``, ``Principal.chain``): opaque ids, bounded."""
    if not isinstance(chain, (list, tuple)) or len(chain) > MAX_CHAIN or not all(_is_ref(item) for item in chain):
        raise invalid(f"{reason}_bad")
    return list(chain)


def check_credential_keys(keys: Any) -> list[str]:
    """The NAMES of the headers and ``meta`` keys that carried a credential: tokens or namespaced keys, never values."""
    if not isinstance(keys, (list, tuple)) or len(keys) > MAX_CHAIN:
        raise invalid("credential_keys_bad")
    out: list[str] = []
    for key in keys:
        if not isinstance(key, str) or not key or len(key) > MAX_HEADER_NAME:
            raise invalid("credential_key_bad")
        if "/" in key:
            if key.startswith("/") or key.endswith("/") or any(ch.isspace() or ord(ch) < 32 for ch in key):
                raise invalid("credential_key_bad")
        elif not TOKEN.match(key):
            raise invalid("credential_key_bad")
        out.append(key)
    return out
