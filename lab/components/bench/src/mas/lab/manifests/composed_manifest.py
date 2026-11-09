#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Compose MAS manifests to an inlined kind:MAS tree and validate recursively."""

from __future__ import annotations

import copy
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _overlay_path(
    entry: str | dict,
    *,
    overlays_dir: Path,
    base_dir: Path,
) -> Path:
    if isinstance(entry, dict) and "ref" in entry:
        return (base_dir / entry["ref"]).resolve()
    return overlays_dir / f"{entry}.yaml"


def _is_inline_agent(entry: dict[str, Any]) -> bool:
    return str(entry.get("kind", "")).lower() == "agent" and isinstance(entry.get("spec"), dict)


def _load_agent_entry(entry: dict[str, Any], *, mas_dir: Path) -> tuple[str, dict[str, Any]]:
    if _is_inline_agent(entry):
        aid = str(entry.get("metadata", {}).get("name") or entry.get("id") or "agent")
        doc = copy.deepcopy(entry)
        return aid, doc

    from mas.runtime.spec.source import load_yaml_mapping, resolve_yaml_path

    ref = entry.get("ref")
    if not ref:
        raise FileNotFoundError(f"agency agent entry missing ref and kind:Agent: {entry!r}")
    path = resolve_yaml_path(str(ref), mas_dir)
    doc = load_yaml_mapping(path)
    if not isinstance(doc, dict):
        raise FileNotFoundError(f"agent manifest is not a mapping: {path}")
    doc["_validation_base_dir"] = str(path.parent)
    aid = str(entry.get("id") or doc.get("metadata", {}).get("name") or path.stem)
    merged = copy.deepcopy(doc)
    if entry.get("id") and merged.get("metadata", {}).get("name") != entry["id"]:
        merged.setdefault("metadata", {})["name"] = entry["id"]
    return aid, merged


def _apply_patch_to_agents(
    agents_by_id: dict[str, dict[str, Any]],
    patch: dict[str, Any],
) -> None:
    from mas.ctl.overlay import merge_agent_overlay

    per_agent = patch.get("agents")
    if isinstance(per_agent, dict):
        for agent_id, fragment in per_agent.items():
            if agent_id not in agents_by_id or not isinstance(fragment, dict):
                continue
            stub: dict[str, Any] = {"spec": {}}
            ctx = fragment.get("context")
            if isinstance(ctx, dict):
                stub["spec"]["context"] = copy.deepcopy(ctx)
            for key in ("design_pattern", "tools", "skills", "llm"):
                if key in fragment:
                    stub["spec"][key] = copy.deepcopy(fragment[key])
            agents_by_id[agent_id] = merge_agent_overlay(agents_by_id[agent_id], stub)

    dp_spec = patch.get("design_pattern")
    if dp_spec:
        overlay = {"spec": {"design_pattern": dp_spec}}
        for agent_id in list(agents_by_id):
            agents_by_id[agent_id] = merge_agent_overlay(agents_by_id[agent_id], overlay)


def materialize_composed_mas_tree(
    mas_yaml: Path,
    overlay_ids: list[str | dict],
    *,
    overlays_dir: Path | None = None,
    base_dir: Path | None = None,
) -> dict[str, Any]:
    """Return a kind:MAS document with agency agents inlined after overlay composition.

    MAS-level overlay merge uses :func:`mas.ctl.compose.runner.compose_run` (same as
    runtime). Agent YAML loading and patch replay use :func:`merge_agent_overlay`.
    Bench execution still uses :func:`load_stacked_config` for the runtime dict shape.
    """
    from mas.ctl.compose.runner import ComposeRequest, compose_run
    from mas.runtime.spec.source import load_yaml_mapping

    if not mas_yaml.is_file():
        raise FileNotFoundError(f"mas.yaml not found: {mas_yaml}")

    mas_dir = mas_yaml.parent
    _overlays_dir = overlays_dir if overlays_dir is not None else mas_dir / "overlays"
    _base_dir = base_dir if base_dir is not None else mas_dir

    overlay_paths: list[Path] = []
    for overlay_entry in overlay_ids:
        overlay_path = _overlay_path(overlay_entry, overlays_dir=_overlays_dir, base_dir=_base_dir)
        if not overlay_path.is_file():
            raise FileNotFoundError(f"Overlay {overlay_entry!r} not found: {overlay_path}")
        overlay_paths.append(overlay_path)

    mas_result = compose_run(
        ComposeRequest(
            manifest=mas_yaml,
            overlay_paths=overlay_paths,
            validate=False,
        )
    )
    mas = mas_result.mas_config
    agent_patches = {}
    if mas_result.bind.composed_application is not None:
        agent_patches = mas_result.bind.composed_application.agent_patches or {}

    agents_by_id: dict[str, dict[str, Any]] = {}
    spec = mas.get("spec") or {}
    agency = spec.get("agency") or {}
    for entry in agency.get("agents") or spec.get("agents") or []:
        if not isinstance(entry, dict):
            continue
        aid, doc = _load_agent_entry(entry, mas_dir=mas_dir)
        from mas.ctl.manifest.mas_agent_merge import apply_agency_entry_overlay

        if not _is_inline_agent(entry):
            doc = apply_agency_entry_overlay(doc, entry, agent_patch=agent_patches.get(aid))
        agents_by_id[aid] = doc

    for overlay_path in overlay_paths:
        overlay = load_yaml_mapping(overlay_path)
        if str(overlay.get("kind") or "") == "Control":
            continue
        patch = (overlay.get("spec") or {}).get("patch") or {}
        if isinstance(patch, dict):
            _apply_patch_to_agents(agents_by_id, patch)

    from mas.ctl.infra.identities import declared_entry_id, peers_of
    from mas.ctl.session.flavour_control import apply_overlay_controls_to_manifest, control_files_from_paths

    ids = list(agents_by_id)
    entry = declared_entry_id(mas)
    delegates = list(peers_of(mas, ids).get(entry) or [])
    control_rows = control_files_from_paths(overlay_paths)
    for aid in ids:
        agents_by_id[aid] = apply_overlay_controls_to_manifest(
            agents_by_id[aid],
            (),
            agent_id=aid,
            entry_id=entry,
            delegate_ids=delegates,
            control_rows=control_rows,
        )

    spec = mas.setdefault("spec", {})
    agency = spec.setdefault("agency", {})
    final_ids: list[str] = []
    for entry in agency.get("agents") or []:
        if isinstance(entry, dict):
            eid = str(entry.get("id") or entry.get("metadata", {}).get("name") or "")
            if eid and eid in agents_by_id and eid not in final_ids:
                final_ids.append(eid)
    for aid in agents_by_id:
        if aid not in final_ids:
            final_ids.append(aid)

    agency["agents"] = [agents_by_id[aid] for aid in final_ids if aid in agents_by_id]
    return mas


def validate_composed_scenario(
    mas_yaml: Path,
    overlay_ids: list[str | dict],
    *,
    overlays_dir: Path | None,
    base_dir: Path | None,
    label: str,
) -> list[str]:
    """Materialize kind:MAS with inlined agents and validate recursively."""
    try:
        mas_doc = materialize_composed_mas_tree(
            mas_yaml,
            overlay_ids,
            overlays_dir=overlays_dir,
            base_dir=base_dir,
        )
    except FileNotFoundError as exc:
        return [f"{label} composed MAS — {exc}"]
    except Exception as exc:
        return [f"{label} composed MAS — {exc}"]
    return validate_composed_mas_tree(mas_doc, label=label, mas_dir=mas_yaml.parent)


def apply_manifest_defaults(doc: dict[str, Any]) -> dict[str, Any]:
    """Apply the same defaults the runtime uses before validating agent subtrees."""
    out = copy.deepcopy(doc)
    out.pop("_validation_base_dir", None)
    if str(out.get("kind", "")).lower() != "agent":
        return out
    spec = out.setdefault("spec", {})
    if not spec.get("design_pattern"):
        spec["design_pattern"] = {"type": "react"}
    return out


def validate_composed_mas_tree(mas_doc: dict[str, Any], *, label: str, mas_dir: Path) -> list[str]:
    """Validate a composed kind:MAS tree: mas.schema at root, agent.schema per agent subtree."""
    from mas.ctl.validate import validate_data

    violations: list[str] = []

    agent_bases: dict[str, Path] = {}
    mas_for_schema = copy.deepcopy(mas_doc)
    spec = mas_for_schema.get("spec") or {}
    for entry in (spec.get("agency") or {}).get("agents") or []:
        if not isinstance(entry, dict):
            continue
        aid = str(entry.get("metadata", {}).get("name") or entry.get("id") or "")
        if entry.get("_validation_base_dir"):
            agent_bases[aid] = Path(str(entry["_validation_base_dir"]))
            entry.pop("_validation_base_dir", None)

    mas_result = validate_data(
        mas_for_schema,
        source=label,
        kind="mas",
        strict=True,
        base_dir=mas_dir,
        resolve_refs=False,
    )
    for issue in mas_result.issues:
        if issue.level == "error":
            violations.append(f"{label}: mas — {issue.path}: {issue.message}")

    spec = mas_doc.get("spec") or {}
    agency = spec.get("agency") or {}
    agents = list(agency.get("agents") or spec.get("agents") or [])
    if not agents:
        violations.append(f"{label}: composed MAS has no agents (cardinality 1..N)")
        return violations

    agent_ids = {str(a.get("metadata", {}).get("name") or a.get("id") or "") for a in agents if isinstance(a, dict)}
    agent_ids.discard("")
    entry = (spec.get("workflow") or {}).get("entry")
    if entry and str(entry) not in agent_ids:
        violations.append(f"{label}: workflow entry {entry!r} not in composed agents {sorted(agent_ids)!r}")
    for node in (spec.get("workflow") or {}).get("nodes") or []:
        if isinstance(node, dict) and node.get("id") and str(node["id"]) not in agent_ids:
            violations.append(f"{label}: workflow node {node['id']!r} has no matching composed agent")

    for entry in agents:
        if not isinstance(entry, dict):
            continue
        if not _is_inline_agent(entry):
            violations.append(f"{label}: agency agent is not inlined after compose: {entry!r}")
            continue
        agent_id = str(entry.get("metadata", {}).get("name") or "?")
        agent_base = agent_bases.get(agent_id) or Path(str(entry.get("_validation_base_dir") or mas_dir))
        agent_doc = apply_manifest_defaults(entry)
        agent_result = validate_data(
            agent_doc,
            source=f"{label}:{agent_id}",
            kind="agent",
            strict=True,
            base_dir=agent_base,
            resolve_refs=True,
        )
        for issue in agent_result.issues:
            if issue.level == "error":
                violations.append(f"{label}: agent {agent_id!r} — {issue.path}: {issue.message}")

    return violations
