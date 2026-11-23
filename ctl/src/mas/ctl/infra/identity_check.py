#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``mas-ctl identity check | list | describe``: the functions behind the commands. Nothing here prints a secret.

A record is read the way a session reads it: the Flavour bound the infra (``bind_session``), so each identity layer
step carries its record's scheme and params. Nothing here imports the identity library unless the session uses it.
"""

from __future__ import annotations

import asyncio
from typing import Any

from mas.ctl.compose.models import ResolvedInfra
from mas.ctl.infra.identity import Use, iter_uses
from mas.ctl.session.manifest_config import spec_with_control_lists
from mas.runtime.secrets import current_resolver
from mas.runtime.secrets.errors import SecretError
from mas.runtime.secrets.ref import iter_secret_refs


def _names_declaring(attribute: str) -> frozenset[str]:
    """Every name a governance entry may be called by: each registered plugin that declares ``attributes:
    {<attribute>: true}``, by URN and shortcut. A plugin another library ships is recognised without ctl naming it."""
    from mas.runtime.registry import get_registry

    names: set[str] = set()
    for entry in get_registry().entries_of_type("governance"):
        if entry.attributes.get(attribute) is True:
            names.add(entry.urn)
            names.update(entry.shortcuts)
    return frozenset(names)


def gate_names() -> frozenset[str]:
    """The names an identity gate goes by (``attributes: {identity_gate: true}``)."""
    return _names_declaring("identity_gate")


def gate_configs(governance: Any, *, guard: bool = False) -> list[dict[str, Any]]:
    """The config of each identity gate (or, with ``guard``, identity guard) in a governance list."""
    out: list[dict[str, Any]] = []
    names = _names_declaring("identity_guard" if guard else "identity_gate")
    for item in governance or []:
        if isinstance(item, str) and item in names:
            out.append({})
        elif isinstance(item, dict) and len(item) == 1 and next(iter(item)) in names:
            config = next(iter(item.values()))
            out.append(dict(config) if isinstance(config, dict) else {})
    return out


def _uses_changing_tools(spec: dict[str, Any]) -> bool:
    """Does the agent's spec grant the identity system plugin with ``identity_delegate`` or ``identity_revoke``?"""
    from mas.runtime.system_plugins.load import grants

    try:
        granted = grants(spec)
    except Exception:  # a spec the loader refuses is reported where it loads
        return False
    for grant in granted.values():
        if grant.entry.urn == "mas.system_plugin.identity" and grant.enabled:
            tools = grant.settings.get("tools") or ["identity_delegate", "identity_revoke"]
            return bool({"identity_delegate", "identity_revoke"} & set(tools))
    return False


def _uses(resolved: ResolvedInfra, agent: str) -> list[Use]:
    """The identity layer steps that apply to ``agent``: its own and the shared ones."""
    return [use for use in iter_uses(resolved) if use.agent in {"", agent}]


def _record_problems(use: Use) -> list[str]:
    from mas.library.standard.plugins.identity.layer import lookup_scheme

    params = use.step.get("params") if isinstance(use.step.get("params"), dict) else {}
    record, scheme = use.record, str(params.get("scheme") or "")
    if not scheme:
        return [f"{use.where}: record {record!r} has no scheme"]
    cls = lookup_scheme(scheme)
    if cls is None:
        return [f"record {record!r}: scheme {scheme!r} is not an installed identity_scheme plugin"]
    return _param_problems(record, cls, dict(params.get("params") or {}))


def _param_problems(record: str, cls: Any, params: dict[str, Any]) -> list[str]:
    """What is wrong with ``params`` for scheme class ``cls``: the schema, and each required secret not being set."""
    from mas.library.standard.lib.identity.rules import validate

    out = [f"record {record!r}: params {problem}" for problem in validate(params, cls.PARAMS_SCHEMA)]
    resolver = current_resolver()
    for ref in iter_secret_refs(params):
        if ref.optional:
            continue
        try:
            present = resolver.describe(ref).present
        except SecretError:
            present = False
        if not present:
            out.append(f"record {record!r}: secret {ref} is not set (mas-ctl secrets set {ref.name})")
    return out


def check(
    resolved: ResolvedInfra,
    flavour_spec: dict[str, Any] | None,
    agents: dict[str, dict[str, Any]],
    agent_ids: list[str],
    *,
    require_gate: bool = False,
) -> dict[str, Any]:
    """Preflight. ``ok`` is false when ``problems`` is not empty; ``warnings`` never fail the check.

    ``agents`` maps an agent id to its own spec (its governance list is what a Control that names none leaves in
    place). A record used by an agent: scheme installed, params valid, secrets set. A gate requirement: well formed,
    names a record the agent holds, and a scheme that has ``describe`` when it reads facts. ``require_gate`` makes a
    holder with no identity gate a problem; without it, a warning.
    """
    if not any(True for _ in iter_uses(resolved)):
        declared = sorted(resolved.identities or {})
        warnings = [f"record {rid!r} is declared but no identity layer uses it" for rid in declared]
        return {"ok": True, "problems": [], "warnings": warnings, "records": declared, "agents": {}}
    from mas.library.standard.lib.identity.requirements import GateConfigError, parse_requirements
    from mas.library.standard.plugins.identity.gate import EVENT_MODES
    from mas.library.standard.plugins.identity.layer import lookup_scheme

    problems: list[str] = []
    warnings: list[str] = []
    out_agents: dict[str, Any] = {}
    checked: set[str] = set()
    for use in iter_uses(resolved):
        key = f"{use.agent}/{use.record}"
        if key not in checked:
            checked.add(key)
            problems.extend(_record_problems(use))
    for agent in agent_ids:
        held = {use.record: use for use in _uses(resolved, agent)}
        spec = spec_with_control_lists(agents.get(agent) or {}, flavour_spec, agent_id=agent, entry_id=agent)
        configs = gate_configs(spec.get("governance"))
        out_agents[agent] = {"identities": sorted(held), "gate": bool(configs)}
        for config in configs:
            try:
                requirements = parse_requirements(config.get("requirements"))
                if config.get("events", "all") not in EVENT_MODES:
                    raise GateConfigError(f"events must be one of {', '.join(EVENT_MODES)}")
            except GateConfigError as exc:
                problems.append(f"agent {agent!r}: identity-gate: {exc}")
                continue
            for tool, requirement in requirements.items():
                for record in [requirement.identity] if requirement.identity else sorted(held):
                    use = held.get(record)
                    if use is None:
                        problems.append(
                            f"agent {agent!r}: identity-gate requirement {tool!r} names record {record!r}, "
                            "which no identity layer of this agent uses"
                        )
                        continue
                    scheme = str((use.step.get("params") or {}).get("scheme") or "")
                    cls = lookup_scheme(scheme)
                    if requirement.needs_facts and cls is not None and "describe" not in cls.capabilities:
                        problems.append(
                            f"agent {agent!r}: identity-gate requirement {tool!r} needs facts, but scheme "
                            f"{scheme!r} of {record!r} has no describe"
                        )
        problems_or_warnings = problems if require_gate else warnings
        if _uses_changing_tools(spec):
            covered = {
                tool
                for config in gate_configs(spec.get("governance"), guard=True)
                for tool in config.get("protect", ("identity_delegate", "identity_revoke"))
            }
            if not {"identity_delegate", "identity_revoke"} & covered:
                problems_or_warnings.append(
                    f"agent {agent!r} uses the identity system plugin but has no identity-guard that covers "
                    "identity_delegate or identity_revoke: those tools refuse every call until one is configured"
                )
        if held and not configs:
            message = (
                f"agent {agent!r} holds records {sorted(held)} but no identity-gate is in its governance list: "
                "the identity layer still enforces, but nothing refuses a call before it starts"
            )
            (problems if require_gate else warnings).append(message)
    return {
        "ok": not problems,
        "problems": list(dict.fromkeys(problems)),
        "warnings": warnings,
        "records": sorted(resolved.identities or {}),
        "agents": out_agents,
    }


def listing(resolved: ResolvedInfra, agent_ids: list[str]) -> dict[str, Any]:
    """The records, their scheme and who uses them. The names of secret params, never a value or a reference."""
    users: dict[str, list[dict[str, str]]] = {}
    for use in iter_uses(resolved):
        for agent in [use.agent] if use.agent else list(agent_ids) or ["(every agent)"]:
            users.setdefault(use.record, []).append({"agent": agent, "where": use.where})
    by_agent: dict[str, list[str]] = {}
    for record, entries in users.items():
        for entry in entries:
            held = by_agent.setdefault(entry["agent"], [])
            if record not in held:
                held.append(record)
    secret_names: dict[str, list[str]] = {}
    if resolved.identities:
        from mas.library.standard.lib.identity.rules import secret_params
        from mas.library.standard.plugins.identity.layer import lookup_scheme

        for record, row in resolved.identities.items():
            cls = lookup_scheme(str(row.get("scheme") or ""))
            secret_names[record] = (
                sorted(secret_params(cls.PARAMS_SCHEMA) & set(row.get("params") or {})) if cls else []
            )
    return {
        "agents": {agent: sorted(records) for agent, records in sorted(by_agent.items())},
        "identities": [
            {
                "id": record,
                "scheme": row.get("scheme", ""),
                "secret_params": secret_names.get(record, []),
                "used_by": users.get(record, []),
            }
            for record, row in (resolved.identities or {}).items()
        ],
    }


def describe(resolved: ResolvedInfra, record: str) -> dict[str, Any]:
    """The ``facts`` of the public view of each identity of one record's wallet, built the way a session builds it.

    Only what the agent's own tools may show: never the view's attestation or advertisement, never a proof."""
    row = (resolved.identities or {}).get(record)
    if row is None:
        raise LookupError(f"record {record!r} is not an Identity row (known: {sorted(resolved.identities or {})})")
    from mas.library.standard.lib.identity.caller import Caller
    from mas.library.standard.plugins.identity.layer import lookup_scheme

    cls = lookup_scheme(str(row.get("scheme") or ""))
    if cls is None:
        raise ValueError(f"scheme {row.get('scheme')!r} of {record!r} is not installed")
    params = dict(row.get("params") or {})
    problems = _param_problems(record, cls, params)
    if problems:
        raise ValueError("; ".join(problems))
    try:
        wallet = cls.from_spec(params)
    except Exception as exc:  # a scheme's own message may hold a value: only the kind is said
        raise ValueError(f"scheme {row.get('scheme')!r} of {record!r} cannot be built ({type(exc).__name__})") from None
    caller = Caller(record, wallet)
    if "describe" not in caller.capabilities:
        raise ValueError(f"scheme {row.get('scheme')!r} of {record!r} has no describe")

    async def views() -> list[dict[str, Any]]:
        return [
            {
                "identity": info["id"],
                "status": info["status"],
                "view": {"facts": dict((await caller.describe(info["id"])).get("facts", {}))},  # what a tool may show
            }
            for info in caller.identities()
        ]

    return {"id": record, "scheme": row["scheme"], "identities": asyncio.run(views())}


__all__ = ["check", "describe", "gate_configs", "gate_names", "listing"]
