#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The Identity infra rows bound to the identity layers that name them: records inlined, errors named, nothing else."""

from __future__ import annotations

import sys
from typing import Any

import pytest
from mas.ctl.compose.models import ResolvedInfra
from mas.ctl.infra.bind import BindError, bind_flavour
from mas.ctl.infra.identity import IdentityBindError, inline_identities, iter_uses, uses_identity
from mas.ctl.validate.separation import check_separation
from mas.runtime.secrets import SecretRef

KEY = SecretRef(name="desk-issuer-key")
ROWS = {
    "desk-badge": {"id": "desk-badge", "scheme": "signed_badge", "params": {"key": KEY, "subject": "desk"}},
    "ops-badge": {"id": "ops-badge", "scheme": "signed_badge", "params": {"key": KEY, "subject": "ops"}},
}


def step(identity: str | None = "{agent}-badge", **params: Any) -> dict[str, Any]:
    named = {"identity": identity} if identity is not None else {}
    return {"layer": "identity", "for": ["llm"], "per": "destination", "priority": 10, "params": {**named, **params}}


def test_a_session_without_an_identity_layer_is_returned_untouched() -> None:
    """Kills: an inlining that rebuilds (or imports the identity library for) a session that has no identity."""
    resolved = ResolvedInfra(llm_stack=[{"layer": "retry", "params": {}}], identities=dict(ROWS))
    assert not uses_identity(resolved)
    assert inline_identities(resolved) is resolved


def test_a_per_agent_stack_step_gets_the_record_of_its_agent() -> None:
    """Kills: {agent} left in the record id; the scheme and params missing; the agent id missing."""
    resolved = ResolvedInfra(agent_llm_pipelines={"desk": [step()], "ops": [step()]}, identities=dict(ROWS))
    bound = inline_identities(resolved)
    assert bound is not None
    desk = bound.agent_llm_pipelines["desk"][0]["params"]
    ops = bound.agent_llm_pipelines["ops"][0]["params"]
    assert (desk["identity"], desk["scheme"], desk["agent_id"]) == ("desk-badge", "signed_badge", "desk")
    assert (ops["identity"], ops["agent_id"], ops["params"]["subject"]) == ("ops-badge", "ops", "ops")
    assert [use.record for use in iter_uses(bound)] == ["desk-badge", "ops-badge"]


def test_a_secret_stays_a_reference_and_the_row_is_not_shared() -> None:
    """Kills: a revealed value in a row; two sessions sharing one params dict."""
    resolved = ResolvedInfra(agent_llm_pipelines={"desk": [step()]}, identities=dict(ROWS))
    bound = inline_identities(resolved)
    assert bound is not None
    params = bound.agent_llm_pipelines["desk"][0]["params"]["params"]
    assert isinstance(params["key"], SecretRef)
    params["subject"] = "changed"
    assert ROWS["desk-badge"]["params"]["subject"] == "desk"


def test_a_chain_row_is_inlined_with_its_agent() -> None:
    """Kills: an identity row of an agent chain that is skipped."""
    chain = [
        {"row": "link", "type": "http", "params": {}},
        {"row": "id", "type": "identity", "params": {"identity": "ops-badge"}},
    ]
    bound = inline_identities(ResolvedInfra(agent_chains={"ops": chain}, identities=dict(ROWS)))
    assert bound is not None
    params = bound.agent_chains["ops"][1]["params"]
    assert (params["scheme"], params["agent_id"]) == ("signed_badge", "ops")
    assert "scheme" not in bound.agent_chains["ops"][0]["params"]


def test_a_stack_row_shared_by_every_agent_keeps_its_template_until_it_is_bound() -> None:
    """Kills: a shared Stack row that fails because {agent} cannot be filled yet."""
    resolved = ResolvedInfra(stacks={"s": [step()]}, identities=dict(ROWS))
    bound = inline_identities(resolved)
    assert bound is not None
    assert bound.stacks["s"][0]["params"]["identity"] == "{agent}-badge"
    assert "scheme" not in bound.stacks["s"][0]["params"]


def test_a_tool_server_row_with_an_identity_step_gets_its_record_and_is_listed_as_a_use() -> None:
    """Kills: a tool server whose own pipeline step never gets its record (the layer then has no scheme)."""
    resolved = ResolvedInfra(
        tool_server_registry={"fares": {"protocol": "mcp", "pipeline": [step("desk-badge")]}},
        identities=dict(ROWS),
    )
    bound = inline_identities(resolved)
    assert bound is not None
    assert bound.tool_server_registry["fares"]["pipeline"][0]["params"]["scheme"] == "signed_badge"
    uses = list(iter_uses(bound))
    assert [(use.record, use.agent) for use in uses] == [("desk-badge", "")]
    assert "fares" in uses[0].where


def test_a_step_written_in_place_is_left_as_it_is() -> None:
    """Kills: a record written on the layer that is looked up and refused."""
    own = step("x", scheme="none", params={})
    bound = inline_identities(ResolvedInfra(agent_llm_pipelines={"desk": [own]}, identities={}))
    assert bound is not None
    assert bound.agent_llm_pipelines["desk"][0]["params"]["scheme"] == "none"


def test_a_dangling_record_names_the_step_and_the_known_records() -> None:
    """Kills: a missing record that fails late, at the first call, with no hint."""
    resolved = ResolvedInfra(agent_llm_pipelines={"desk": [step("no-such-badge")]}, identities=dict(ROWS))
    with pytest.raises(IdentityBindError) as err:
        inline_identities(resolved)
    text = str(err.value)
    assert "no-such-badge" in text and "bind.agents.desk.llm" in text and "desk-badge" in text


def test_a_step_that_names_no_record_is_refused() -> None:
    """Kills: an identity layer with nothing to present."""
    with pytest.raises(IdentityBindError, match="names no record"):
        inline_identities(ResolvedInfra(agent_llm_pipelines={"desk": [step(None)]}, identities=dict(ROWS)))


def test_a_flavour_binds_identity_through_an_llm_stack_and_a_dangling_record_is_a_bind_error() -> None:
    """Kills: identity inlined before the Flavour binds (a stack row never filled), or a bind that hides the error."""
    resolved = ResolvedInfra(stacks={"id": [step()]}, identities=dict(ROWS))
    flavour = {"bind": {"agents": {"desk": {"llm": {"stack": "id"}}}}}
    bound = bind_flavour(resolved, flavour, agent_ids=["desk"])
    assert bound.agent_llm_pipelines["desk"][0]["params"]["identity"] == "desk-badge"
    with pytest.raises(BindError, match="no-such-badge"):
        bind_flavour(
            ResolvedInfra(stacks={"id": [step("no-such-badge")]}, identities=dict(ROWS)), flavour, agent_ids=["desk"]
        )


@pytest.mark.parametrize("kind", ["agent", "mas"])
@pytest.mark.parametrize("key", ["identity", "identities", "credential", "credentials"])
def test_an_agent_or_a_mas_never_names_an_identity(kind: str, key: str) -> None:
    """Kills: an identity or a credential written in the logic, where infra owns it."""
    problems = check_separation({"spec": {key: "x"}}, kind)
    assert problems == [f"spec.{key} is an identity concern — declare an Identity infra row and bind it in the Flavour"]
    assert check_separation({"spec": {key: []}}, kind) == []  # an empty value says nothing


def test_importing_the_cli_and_resolving_infra_without_identity_loads_no_identity_code() -> None:
    """Kills: an identity module imported at startup, so 'no identity row' still pays for it."""
    import subprocess

    code = (
        "import sys; import mas.ctl.cli.main; from mas.ctl.infra.bind import bind_flavour;"
        "from mas.ctl.compose.models import ResolvedInfra;"
        "bind_flavour(ResolvedInfra(), {}, agent_ids=['a']);"
        "prefixes=('mas.library.standard.lib.identity','mas.library.standard.plugins.identity');"
        "bad=[m for m in sys.modules if m.startswith(prefixes)];"
        "print(bad); sys.exit(1 if bad else 0)"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stdout + done.stderr


def test_each_bind_gets_its_own_scope_token_and_a_rebind_keeps_it() -> None:
    """Kills: two runs of one MAS sharing a wallet scope; a second bind of one run that mints a new token."""
    make = lambda: ResolvedInfra(agent_llm_pipelines={"desk": [step()]}, identities=dict(ROWS))  # noqa: E731
    first, second = inline_identities(make()), inline_identities(make())
    assert first is not None and second is not None
    assert first.identity_scope and first.identity_scope != second.identity_scope
    assert first.agent_llm_pipelines["desk"][0]["params"]["holder_scope"] == first.identity_scope
    again = inline_identities(first)
    assert again is not None and again.identity_scope == first.identity_scope


def test_check_sees_the_changing_tools_and_the_guard_that_covers_them() -> None:
    """Kills: a check that stays silent about an agent whose identity_delegate/identity_revoke can never run."""
    from mas.ctl.infra.identity_check import _uses_changing_tools, gate_configs

    assert _uses_changing_tools({"system_plugins": [{"identity": {}}]})
    assert _uses_changing_tools({"system_plugins": [{"identity": {"tools": ["identity_revoke"]}}]})
    assert not _uses_changing_tools({"system_plugins": [{"identity": {"tools": ["identity_list"]}}]})
    assert not _uses_changing_tools({"system_plugins": [{"identity": {"enabled": False}}]})
    assert not _uses_changing_tools({})
    assert gate_configs(["identity-guard", {"identity-gate": {}}], guard=True) == [{}]
    assert gate_configs(["identity-guard"], guard=False) == []


def test_describe_shows_facts_only_and_never_a_schemes_own_words(monkeypatch: pytest.MonkeyPatch) -> None:
    """Kills: a describe command that prints the attestation or the text of a scheme that fails to build."""
    from mas.ctl.infra import identity_check
    from mas.library.standard.lib.identity.contract import CAP_DESCRIBE, IdentityInfo, PublicView
    from mas.library.standard.plugins.identity import layer as layer_module

    class Described:
        scheme = "described"
        capabilities = frozenset({CAP_DESCRIBE})
        PARAMS_SCHEMA: dict[str, Any] = {"type": "object"}
        breaks = False

        @classmethod
        def from_spec(cls, params: Any) -> Any:
            if cls.breaks:
                raise RuntimeError("the key is s3cr3t-in-a-message")
            return cls()

        def identities(self) -> list[IdentityInfo]:
            return [IdentityInfo(id="badge", kind="agent", subject="", labels=[], status="active")]

        def select(self, req: Any) -> str:
            return "badge"

        async def describe(self, ref: str) -> PublicView:
            return PublicView(facts={"group": "ops"}, attestation={"token": "ATTEST-VALUE"}, advertise={"c": "ADV"})

    monkeypatch.setattr(layer_module, "lookup_scheme", lambda name, extra=None: Described)
    resolved = ResolvedInfra(identities={"r": {"id": "r", "scheme": "described", "params": {}}})
    shown = identity_check.describe(resolved, "r")
    assert shown["identities"][0]["view"] == {"facts": {"group": "ops"}}
    Described.breaks = True
    with pytest.raises(ValueError) as failed:
        identity_check.describe(resolved, "r")
    assert "s3cr3t" not in str(failed.value) and "RuntimeError" in str(failed.value)


class _Stop(Exception):
    pass


@pytest.mark.parametrize("scope", ["run-token", ""])
def test_every_agent_build_carries_the_bind_scope_in_its_spec(monkeypatch: pytest.MonkeyPatch, scope: str) -> None:
    """Kills: a scope only the MAS backend stamps, so a lab or chat build gates on no wallet (`no_identity`)."""
    from mas.ctl.compose.models import ResolvedInfra
    from mas.ctl.session.bootstrap import InstantiationOptions, instantiate_runtime

    seen: dict[str, object] = {}

    def spy(spec: dict, **_kw: object) -> None:
        seen.update(spec)
        raise _Stop

    monkeypatch.setattr("mas.runtime.spec.parser.parse_agent_spec", spy)
    agent = {
        "apiVersion": "mas/v1",
        "kind": "Agent",
        "metadata": {"name": "solo"},
        "spec": {"models": [{"model": "m"}]},
    }
    options = InstantiationOptions(
        agent_manifest=agent, resolved_infra=ResolvedInfra(identity_scope=scope), validate_manifests=False
    )
    with pytest.raises(_Stop):
        instantiate_runtime(options)
    assert seen.get("identity_scope", "") == scope


def test_a_listen_row_that_names_a_verifier_the_host_cannot_enforce_is_refused() -> None:
    """Kills: an A2A listener started from a row whose identity step is never put in front of the handler."""
    from mas.ctl.session.exposure import _refuse_unenforced_pipeline

    _refuse_unenforced_pipeline({"protocol": "a2a", "url": "http://127.0.0.1:1"})
    _refuse_unenforced_pipeline({"protocol": "a2a", "pipeline": [{"layer": "retry", "params": {}}]})
    with pytest.raises(ValueError, match="'identity'.*unverified"):
        _refuse_unenforced_pipeline({"protocol": "a2a", "url": "u", "pipeline": [{"layer": "identity", "params": {}}]})
