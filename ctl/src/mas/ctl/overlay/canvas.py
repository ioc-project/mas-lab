#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""UI canvas overlay convention.

The canvas builder stores layout (`x-canvas-positions`, `x-canvas-node-ids`,
`x-text-input`) on a dedicated Overlay next to the MAS rather than on runtime
manifests. The UI finds that overlay automatically using this convention:

1. Path: ``{mas_dir}/overlays/ui-canvas.yaml`` (preferred).
2. ``metadata.name: ui-canvas``.
3. Explicit marker ``x-ui-canvas: true`` on the overlay document.

A matching filename is not enough: the document must be ``kind: Overlay`` and
match one of those three signals. Behavioral overlays that happen to carry
canvas coordinates (for example ``cot-moderator.yaml``) are **not** canvas
overlays.

Auto-apply for the UI copies document-level ``x-*`` fields onto the MAS and
projects per-agent node ids / text input onto agent documents. It never
applies ``spec.patch``, so a canvas overlay cannot change runtime spec.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from mas.ctl.overlay.merge import apply_document_extensions

_LOG = logging.getLogger(__name__)

UI_CANVAS_OVERLAY_NAME = "ui-canvas"
UI_CANVAS_OVERLAY_FILENAME = f"{UI_CANVAS_OVERLAY_NAME}.yaml"

# OverlayBuilder uses camelCase keys; CanvasBuilder uses these names.
_NODE_ID_ALIASES: dict[str, str] = {
    "agent": "agent",
    "model": "model",
    "designPattern": "design_pattern",
    "design_pattern": "design_pattern",
    "tool": "tools",
    "tools": "tools",
    "promptSkills": "promptSkills",
    "prompt_skills": "promptSkills",
    "contextSkills": "contextSkills",
    "context_skills": "contextSkills",
    "memory": "memory",
    "role": "role",
    "inputPrompt": "role",
}


def is_ui_canvas_overlay(doc: Any, *, path: Path | None = None) -> bool:
    """Return True when *doc* (and optional *path*) matches the canvas convention."""
    if not isinstance(doc, dict):
        return False
    if str(doc.get("kind") or "") != "Overlay":
        return False
    if doc.get("x-ui-canvas") is True:
        return True
    name = str((doc.get("metadata") or {}).get("name") or "").strip()
    if name == UI_CANVAS_OVERLAY_NAME:
        return True
    if path is not None and path.stem == UI_CANVAS_OVERLAY_NAME:
        return True
    return False


def _load_yaml_mapping(path: Path) -> dict[str, Any] | None:
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        _LOG.debug("skip unreadable YAML %s: %s", path, exc)
        return None
    return doc if isinstance(doc, dict) else None


def find_ui_canvas_overlay(mas_dir: Path) -> Path | None:
    """Locate the UI canvas overlay for a MAS directory.

    Prefers ``overlays/ui-canvas.yaml`` beside ``mas.yaml``, then any overlay
    in that folder whose name or ``x-ui-canvas`` marker matches. The preferred
    filename is accepted only when the file is a canvas overlay.
    """
    overlays_dir = mas_dir / "overlays"
    preferred = overlays_dir / UI_CANVAS_OVERLAY_FILENAME
    alt = overlays_dir / f"{UI_CANVAS_OVERLAY_NAME}.yml"
    candidates: list[Path] = []
    if preferred.is_file():
        candidates.append(preferred)
    if alt.is_file() and alt not in candidates:
        candidates.append(alt)
    if overlays_dir.is_dir():
        for path in sorted(overlays_dir.glob("*.yaml")) + sorted(overlays_dir.glob("*.yml")):
            if path not in candidates:
                candidates.append(path)
    for path in candidates:
        doc = _load_yaml_mapping(path)
        if doc is not None and is_ui_canvas_overlay(doc, path=path):
            return path
    return None


def _normalize_node_ids(raw: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    if not isinstance(raw, dict):
        return out
    for key, value in raw.items():
        alias = _NODE_ID_ALIASES.get(str(key))
        if alias is None or value is None:
            continue
        out[alias] = str(value)
    return out


def project_ui_canvas(
    mas_doc: dict[str, Any],
    agents: dict[str, dict[str, Any]],
    overlay: dict[str, Any],
) -> None:
    """Copy canvas ``x-*`` fields from *overlay* onto *mas_doc* and *agents*.

    Mutates the documents in place. ``spec.patch`` is ignored.
    """
    apply_document_extensions(mas_doc, overlay)

    node_ids = overlay.get("x-canvas-node-ids")
    if isinstance(node_ids, dict):
        for agent_id, raw_ids in node_ids.items():
            agent = agents.get(str(agent_id))
            if not isinstance(agent, dict):
                continue
            ids = _normalize_node_ids(raw_ids)
            agent_node = ids.pop("agent", None)
            if agent_node:
                metadata = agent.setdefault("metadata", {})
                if isinstance(metadata, dict):
                    metadata["x-node-id"] = agent_node
            if ids:
                existing = agent.get("x-canvas-node-ids")
                merged = existing if isinstance(existing, dict) else {}
                merged.update(ids)
                agent["x-canvas-node-ids"] = merged

    text_input = overlay.get("x-text-input")
    if isinstance(text_input, dict):
        for agent_id, text in text_input.items():
            agent = agents.get(str(agent_id))
            if not isinstance(agent, dict) or text is None:
                continue
            spec = agent.setdefault("spec", {})
            if isinstance(spec, dict):
                spec["x-text-input"] = text


def apply_ui_canvas_to_yaml(
    mas_dir: Path,
    mas_yaml: str,
    agents: dict[str, str],
) -> tuple[str, dict[str, str], Path | None]:
    """Parse YAML, project a sibling canvas overlay, and dump YAML back."""
    overlay_path = find_ui_canvas_overlay(mas_dir)
    if overlay_path is None:
        return mas_yaml, agents, None
    overlay = _load_yaml_mapping(overlay_path)
    if overlay is None or not is_ui_canvas_overlay(overlay, path=overlay_path):
        return mas_yaml, agents, None
    try:
        mas_doc = yaml.safe_load(mas_yaml) or {}
        agent_docs = {name: (yaml.safe_load(text) or {}) for name, text in agents.items()}
    except Exception as exc:
        _LOG.debug("skip canvas apply; unreadable MAS/agent YAML: %s", exc)
        return mas_yaml, agents, None
    if not isinstance(mas_doc, dict):
        return mas_yaml, agents, None
    typed_agents = {k: v for k, v in agent_docs.items() if isinstance(v, dict)}
    if len(typed_agents) != len(agents):
        return mas_yaml, agents, None
    project_ui_canvas(mas_doc, typed_agents, overlay)
    return (
        _dump_yaml(mas_doc),
        {name: _dump_yaml(doc) for name, doc in typed_agents.items()},
        overlay_path,
    )


def extract_ui_canvas_overlay(
    mas_doc: dict[str, Any],
    agents: dict[str, dict[str, Any]],
    *,
    mas_name: str,
) -> dict[str, Any] | None:
    """Pull canvas ``x-*`` off MAS/agent docs into an overlay document.

    Mutates *mas_doc* and *agents* to remove the extracted fields. Returns
    ``None`` when there is no canvas metadata to store.
    """
    positions = mas_doc.pop("x-canvas-positions", None)
    node_ids: dict[str, Any] = {}
    text_input: dict[str, Any] = {}
    for agent_id, agent in agents.items():
        if not isinstance(agent, dict):
            continue
        ids = agent.pop("x-canvas-node-ids", None)
        metadata = agent.get("metadata")
        agent_node = None
        if isinstance(metadata, dict):
            agent_node = metadata.pop("x-node-id", None)
        collected = _normalize_node_ids(ids) if isinstance(ids, dict) else {}
        if agent_node:
            collected["agent"] = str(agent_node)
        if collected:
            node_ids[agent_id] = collected
        spec = agent.get("spec")
        if isinstance(spec, dict) and "x-text-input" in spec:
            text_input[agent_id] = spec.pop("x-text-input")

    if not positions and not node_ids and not text_input:
        return None

    overlay: dict[str, Any] = {
        "apiVersion": "mas/v1",
        "kind": "Overlay",
        "metadata": {
            "name": UI_CANVAS_OVERLAY_NAME,
            "description": f"UI-only canvas layout for {mas_name}. Ignored by the runtime.",
        },
        "spec": {
            "target": {"kind": "MAS", "name": mas_name},
            "patch": {},
        },
        "x-ui-canvas": True,
    }
    if positions is not None:
        overlay["x-canvas-positions"] = positions
    if node_ids:
        overlay["x-canvas-node-ids"] = node_ids
    if text_input:
        overlay["x-text-input"] = text_input
    return overlay


def write_ui_canvas_overlay(mas_dir: Path, overlay: dict[str, Any]) -> Path:
    """Write *overlay* to the canonical canvas overlay path, preserving extras."""
    dest = find_ui_canvas_overlay(mas_dir) or (mas_dir / "overlays" / UI_CANVAS_OVERLAY_FILENAME)
    dest.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, Any] = {}
    if dest.is_file():
        loaded = _load_yaml_mapping(dest)
        if loaded is not None:
            existing = loaded
    merged = deepcopy(existing)
    merged.update(overlay)
    if isinstance(existing.get("metadata"), dict) and isinstance(overlay.get("metadata"), dict):
        meta = dict(existing["metadata"])
        meta.update(overlay["metadata"])
        merged["metadata"] = meta
    dest.write_text(_dump_yaml(merged), encoding="utf-8")
    return dest


def _dump_yaml(doc: dict[str, Any]) -> str:
    return yaml.dump(doc, sort_keys=False, allow_unicode=True, width=100)
