#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""HITL responders get their spec config from ctl; a run with no operator refuses a hold."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from mas.ctl.session import hitl_config
from mas.ctl.session.hitl_config import resolve_hitl_from_manifest
from mas.runtime.schema.egress import EmitHitlRequest
from mas.runtime.schema.hitl import HitlResolveChoice
from mas.runtime.spec.gov import SpecBindingError, parse_gov_spec


class _Configured:
    built: list[dict] = []

    def __init__(self, token: str = "", operator_id: str = "x") -> None:
        type(self).built.append({"token": token, "operator_id": operator_id})

    def resolve(self, request):  # pragma: no cover - not called
        raise NotImplementedError


def _fake_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    variant = SimpleNamespace(load_class=lambda: _Configured)
    registry = SimpleNamespace(
        resolve_by_type=lambda kind, key: variant if key == "custom" else None,
        list_names=lambda kind: ["custom"],
    )
    monkeypatch.setattr(hitl_config, "get_registry", lambda: registry)
    _Configured.built = []


def _agent(**stanza) -> dict:
    return {
        "apiVersion": "mas/v1",
        "kind": "Agent",
        "metadata": {"name": "a"},
        "spec": {"governance": [{"gate": stanza}]},
    }


def test_responder_gets_its_config_as_secret_refs_not_revealed_values(monkeypatch: pytest.MonkeyPatch) -> None:
    from mas.runtime.secrets import SecretRef
    from secret_fakes import bound_secrets

    _fake_registry(monkeypatch)
    agent = _agent(hitl_on_tool=True, hitl_mode="custom", hitl_responder={"token": {"secret": "hitl-token"}})
    with bound_secrets({"hitl-token": "tok-from-store"}):
        responder, _ = resolve_hitl_from_manifest(agent, session_interactive=True)
    assert isinstance(responder, _Configured)
    built = _Configured.built[0]
    assert isinstance(built["token"], SecretRef)
    assert built["token"].name == "hitl-token"
    assert built["operator_id"] == "x"
    assert "tok-from-store" not in str(built)


def test_absent_config_builds_the_responder_with_no_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    # Kills: passing an empty mapping or a default config where today there is none.
    _fake_registry(monkeypatch)
    responder, _ = resolve_hitl_from_manifest(_agent(hitl_on_tool=True, hitl_mode="custom"), session_interactive=True)
    assert isinstance(responder, _Configured)
    assert _Configured.built == [{"token": "", "operator_id": "x"}]


def test_a_param_the_responder_does_not_accept_is_an_error_naming_the_key_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Kills: silently dropping a misspelt key (a security setting would then be ignored).
    _fake_registry(monkeypatch)
    agent = _agent(hitl_on_tool=True, hitl_mode="custom", hitl_responder={"tokn": "s3cret"})
    with pytest.raises(ValueError, match="tokn") as err:
        resolve_hitl_from_manifest(agent, session_interactive=True)
    assert "s3cret" not in str(err.value)


def test_hitl_responder_must_be_a_mapping() -> None:
    # Kills: accepting a scalar and failing later in the responder.
    with pytest.raises(SpecBindingError, match="mapping"):
        parse_gov_spec([{"gate": {"hitl_responder": "x"}}])
    assert parse_gov_spec([{"gate": {}}]).hitl_responder is None


def _request() -> EmitHitlRequest:
    return EmitHitlRequest(
        request_id=1,
        pending_schedule="TOOL_CALL",
        offered_actions=[HitlResolveChoice.ALLOW, HitlResolveChoice.BLOCK, HitlResolveChoice.SKIP],
    )


@pytest.mark.parametrize("flags", [{"hitl_on_tool": True}, {}])
def test_a_plugin_hold_in_a_run_with_no_operator_is_blocked(flags: dict) -> None:
    # Kills: the old fallback that approved the hold, and returning no responder (the run would stall).
    responder, _ = resolve_hitl_from_manifest(_agent(**flags), session_interactive=False)
    assert responder is not None
    assert responder.resolve(_request()).resolution == HitlResolveChoice.BLOCK


def test_only_an_explicit_auto_approve_approves_without_an_operator() -> None:
    # Kills: making every non-interactive run deny, including the explicit mode.
    agent = _agent(hitl_on_tool=True, hitl_mode="auto-approve")
    responder, _ = resolve_hitl_from_manifest(agent, session_interactive=False)
    assert responder is not None
    assert responder.resolve(_request()).resolution == HitlResolveChoice.ALLOW


def test_an_interactive_session_still_has_no_in_process_responder() -> None:
    # Kills: wiring auto-deny into interactive sessions, which would answer before the operator.
    assert resolve_hitl_from_manifest(_agent(hitl_on_tool=True), session_interactive=True) == (None, None)


def test_an_agent_with_no_governance_has_no_responder() -> None:
    agent = {"apiVersion": "mas/v1", "kind": "Agent", "metadata": {"name": "a"}, "spec": {}}
    assert resolve_hitl_from_manifest(agent, session_interactive=False) == (None, None)
