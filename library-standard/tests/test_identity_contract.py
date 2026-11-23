#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The identity contract (types, errors, interfaces) and the rules a host applies to what a scheme returns."""

from __future__ import annotations

import pickle

import pytest
from mas.library.standard.lib.identity import contract
from mas.library.standard.lib.identity.contract import (
    CAP_DELEGATE,
    CAPABILITIES,
    CAPABILITY_INTERFACES,
    ChallengeRequired,
    Delegator,
    IdentityError,
    InteractionRequired,
)
from mas.library.standard.lib.identity.rules import (
    check_advertise,
    check_carry,
    check_challenge,
    check_credential_keys,
    check_evidence,
    check_grant,
    check_headers,
    check_identity_info,
    check_meta,
    check_narrowing,
    check_params_schema,
    check_payload,
    check_public_view,
    check_verified,
    check_wire,
    validate,
)
from mas.runtime.secrets import SecretRef

SECTIONS = {"x.map": "map", "x.list": "list"}


def refused(call, *args, **kwargs) -> str:
    with pytest.raises(IdentityError) as caught:
        call(*args, **kwargs)
    return f"{caught.value.code}:{caught.value.reason}"


def test_the_contract_module_holds_no_function_but_its_error_classes() -> None:
    """Types, Protocols, constants and exception classes only: the one helper is the slug check of an error reason."""
    functions = [
        name
        for name, value in vars(contract).items()
        if callable(value)
        and getattr(value, "__module__", "") == contract.__name__
        and not isinstance(value, type)
        and not name.startswith("__")
    ]
    assert functions == ["_is_slug"]


def test_every_capability_with_an_interface_names_it() -> None:
    assert set(CAPABILITY_INTERFACES) == CAPABILITIES - {"sign"}
    assert CAPABILITY_INTERFACES[CAP_DELEGATE] is Delegator


def test_errors_are_value_free_and_survive_a_pickle() -> None:
    error = IdentityError("refused", "scope_uncovered:read")
    assert str(error) == "refused:scope_uncovered:read"
    assert pickle.loads(pickle.dumps(error)).reason == "scope_uncovered:read"
    with pytest.raises(ValueError):
        IdentityError("nope")
    with pytest.raises(ValueError):
        IdentityError("refused", "has spaces")
    asked = InteractionRequired("h1", "go to the page")
    assert str(asked) == "no_credential" and pickle.loads(pickle.dumps(asked)).hint == "go to the page"
    challenge = ChallengeRequired({"nonce": "n"}, carry={"http.auth_scheme": "Bearer"}, wire="W")
    assert str(challenge) == "challenge_required" and pickle.loads(pickle.dumps(challenge)).wire == "W"


# --- params ---------------------------------------------------------------------------------------

SCHEMA = {
    "type": "object",
    "required": ["token", "name"],
    "additionalProperties": False,
    "properties": {"token": {"x-secret": True}, "name": {"type": "string"}},
}


def test_a_secret_param_is_a_secret_ref_and_nothing_else() -> None:
    ref = SecretRef(name="tok")
    assert validate({"token": ref, "name": "n"}, SCHEMA) == []
    assert validate({"token": "plain-text", "name": "n"}, SCHEMA) == ["$.token: secret_ref"]
    assert validate({"token": {"v": 1}, "name": "n"}, SCHEMA) == ["$.token: secret_ref"]
    assert validate({"name": "n"}, SCHEMA) == ["$: required"]
    assert validate({"token": ref, "name": 3}, SCHEMA) == ["$.name: type"]
    assert validate({"token": ref, "name": "n", "x": 1}, SCHEMA) == ["$: additionalProperties"]


def test_a_bad_params_schema_is_refused_without_a_value() -> None:
    check_params_schema(SCHEMA)
    assert refused(check_params_schema, {"type": "object", "required": 5}) == "invalid:params_schema"


# --- headers, meta, evidence ----------------------------------------------------------------------


def test_headers_are_lowercased_and_capped() -> None:
    assert check_headers({"X-Api": "v"}) == {"x-api": "v"}
    assert check_headers(None) == {}
    assert refused(check_headers, {"Host": "x"}) == "invalid:header_denied"
    assert refused(check_headers, {"bad name": "x"}) == "invalid:header_name_not_token"
    assert refused(check_headers, {"x": "a\nb"}) == "invalid:header_control_char"
    assert refused(check_headers, {"x": "a" * 5000}) == "invalid:header_too_long"
    assert refused(check_headers, {f"x{i}": "v" for i in range(40)}) == "invalid:headers_too_many"
    assert refused(check_headers, {"x": 1}) == "invalid:headers_not_strings"


def test_meta_keys_are_namespaced() -> None:
    assert check_meta({"a/b": 1}) == {"a/b": 1}
    assert refused(check_meta, {"plain": 1}) == "invalid:meta_key_not_namespaced"


def test_advertise_sections_belong_to_the_adapter() -> None:
    assert check_advertise({"x.map": {"k": "v"}, "x.list": ["a"]}, sections=SECTIONS) == {
        "x.map": {"k": "v"},
        "x.list": ["a"],
    }
    assert refused(check_advertise, {"x.map": {}}) == "invalid:advertise_section_unsupported"
    assert refused(check_advertise, {"x.list": "a"}, sections=SECTIONS) == "invalid:advertise_bad_list"
    assert refused(check_advertise, {"x.map": []}, sections=SECTIONS) == "invalid:advertise_bad_map"
    assert refused(check_advertise, {"x.map": {}}, sections=SECTIONS, phase="sign") == "invalid:advertise_phase"


def test_a_payload_needs_a_text_protocol_the_adapter_declares() -> None:
    assert check_payload("t", protocol="a2a", phase="initiate", text_protocols=frozenset({"a2a"})) == "t"
    assert refused(check_payload, "t", protocol="mcp", phase="initiate") == "invalid:payload_unsupported"
    assert (
        refused(check_payload, "t", protocol="a2a", phase="x", text_protocols=frozenset({"a2a"}))
        == "invalid:payload_phase"
    )


def test_evidence_is_cleaned_and_unknown_keys_refused() -> None:
    out = check_evidence(
        {"headers": {"A": "b"}, "meta": {"a/b": 1}, "valid_until": 5, "reusable": True, "scope": "audience"}
    )
    assert out["headers"] == {"a": "b"} and out["valid_until"] == 5.0 and out["scope"] == "audience"
    assert refused(check_evidence, {"nope": 1}) == "invalid:evidence_unknown_key"
    assert refused(check_evidence, {"reusable": "yes"}) == "invalid:reusable_not_bool"
    assert refused(check_evidence, {"scope": "other"}) == "invalid:scope_unknown"
    assert refused(check_evidence, {"valid_until": float("nan")}) == "invalid:valid_until_not_number"
    assert (
        refused(check_evidence, {"payload": "t", "reusable": True}, protocol="a2a", text_protocols=frozenset({"a2a"}))
        == "invalid:payload_reusable"
    )


def test_verified_and_public_view_are_checked() -> None:
    ok = {"subject": "u", "claims": {}, "source": "token", "actors": ["a"], "credential_keys": ["authorization"]}
    assert check_verified(ok)["actors"] == ["a"]
    assert refused(check_verified, {"claims": {}, "source": "token"}) == "invalid:verified_no_subject"
    assert refused(check_verified, {"subject": "u", "claims": {}, "source": "x"}) == "invalid:verified_unknown_source"
    assert refused(check_verified, {**ok, "extra": 1}) == "invalid:verified_unknown_key"
    assert refused(check_verified, {**ok, "actors": ["a b"]}) == "invalid:actors_bad"
    assert check_public_view({"facts": {"a": 1}}) == {"facts": {"a": 1}}
    assert refused(check_public_view, {"facts": 1}) == "invalid:view_field_not_mapping"
    assert refused(check_public_view, {"other": {}}) == "invalid:view_unknown_key"
    assert check_credential_keys(["authorization", "x/token"]) == ["authorization", "x/token"]
    assert refused(check_credential_keys, ["bad key"]) == "invalid:credential_key_bad"


def test_a_listed_identity_is_value_free_and_well_formed() -> None:
    info = {"id": "main", "kind": "agent", "subject": "user:1", "labels": ["a"], "status": "active"}
    assert check_identity_info(info)["id"] == "main"
    assert check_identity_info({**info, "parent": "root", "grant": {"scopes": ["read"]}})["parent"] == "root"
    assert refused(check_identity_info, {**info, "id": "Bad Id"}) == "invalid:identity_id_bad"
    assert refused(check_identity_info, {**info, "status": "maybe"}) == "invalid:identity_status_bad"
    assert refused(check_identity_info, {**info, "labels": ["a b"]}) == "invalid:identity_label_bad"
    assert refused(check_identity_info, {**info, "extra": 1}) == "invalid:identity_unknown_key"
    assert refused(check_identity_info, {**info, "grant": {"nope": 1}}) == "invalid:grant_unknown_axis"


# --- challenges -----------------------------------------------------------------------------------


def test_challenge_keys_are_typed_or_namespaced() -> None:
    assert check_challenge({"nonce": "n", "x/hint": 1})["nonce"] == "n"
    assert refused(check_challenge, {"nonce": ""}) == "invalid:challenge_key_bad_type"
    assert refused(check_challenge, {"hint": 1}) == "invalid:challenge_key_not_namespaced"
    assert refused(check_challenge, {"expires_at": "soon"}) == "invalid:challenge_key_bad_type"
    assert check_wire(None) is None and check_wire("w") == "w"
    assert refused(check_wire, "w" * 9000) == "invalid:wire_too_large"
    assert check_carry({"k": "Bearer"}, keys=frozenset({"k"})) == {"k": "Bearer"}
    assert refused(check_carry, {"k": "Bearer"}) == "invalid:carry_unknown_key"
    assert refused(check_carry, {"k": "a b"}, keys=frozenset({"k"})) == "invalid:carry_bad_value"


# --- grants ---------------------------------------------------------------------------------------

PARENT = {"scopes": ["read", "write"], "audience": "https://a.example", "valid_until": 100.0, "depth": 0}


def test_a_grant_has_four_axes_and_bounded_values() -> None:
    assert check_grant({"scopes": ["read"], "depth": 1})["depth"] == 1
    assert refused(check_grant, {"nope": 1}) == "invalid:grant_unknown_axis"
    assert refused(check_grant, {"scopes": ["a b"]}) == "invalid:grant_scope_bad"
    assert refused(check_grant, {"depth": 9}) == "invalid:grant_depth_bad"
    assert refused(check_grant, {"valid_until": "x"}) == "invalid:grant_valid_until_bad"


def test_a_delegation_only_narrows() -> None:
    child = {"scopes": ["read"], "audience": "https://a.example", "valid_until": 50.0, "depth": 1}
    check_narrowing(PARENT, child)
    check_narrowing(PARENT, child, target="https://a.example")
    assert refused(check_narrowing, PARENT, {**child, "scopes": ["read", "admin"]}) == "refused:delegation_widens"
    assert refused(check_narrowing, PARENT, {**child, "valid_until": 200.0}) == "refused:delegation_widens"
    assert refused(check_narrowing, PARENT, {**child, "depth": 0}) == "refused:delegation_widens"
    assert refused(check_narrowing, PARENT, {**child, "depth": 3}) == "refused:delegation_widens"
    assert refused(check_narrowing, PARENT, {"scopes": ["read"]}) == "refused:delegation_widens"
    assert refused(check_narrowing, PARENT, child, target="https://b.example") == "refused:wrong_audience"
    assert refused(check_narrowing, PARENT, {**child, "depth": 9}) == "refused:delegation_widens"
    check_narrowing({}, {"scopes": ["anything"], "depth": 1})  # an unknown parent grant leaves only the shape
