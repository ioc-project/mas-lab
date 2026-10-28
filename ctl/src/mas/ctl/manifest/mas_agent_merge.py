#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Merge MAS workflow onto the entry agent manifest at run-mas bootstrap."""

from __future__ import annotations

import copy
import logging
from pathlib import Path
from typing import Any

import yaml
from mas.ctl.overlay.merge import (
    _ops_dict,
    _plugin_entry_key,
    merge_agent_overlay,
)
from mas.runtime.boundary.agentcomm.protocol import AgentCommContract
from mas.runtime.boundary.agentcomm.routing import AgentCommRoute
from mas.runtime.boundary.context.manifest_context import routing_description_from_agent
from mas.runtime.boundary.delegation.policy import delegation_targets
from mas.runtime.contracts.tool_semantics import existing_attr
from mas.runtime.engine.leaf import leaf_engine
from mas.runtime.engine.llm_live import LiveLlmEngine
from mas.runtime.engine.tools import resolve_manifest_tool_refs
from mas.runtime.registry import get_registry

logger = logging.getLogger(__name__)


def _load_agent_yaml(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    return doc if isinstance(doc, dict) else None


def _tool_ref_key(item: Any) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        return str(item.get("ref") or item.get("name") or "")
    return str(item)


def _merge_tool_ref_list(existing: list[Any], added: list[Any]) -> list[Any]:
    out = list(existing)
    seen = {_tool_ref_key(t) for t in out if _tool_ref_key(t)}
    for item in added:
        key = _tool_ref_key(item)
        if not key:
            logger.warning(
                "tool list entry has no ref/name; merging without dedup key: %r",
                item,
            )
            out.append(copy.deepcopy(item))
            continue
        if key not in seen:
            out.append(copy.deepcopy(item))
            seen.add(key)
    return out


def _merge_agency_entry_tools(existing: list[Any], incoming: Any) -> list[Any]:
    """Merge an agency-entry's inline ``tools`` override onto the base list.

    A plain list is an ADD (dedup-merged onto existing) — unchanged,
    long-standing behavior (see ``_merge_tool_ref_list``). An
    ``{"$op": {...}}`` value additionally supports ``remove``/``replace``/
    ``clear`` — the same sugar ``tools: {"$op": {"remove": [...]}}`` already
    means at the overlay-patch level (``ctl/overlay/merge.py``), now also
    available here: this is the mechanism that replaces the old, separate
    ``tools_remove`` field for agency-entry inline overrides too (removed as
    a concept — a plain list previously fed straight to ``list(val)`` would
    have silently mangled an ``$op`` dict into its key names, so this isn't
    a behavior change for anyone actually relying on ``$op`` here; nothing
    could have).
    """
    ops = _ops_dict(incoming)
    if ops is None:
        return _merge_tool_ref_list(existing, list(incoming))
    result = list(existing)
    if ops.get("clear") is True:
        result = []
    if "replace" in ops:
        result = list(ops.get("replace") or [])
    if "remove" in ops:
        to_remove = {_tool_ref_key(t) for t in (ops.get("remove") or [])}
        result = [t for t in result if _tool_ref_key(t) not in to_remove]
    if "add" in ops:
        result = _merge_tool_ref_list(result, list(ops.get("add") or []))
    return result


def _agency_entries_by_id(mas_config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Index agency/spec.agents rows by id or name.

    ``spec.agency.agents`` is consulted before ``spec.agents``; the first row
    for a given id wins when both lists declare the same agent.
    """
    spec = mas_config.get("spec") or mas_config
    buckets = [
        (spec.get("agency") or {}).get("agents") or [],
        spec.get("agents") or [],
    ]
    by_id: dict[str, dict[str, Any]] = {}
    for agents in buckets:
        for entry in agents:
            if not isinstance(entry, dict):
                continue
            aid = str(entry.get("id") or entry.get("name") or "")
            if aid and aid not in by_id:
                by_id[aid] = entry
    return by_id


def find_agency_entry(mas_config: dict[str, Any] | None, agent_id: str) -> dict[str, Any] | None:
    """Return the agency/spec.agents list row for *agent_id*, if present."""
    if not mas_config or not agent_id:
        return None
    return _agency_entries_by_id(mas_config).get(agent_id)


def _entry_val(entry: dict[str, Any], entry_spec: dict[str, Any], field: str) -> Any:
    val = entry.get(field)
    return val if val is not None else entry_spec.get(field)


def _apply_description_overlay(
    spec: dict[str, Any],
    agency_entry: dict[str, Any],
    entry_spec: dict[str, Any],
) -> None:
    description = _entry_val(agency_entry, entry_spec, "description")
    if isinstance(description, str) and description.strip():
        spec["description"] = description.strip()


def _plugin_list_as_add(value: Any) -> Any:
    """Union a plain plugin list onto the agent YAML list (upsert by plugin id).

    Agency-entry inline ``governance`` / ``observability`` used to replace the
    agent YAML list. Agent overlays that fan out onto a MAS must keep other
    plugins the agent already declared. Same plugin id is replaced by the
    overlay stanza (so ``sample_governance`` plus policies overwrites a bare
    ``sample_governance`` string). An explicit empty list still clears.
    ``$op`` values pass through.
    """
    if _ops_dict(value) is not None:
        return copy.deepcopy(value)
    if isinstance(value, list):
        if not value:
            return {"$op": {"clear": True}}
        keys = [_plugin_entry_key(item) for item in value]
        return {"$op": {"remove": [k for k in keys if k], "add": copy.deepcopy(value)}}
    return copy.deepcopy(value)


def _patch_from_agency_entry(agency_entry: dict[str, Any]) -> dict[str, Any]:
    """Read leftover Agent fields off an agency row (pre-fix composed MAS)."""
    entry_spec = agency_entry.get("spec")
    if not isinstance(entry_spec, dict):
        entry_spec = {}
    patch: dict[str, Any] = {}
    for field in (
        "skills",
        "tools",
        "context",
        "description",
        "design_pattern",
        "context_manager",
        "assembler",
        "memory",
        "governance",
        "observability",
        "memory_seed",
        "llm",
        "budget",
    ):
        val = _entry_val(agency_entry, entry_spec, field)
        if val is not None:
            patch[field] = copy.deepcopy(val)
    for key, val in entry_spec.items():
        if key not in patch:
            patch[key] = copy.deepcopy(val)
    return patch


def apply_agency_entry_overlay(
    agent_manifest: dict[str, Any],
    agency_entry: dict[str, Any],
    *,
    mas_config: dict[str, Any] | None = None,
    agent_patch: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge an overlay's per-agent patch onto a loaded agent manifest.

    Pass *agent_patch* from :func:`mas.ctl.overlay.merge.loaded_agent_patches`
    (compile / compose collect these after each overlay). Leftover Agent
    fields on *agency_entry* still apply when the caller passes them
    explicitly (stacked bench rows). The MAS agency row itself stays
    ``{id, ref}``; nothing is read from the MAS document.
    """
    _ = mas_config
    out = copy.deepcopy(agent_manifest)
    patch = agent_patch
    if patch is None:
        patch = _patch_from_agency_entry(agency_entry)
    if not patch:
        return out

    normalized = copy.deepcopy(patch)
    for field in ("governance", "observability"):
        if field in normalized:
            normalized[field] = _plugin_list_as_add(normalized[field])
    merged = merge_agent_overlay(out, {"spec": {"patch": normalized}})
    spec = merged.setdefault("spec", {})

    # Tools keep agency-entry semantics (plain list = ADD; $op.remove matches
    # by ref). merge_agent_overlay's list_ops compares items by identity.
    tools_val = patch.get("tools")
    if tools_val is not None:
        base_tools = list((agent_manifest.get("spec") or {}).get("tools") or [])
        spec["tools"] = _merge_agency_entry_tools(base_tools, tools_val)

    memory_seed = patch.get("memory_seed")
    if memory_seed:
        existing_seed = list((agent_manifest.get("spec") or {}).get("memory_seed") or [])
        spec["memory_seed"] = existing_seed + list(copy.deepcopy(memory_seed))

    return merged


def apply_loaded_agent_patch(
    agent_manifest: dict[str, Any],
    *,
    agent_id: str,
    agent_patches: dict[str, Any] | None,
    mas_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply a compose/compile per-agent overlay patch onto loaded Agent YAML."""
    patch = (agent_patches or {}).get(str(agent_id))
    if not isinstance(patch, dict) or not patch:
        return agent_manifest
    from mas.ctl.overlay.merge import _LOADED_AGENT_OVERRIDES

    cli_overrides = list(patch.get(_LOADED_AGENT_OVERRIDES) or [])
    agent_patch = {key: value for key, value in patch.items() if key != _LOADED_AGENT_OVERRIDES}
    entry = find_agency_entry(mas_config, agent_id) if mas_config else None
    result = (
        apply_agency_entry_overlay(
            agent_manifest, entry or {"id": agent_id}, agent_patch=agent_patch
        )
        if agent_patch
        else agent_manifest
    )
    if cli_overrides:
        from mas.ctl.overrides import apply_cli_overrides

        result = apply_cli_overrides(result, cli_overrides, root="agent")
    return result


def _peer_manifests_for_ids(
    mas_config: dict[str, Any],
    *,
    mas_base_dir: Path,
    peer_ids: list[str],
) -> dict[str, dict[str, Any]]:
    by_id = _agency_entries_by_id(mas_config)
    out: dict[str, dict[str, Any]] = {}
    for peer_id in peer_ids:
        entry = by_id.get(peer_id)
        if not entry:
            continue
        ref = entry.get("ref")
        if not isinstance(ref, str) or not ref.strip():
            continue
        path = (mas_base_dir / ref).resolve()
        peer_manifest = _load_agent_yaml(path)
        if peer_manifest is None:
            logger.warning("peer agent %r manifest not found: %s", peer_id, path)
            continue
        out[peer_id] = apply_agency_entry_overlay(
            peer_manifest, entry, mas_config=mas_config
        )
    return out


def enrich_entry_agent_for_delegation(
    agent_manifest: dict[str, Any],
    mas_config: dict[str, Any],
    *,
    manifest_dir: Path | None = None,
    mas_base_dir: Path | None = None,
) -> dict[str, Any]:
    """Attach MAS ``workflow`` to the entry agent; resolve tool refs and peer descriptions."""
    out = copy.deepcopy(agent_manifest)
    mas_spec = mas_config.get("spec", mas_config) if isinstance(mas_config, dict) else {}
    wf = mas_spec.get("workflow")
    if isinstance(wf, dict):
        spec_out = out.setdefault("spec", {})
        if spec_out.get("workflow") and spec_out.get("workflow") != wf:
            logger.warning("entry agent spec.workflow replaced by MAS workflow (MAS topology wins)")
        spec_out["workflow"] = copy.deepcopy(wf)
    if manifest_dir is not None:
        resolve_manifest_tool_refs(out, manifest_dir, inplace=True)
    return out


def wire_entry_engine_delegation(
    engine: Any,
    manifest: dict[str, Any],
    manifest_dir: Path,
    *,
    comm: AgentCommContract,
    entry_agent_id: str,
    mas_config: dict[str, Any] | None = None,
    mas_base_dir: Path | None = None,
    routes: dict[str, AgentCommRoute] | None = None,
) -> None:
    """Set enriched manifest on the entry engine and bind the delegation plugin when peers exist.

    When peers exist, ``use_tool_loop`` is enabled on the leaf engine so the LLM can
    emit ``delegate_to_*`` tool calls. A manifest or instantiation that set
    ``use_tool_loop=False`` is overridden with a warning.
    """
    if engine is None:
        return
    leaf = leaf_engine(engine)
    leaf.manifest = manifest
    if isinstance(leaf, LiveLlmEngine):
        leaf.manifest_dir = manifest_dir
    peers = delegation_targets(manifest, agent_id=entry_agent_id)
    peer_manifests: dict[str, dict[str, Any]] = {}
    if isinstance(leaf, LiveLlmEngine) and peers and mas_config is not None and mas_base_dir is not None:
        peer_manifests = _peer_manifests_for_ids(mas_config, mas_base_dir=mas_base_dir, peer_ids=peers)
        leaf.delegation_peer_descriptions = {
            peer_id: desc
            for peer_id, manifest_doc in peer_manifests.items()
            if (desc := routing_description_from_agent(manifest_doc))
        }
    elif isinstance(leaf, LiveLlmEngine):
        leaf.delegation_peer_descriptions = None
    if not peers:
        leaf.delegation = None
        return
    # ``delegation`` names the job slot, not a swappable backend — see
    # ``subagent_spawner.py`` for the parallel ``spawn_subagent`` slot. A user
    # wanting a different delegation implementation registers a plugin under
    # the same ``delegation`` name; swapping job identifiers is not a
    # supported axis.
    leaf.delegation = get_registry().instantiate_by_type(
        "engine_tool_provider",
        "delegation",
        comm=comm,
        routes=routes,
        allowed_peers=peers,
    )
    if hasattr(leaf, "use_tool_loop"):
        if not leaf.use_tool_loop:
            logger.warning(
                "entry agent %r: enabling use_tool_loop for dynamic delegation (%d peers)",
                entry_agent_id,
                len(peers),
            )
            leaf.use_tool_loop = True


def reset_engine_delegation(engine: Any) -> None:
    """Clear delegate caches at the start of each user turn."""
    seen: set[int] = set()
    for _ in range(8):
        if engine is None or id(engine) in seen:
            break
        seen.add(id(engine))
        delegation = existing_attr(engine, "delegation")
        reset_fn = existing_attr(delegation, "reset_session")
        if callable(reset_fn):
            reset_fn()
        inner = existing_attr(engine, "inner")
        if inner is None or inner is engine:
            break
        engine = inner
