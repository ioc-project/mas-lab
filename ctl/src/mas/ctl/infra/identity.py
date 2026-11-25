#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Bind the ``Identity`` infra rows to the identity layers that name them.

An ``Identity`` row holds records: an id, a scheme and its params (a secret param is a ``{secret: NAME}`` reference).
An ``identity`` layer on a stack or an agent chain names one record (``params.identity``, ``{agent}`` allowed). When a
Flavour binds the infra, each such step gets the record's ``scheme`` and ``params`` and the id of the agent whose stack
holds it, so the layer builds the wallet and the gate finds it. Nothing here reads a secret: a reference stays a
reference until the layer asks the secret store at start.

A session with no identity layer never reaches the scheme registry or the identity library: :func:`uses_identity` is
a walk over plain dicts.
"""

from __future__ import annotations

import copy
import dataclasses
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from mas.ctl.compose.models import ResolvedInfra

LAYER = "identity"


class IdentityBindError(ValueError):
    """An identity layer names no record, a record no row declares, or a record the scheme refuses. Value-free."""


@dataclass(frozen=True)
class Use:
    """One identity layer step: where it sits, the agent whose stack holds it (``""`` for a shared one), the record."""

    where: str
    agent: str
    record: str
    step: dict[str, Any]


def _is_identity_step(step: Any, *, chain: bool) -> bool:
    if not isinstance(step, dict):
        return False
    if chain:
        return str(step.get("type") or "") == LAYER
    from mas.runtime.layers.compose import layer_id

    return layer_id(step) == LAYER


def _walk(
    resolved: ResolvedInfra, fn: Callable[[str, str, dict[str, Any], bool], Any], *, definitions: bool = False
) -> ResolvedInfra:
    """``resolved`` with ``fn(where, agent, step, chain)`` in place of every step of every pipeline and chain.

    ``definitions`` also visits the named ``Stack`` rows themselves (a shared stack the agents' chains are built from);
    the rows an agent's Flavour picked are in the pipelines already.
    """

    def pipeline(steps: list[Any], where: str, agent: str = "", *, chain: bool = False) -> list[Any]:
        return [fn(where, agent, step, chain) if isinstance(step, dict) else step for step in steps or []]

    updates: dict[str, Any] = {}
    llm = dict(resolved.llm_proxy or {})
    if llm.get("pipeline"):
        llm["pipeline"] = pipeline(llm["pipeline"], "the LLM pipeline")
        updates["llm_proxy"] = llm
    updates["llm_proxies"] = {
        name: {**leaf, "pipeline": pipeline(leaf["pipeline"], f"LLM leaf {name!r}")} if leaf.get("pipeline") else leaf
        for name, leaf in (resolved.llm_proxies or {}).items()
    }
    updates["tool_server_registry"] = {
        name: {**server, "pipeline": pipeline(server["pipeline"], f"tool server {name!r}")}
        if server.get("pipeline")
        else server
        for name, server in (resolved.tool_server_registry or {}).items()
    }
    updates["tool_pipeline"] = pipeline(resolved.tool_pipeline or [], "the shared tool pipeline")
    updates["llm_stack"] = pipeline(resolved.llm_stack or [], "bind.llm")
    updates["tool_stack"] = pipeline(resolved.tool_stack or [], "bind.tools")
    updates["agent_llm_pipelines"] = {
        agent: pipeline(steps, f"bind.agents.{agent}.llm", agent)
        for agent, steps in (resolved.agent_llm_pipelines or {}).items()
    }
    updates["agent_tool_pipelines"] = {
        agent: pipeline(steps, f"bind.agents.{agent}.tools", agent)
        for agent, steps in (resolved.agent_tool_pipelines or {}).items()
    }
    updates["agent_chains"] = {
        agent: pipeline(chain, f"the chain of agent {agent!r}", agent, chain=True)
        for agent, chain in (resolved.agent_chains or {}).items()
    }
    if definitions:
        updates["stacks"] = {
            name: pipeline(steps, f"stack {name!r}") for name, steps in (resolved.stacks or {}).items()
        }
    return dataclasses.replace(resolved, **updates)


def iter_uses(resolved: ResolvedInfra) -> Iterator[Use]:
    """Every identity layer step of ``resolved``, in the order a walk visits them."""
    found: list[Use] = []

    def visit(where: str, agent: str, step: dict[str, Any], chain: bool) -> dict[str, Any]:
        if _is_identity_step(step, chain=chain):
            params = step.get("params") if isinstance(step.get("params"), dict) else {}
            found.append(Use(where, agent, str(params.get("identity") or ""), step))
        return step

    _walk(resolved, visit)
    return iter(found)


def uses_identity(resolved: ResolvedInfra | None) -> bool:
    """Does any step of ``resolved`` name the identity layer? Plain dicts only: it imports nothing identity."""
    return resolved is not None and any(True for _ in iter_uses(resolved))


def _record_id(where: str, agent: str, params: dict[str, Any]) -> str:
    named = str(params.get("identity") or "").strip()
    if not named:
        raise IdentityBindError(f"{where}: the identity layer names no record (params.identity)")
    return named.replace("{agent}", agent) if agent else named


def inline_identities(resolved: ResolvedInfra | None) -> ResolvedInfra | None:
    """``resolved`` with each identity layer step carrying its record's ``scheme`` and ``params`` and its ``agent_id``.

    A step that already has a ``scheme`` is a record written in place and is left as it is. A step that names a record
    no ``Identity`` row declares raises :class:`IdentityBindError`, naming the step and the known records.
    """
    if resolved is None or not (uses_identity(resolved) or _in_stacks(resolved)):
        return resolved
    rows = dict(resolved.identities or {})
    scope = resolved.identity_scope or uuid.uuid4().hex

    def fill(where: str, agent: str, step: dict[str, Any], chain: bool) -> dict[str, Any]:
        if not _is_identity_step(step, chain=chain):
            return step
        params = dict(step.get("params") or {})
        if params.get("scheme"):
            stamped = {**params, "holder_scope": scope}
            return {
                **step,
                "params": stamped if not agent else {**stamped, "agent_id": params.get("agent_id") or agent},
            }
        record = _record_id(where, agent, params)
        if "{agent}" in record:  # a stack row shared by every agent: each agent's copy is filled where it is bound
            return step
        row = rows.get(record)
        if row is None:
            known = ", ".join(sorted(rows)) or "none"
            raise IdentityBindError(
                f"{where}: the identity layer names record {record!r}, which no Identity row declares (known: {known})"
            )
        filled = {
            **params,
            "identity": record,
            "scheme": str(row.get("scheme") or ""),
            "params": copy.deepcopy(dict(row.get("params") or {})),
            "holder_scope": scope,
        }
        if agent:
            filled["agent_id"] = agent
        return {**step, "params": filled}

    return dataclasses.replace(_walk(resolved, fill, definitions=True), identity_scope=scope)


def _in_stacks(resolved: ResolvedInfra) -> bool:
    return any(_is_identity_step(step, chain=False) for steps in (resolved.stacks or {}).values() for step in steps)
