#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0

"""MAS manifest loading for mas-lab bench — compose + agent resolution."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mas.ctl.compose.runner import ComposeRequest, ComposeResult, compose_run
from mas.ctl.executor.mas_session import entry_agent_id
from mas.ctl.infra.identities import agency_row_id
from mas.ctl.manifest.agency_groups import group_members
from mas.ctl.manifest.mas_agent_merge import apply_agency_entry_overlay, find_agency_entry
from mas.ctl.overlay import apply_merge_patch
from mas.ctl.paths import OverlayRefEntry, resolve_overlay_ref_entries
from mas.ctl.runtime_cli import load_merged_agent_manifest
from mas.ctl.validate import validate_file, validation_enabled
from mas.runtime.spec.source import load_yaml_mapping, resolve_yaml_path


@dataclass
class LoadedMAS:
    """Materialized MAS config (runtime dict) for bench compatibility."""

    _raw: dict[str, Any]
    path: Path


LOADED_MAS_RAW_KEY = "_loaded_mas_raw"


def is_loaded_mas_raw(config: dict[str, Any]) -> bool:
    """True when *config* is :attr:`LoadedMAS._raw` from :func:`load_mas_config`."""
    return bool(config.get(LOADED_MAS_RAW_KEY))


def should_merge_stacked_entry_agent_config(config: dict[str, Any]) -> bool:
    """Whether bench stacked agent rows should overlay the entry manifest."""
    return bool(config.get("agents")) and not is_loaded_mas_raw(config)


def agent_manifest_from_path(
    agent_path: Path,
    overlay_refs: list[OverlayRefEntry] | None = None,
    *,
    overlays_dir: Path | None = None,
    overlay_base_dir: Path | None = None,
    validate: bool = False,
) -> tuple[dict[str, Any], Path]:
    """Load a standalone agent manifest (with overlay refs) for bench bootstrap."""
    manifest_dir = agent_path.parent.parent if agent_path.parent.name == "agents" else agent_path.parent
    overlay_paths = resolve_overlay_ref_entries(
        overlay_refs or [],
        manifest_dir=manifest_dir,
        overlays_dir=overlays_dir,
        base_dir=overlay_base_dir,
    )
    overlays = tuple(str(p) for p in overlay_paths)
    agent, _ = load_merged_agent_manifest(
        agent_path,
        overlays=overlays,
        validate=validate,
        manifest_dir=manifest_dir,
    )
    return agent or {}, agent_path


def entry_agent_from_compose(
    result: ComposeResult,
    mas_path: Path,
    *,
    validate: bool = False,
) -> tuple[dict[str, Any], Path]:
    """Extract entry agent manifest + path from an already-run compose result."""
    entry = entry_agent_id(result.mas_config)
    agent_path = _agent_path_for_entry(result.mas_config, mas_path, entry)
    agent, _ = load_merged_agent_manifest(
        agent_path,
        overlays=(),
        validate=validate,
        manifest_dir=mas_path.parent,
    )
    agency_entry = find_agency_entry(result.mas_config, entry)
    if agent:
        patches = {}
        if result.bind.composed_application is not None:
            patches = result.bind.composed_application.agent_patches or {}
        agent = apply_agency_entry_overlay(
            agent,
            agency_entry or {"id": entry},
            agent_patch=patches.get(entry),
        )
    return agent or {}, agent_path


def _stacked_row_to_agency_entry(row: dict[str, Any]) -> dict[str, Any]:
    """Map legacy stacked bench agent rows onto agency-entry overlay shape."""
    agency_entry = deepcopy(row)
    spec = agency_entry.setdefault("spec", {})
    if not isinstance(spec, dict):
        spec = {}
        agency_entry["spec"] = spec

    if isinstance(row.get("context"), dict) and "context" not in spec:
        spec["context"] = deepcopy(row["context"])

    if row.get("spec_tools") and "tools" not in spec:
        spec["tools"] = list(row["spec_tools"])

    dp = row.get("design_pattern")
    if not isinstance(dp, dict) and row.get("pattern_framework"):
        dp = {"type": row["pattern_framework"]}
        params = row.get("pattern_params")
        if isinstance(params, dict) and params:
            dp["config"] = params
    if isinstance(dp, dict) and "design_pattern" not in spec:
        spec["design_pattern"] = deepcopy(dp)

    return agency_entry


def merge_stacked_entry_agent_manifest(
    agent_cfg: dict[str, Any],
    stacked_config: dict[str, Any],
) -> dict[str, Any]:
    """Apply stacked MAS entry-agent overrides onto an agent manifest for bootstrap."""
    entry_id = (stacked_config.get("mas") or {}).get("entry_agent")
    if not entry_id:
        return agent_cfg
    row = next(
        (a for a in (stacked_config.get("agents") or []) if a.get("id") == entry_id),
        None,
    )
    if not isinstance(row, dict):
        return agent_cfg
    return apply_agency_entry_overlay(agent_cfg, _stacked_row_to_agency_entry(row))


def _agent_path_for_entry(mas: dict[str, Any], mas_path: Path, entry_id: str) -> Path:
    spec = mas.get("spec") or mas
    agency = spec.get("agency") or {}
    base = mas_path.parent
    for entry in agency.get("agents") or []:
        if not isinstance(entry, dict):
            continue
        aid = str(entry.get("id") or entry.get("name") or "")
        if aid and aid != entry_id:
            continue
        ref = entry.get("ref")
        if ref:
            return resolve_yaml_path(str(ref), base)
    for ref in (f"agents/{entry_id}.yaml", f"{entry_id}.yaml", "agent.yaml"):
        try:
            return resolve_yaml_path(ref, base)
        except FileNotFoundError:
            continue
    raise FileNotFoundError(f"agent manifest for entry {entry_id!r} not found near {mas_path}")


def _agent_runtime_dict(doc: dict[str, Any], *, agent_id: str, agent_dir: Path) -> dict[str, Any]:
    meta = doc.get("metadata") or {}
    spec = doc.get("spec") or {}
    dp = spec.get("design_pattern") or {}
    models = spec.get("models") or []
    raw: dict[str, Any] = {
        "id": agent_id,
        "name": meta.get("name") or agent_id,
        "pattern_framework": dp.get("type", "react"),
        "pattern_params": dp.get("config") or dp.get("params") or {},
        "has_design_pattern": bool(dp),
        "_agent_dir": str(agent_dir),
    }
    if models:
        raw["llm_model"] = models[0].get("model") if isinstance(models[0], dict) else None
    if spec.get("tools"):
        raw["spec_tools"] = spec["tools"]
    if spec.get("description"):
        raw["description"] = spec["description"]
    if spec.get("context"):
        raw["context"] = spec["context"]
    if spec.get("governance") is not None:
        raw["governance"] = deepcopy(spec["governance"])
    if spec.get("observability") is not None:
        raw["observability"] = deepcopy(spec["observability"])
    return raw


def load_mas_config(
    mas_path: Path,
    *,
    overlay_paths: list[Path] | None = None,
    infra_refs: list[str] | None = None,
    workspace_root: Path | None = None,
    validate: bool = False,
) -> LoadedMAS:
    """Load mas.yaml via compose; expand agency refs into runtime ``_raw`` dict."""
    doc = load_yaml_mapping(mas_path)
    if str((doc or {}).get("kind", "")).lower() == "agent":
        return _loaded_mas_from_agent(mas_path, doc or {})

    if validate and validation_enabled():
        validate_file(mas_path, kind="mas").raise_if_failed()
    result = compose_run(
        ComposeRequest(
            manifest=mas_path,
            workspace_root=workspace_root,
            overlay_paths=list(overlay_paths or []),
            infra_refs=list(infra_refs or []),
            validate=validate,
        )
    )
    mas = result.mas_config
    base_dir = mas_path.parent
    raw_agents: list[dict[str, Any]] = []
    entry_id = entry_agent_id(mas)
    spec = mas.get("spec") or mas
    agency = spec.get("agency") or {}
    for entry in agency.get("agents") or []:
        if not isinstance(entry, dict):
            continue
        if "ref" in entry:
            ap = resolve_yaml_path(str(entry["ref"]), base_dir)
            agent_doc, _ = load_merged_agent_manifest(ap, validate=False)
            patches = {}
            if result.bind.composed_application is not None:
                patches = result.bind.composed_application.agent_patches or {}
            aid = agency_row_id(entry) or str((agent_doc or {}).get("metadata", {}).get("name") or ap.stem)
            agent_doc = apply_agency_entry_overlay(agent_doc or {}, entry, agent_patch=patches.get(aid))
            raw_agents.append(_agent_runtime_dict(agent_doc or {}, agent_id=aid, agent_dir=ap.parent))
        elif entry.get("kind", "").lower() == "agent" or "metadata" in entry:
            aid = agency_row_id(entry) or "agent"
            raw_agents.append(_agent_runtime_dict(entry, agent_id=aid, agent_dir=base_dir))

    if not raw_agents and str(mas.get("kind", "")).lower() == "agent":
        raw_agents.append(_agent_runtime_dict(mas, agent_id=entry_id, agent_dir=base_dir))

    raw = {
        LOADED_MAS_RAW_KEY: True,
        "mas": {
            "id": mas.get("metadata", {}).get("name") or mas_path.stem,
            "version": mas.get("metadata", {}).get("version", "0.1.0"),
            "description": mas.get("metadata", {}).get("description", ""),
            "entry_agent": entry_id,
        },
        "agents": raw_agents,
        "groups": group_members(mas),
        "workflow": spec.get("workflow") or {},
        "capabilities": spec.get("capabilities") or {},
    }
    return LoadedMAS(_raw=raw, path=mas_path)


def _loaded_mas_from_agent(agent_path: Path, doc: dict[str, Any]) -> LoadedMAS:
    """Legacy compat: synthesize a MAS-shaped runtime dict from a lone agent manifest."""
    base_dir = agent_path.parent
    entry_id = str((doc.get("metadata") or {}).get("name") or agent_path.stem)
    agent_row = _agent_runtime_dict(doc, agent_id=entry_id, agent_dir=base_dir)
    raw = {
        LOADED_MAS_RAW_KEY: True,
        "mas": {
            "id": entry_id,
            "version": (doc.get("metadata") or {}).get("version", "0.1.0"),
            "description": (doc.get("metadata") or {}).get("description", ""),
            "entry_agent": entry_id,
        },
        "agents": [agent_row],
        "workflow": {
            "type": "dynamic",
            "entry": entry_id,
            "nodes": [{"id": entry_id, "role": "specialist"}],
        },
        "capabilities": (doc.get("spec") or {}).get("capabilities") or {},
    }
    return LoadedMAS(_raw=raw, path=agent_path)


def load_overlay_as_spec(overlay_path: Path, overlay: dict[str, Any] | None = None) -> dict[str, Any]:
    """Merge overlay patch into sibling mas.yaml (legacy check-run helper)."""
    overlay_path = Path(overlay_path)
    ov = overlay if overlay is not None else load_yaml_mapping(overlay_path)
    candidates = [
        overlay_path.parent.parent / "mas.yaml",
        overlay_path.parent / "mas.yaml",
    ]
    mas_yaml = next((c for c in candidates if c.is_file()), None)
    if mas_yaml is None:
        raise FileNotFoundError(f"overlay {overlay_path.name}: no sibling mas.yaml")
    spec = dict(load_mas_config(mas_yaml, validate=False)._raw)
    patch = (ov.get("spec") or {}).get("patch") or {}
    if patch:
        apply_merge_patch(spec, patch)
    return spec


def load_agent_runtime_entry(agent_path: Path, *, agent_id: str | None = None) -> dict[str, Any]:
    """Expand an agent YAML path into a bench runtime agent row."""
    doc, _ = load_merged_agent_manifest(agent_path, validate=False)
    aid = agent_id or (doc or {}).get("metadata", {}).get("name") or agent_path.stem
    return _agent_runtime_dict(doc or {}, agent_id=str(aid), agent_dir=agent_path.parent)
