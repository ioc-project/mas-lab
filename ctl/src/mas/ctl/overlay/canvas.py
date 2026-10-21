#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""UI canvas layout on Overlay documents.

Canvas fields (`x-canvas-positions`, `x-canvas-node-ids`, `x-text-input`) live
only on **layout-only** Overlay documents — never on ``mas.yaml``, Agent
YAML, or a spec overlay that carries ``spec.patch``.

The UI does not look up a filename. Association is the overlay selector
experiments already use:

- ``kind: Overlay``
- ``spec.target.kind`` / ``spec.target.name`` name the document being laid out
  (a MAS, or another Overlay)
- ``spec.patch`` / ``overrides`` / ``tools`` are empty
- at least one canvas ``x-*`` key is present

The lab registry indexes overlays by that target while scanning. GET attaches
matching layout overlays from the index once both the document and the canvas
overlay exist, in either order. Spec overlays are never projected.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from mas.ctl.overlay.merge import apply_document_extensions

_LOG = logging.getLogger(__name__)

CANVAS_EXTENSION_KEYS = ("x-canvas-positions", "x-canvas-node-ids", "x-text-input")

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


def overlay_targets(
    doc: Any, *, kind: str, name: str, require_name: bool = False
) -> bool:
    """True when *doc* is an Overlay whose ``spec.target`` selects *kind*/*name*."""
    if not isinstance(doc, dict):
        return False
    if str(doc.get("kind") or "") != "Overlay":
        return False
    spec = doc.get("spec") if isinstance(doc.get("spec"), dict) else {}
    target = spec.get("target") if isinstance(spec.get("target"), dict) else {}
    target_kind = str(target.get("kind") or "").strip()
    if target_kind.lower() != kind.lower():
        return False
    target_name = str(target.get("name") or "").strip()
    if require_name:
        return target_name == name
    return not target_name or target_name == name


def overlay_targets_mas(doc: Any, *, mas_name: str, require_name: bool = False) -> bool:
    """True when *doc* is an Overlay whose ``spec.target`` selects *mas_name*."""
    return overlay_targets(doc, kind="MAS", name=mas_name, require_name=require_name)


def overlay_has_canvas_extensions(doc: Any) -> bool:
    """True when *doc* carries canvas ``x-*`` keys on the document root."""
    if not isinstance(doc, dict):
        return False
    return any(key in doc for key in CANVAS_EXTENSION_KEYS)


def overlay_is_layout_only(doc: Any) -> bool:
    """True for an overlay that holds canvas layout and no runtime patch."""
    if not overlay_has_canvas_extensions(doc):
        return False
    if not isinstance(doc, dict) or str(doc.get("kind") or "") != "Overlay":
        return False
    spec = doc.get("spec") if isinstance(doc.get("spec"), dict) else {}
    patch = spec.get("patch")
    if patch not in (None, {}):
        return False
    if spec.get("overrides"):
        return False
    if spec.get("tools"):
        return False
    return True


def is_ui_canvas_overlay(doc: Any, *, path: Path | None = None) -> bool:
    """Layout-only canvas overlay (hidden from overlay catalogs / stacks)."""
    del path
    return overlay_is_layout_only(doc)


def _load_yaml_mapping(path: Path) -> dict[str, Any] | None:
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        _LOG.debug("skip unreadable YAML %s: %s", path, exc)
        return None
    return doc if isinstance(doc, dict) else None


def _overlay_yaml_files(overlays_dir: Path) -> list[Path]:
    if not overlays_dir.is_dir():
        return []
    seen: set[Path] = set()
    out: list[Path] = []
    for path in sorted(overlays_dir.glob("*.yaml")) + sorted(overlays_dir.glob("*.yml")):
        if path in seen:
            continue
        seen.add(path)
        out.append(path)
    return out


def iter_layout_overlays(
    overlay_dirs: list[Path],
    *,
    target_kind: str,
    target_name: str,
    require_name: bool,
) -> list[tuple[Path, dict[str, Any]]]:
    """Layout-only overlays whose ``spec.target`` selects *target_kind*/*target_name*."""
    matched: list[tuple[Path, dict[str, Any]]] = []
    seen: set[Path] = set()
    for overlays_dir in overlay_dirs:
        for path in _overlay_yaml_files(overlays_dir):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            doc = _load_yaml_mapping(path)
            if doc is None:
                continue
            if not overlay_targets(
                doc, kind=target_kind, name=target_name, require_name=require_name
            ):
                continue
            if not overlay_is_layout_only(doc):
                continue
            matched.append((path, doc))
    return matched


def iter_targeting_canvas_overlays(
    mas_dir: Path,
    mas_name: str,
    extra_overlay_dirs: list[Path] | None = None,
) -> list[tuple[Path, dict[str, Any]]]:
    """Layout-only overlays that target MAS *mas_name*."""
    extra: list[Path] = []
    local: list[Path] = []
    local_dir = mas_dir / "overlays"
    for path in extra_overlay_dirs or []:
        if path.resolve() != local_dir.resolve():
            extra.append(path)
    if local_dir.is_dir():
        local.append(local_dir)
    named = iter_layout_overlays(
        extra, target_kind="MAS", target_name=mas_name, require_name=True
    )
    folder = iter_layout_overlays(
        local, target_kind="MAS", target_name=mas_name, require_name=False
    )
    return named + folder


def find_layout_overlay(
    mas_dir: Path,
    mas_name: str,
    extra_overlay_dirs: list[Path] | None = None,
    *,
    target_kind: str = "MAS",
) -> Path | None:
    """Path of the layout-only overlay targeting *mas_name*, if any."""
    if target_kind.upper() == "MAS":
        matched = iter_targeting_canvas_overlays(mas_dir, mas_name, extra_overlay_dirs)
    else:
        dirs = [mas_dir, *(extra_overlay_dirs or [])]
        matched = iter_layout_overlays(
            dirs, target_kind=target_kind, target_name=mas_name, require_name=True
        )
    return matched[-1][0] if matched else None


def find_ui_canvas_overlay(mas_dir: Path, mas_name: str | None = None) -> Path | None:
    """Backward-compatible alias for :func:`find_layout_overlay`."""
    if not mas_name:
        mas_file = mas_dir / "mas.yaml"
        loaded = _load_yaml_mapping(mas_file) if mas_file.is_file() else None
        mas_name = str((loaded or {}).get("metadata", {}).get("name") or mas_dir.name)
    return find_layout_overlay(mas_dir, mas_name)


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
    extra_overlay_dirs: list[Path] | None = None,
    overlays: list[tuple[Path, dict[str, Any]]] | None = None,
) -> tuple[str, dict[str, str], Path | None]:
    """Parse YAML, project layout-only canvas overlays, and dump YAML back.

    Pass *overlays* (registry records) to skip scanning *mas_dir*. An empty
    list means no canvas; ``None`` walks overlay folders (ctl / tests).
    """
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
    mas_name = str((mas_doc.get("metadata") or {}).get("name") or mas_dir.name).strip() or mas_dir.name
    matched = (
        overlays
        if overlays is not None
        else iter_targeting_canvas_overlays(mas_dir, mas_name, extra_overlay_dirs)
    )
    if not matched:
        return mas_yaml, agents, None
    last_path: Path | None = None
    for path, overlay in matched:
        project_ui_canvas(mas_doc, typed_agents, overlay)
        last_path = path
    return (
        _dump_yaml(mas_doc),
        {name: _dump_yaml(doc) for name, doc in typed_agents.items()},
        last_path,
    )


def apply_ui_canvas_to_overlay_yaml(
    overlay_dir: Path,
    overlay_yaml: str,
    extra_overlay_dirs: list[Path] | None = None,
    overlays: list[tuple[Path, dict[str, Any]]] | None = None,
) -> tuple[str, Path | None]:
    """Project a layout-only overlay targeting this Overlay onto *overlay_yaml*.

    Pass *overlays* to skip scanning overlay folders. An empty list means no
    canvas; ``None`` walks *overlay_dir* plus *extra_overlay_dirs*.
    """
    try:
        doc = yaml.safe_load(overlay_yaml) or {}
    except Exception as exc:
        _LOG.debug("skip overlay canvas apply; unreadable YAML: %s", exc)
        return overlay_yaml, None
    if not isinstance(doc, dict) or overlay_is_layout_only(doc):
        return overlay_yaml, None
    name = str((doc.get("metadata") or {}).get("name") or overlay_dir.name).strip()
    if not name:
        return overlay_yaml, None
    if overlays is None:
        dirs = [overlay_dir]
        for extra in extra_overlay_dirs or []:
            if extra.resolve() != overlay_dir.resolve():
                dirs.append(extra)
        matched = iter_layout_overlays(
            dirs, target_kind="Overlay", target_name=name, require_name=True
        )
    else:
        matched = overlays
    if not matched:
        return overlay_yaml, None
    last_path: Path | None = None
    for path, layout in matched:
        apply_document_extensions(doc, layout)
        last_path = path
    return _dump_yaml(doc), last_path


def extract_ui_canvas_overlay(
    mas_doc: dict[str, Any],
    agents: dict[str, dict[str, Any]],
    *,
    mas_name: str,
) -> dict[str, Any] | None:
    """Pull canvas ``x-*`` off MAS/agent docs into a layout-only overlay.

    Mutates *mas_doc* and *agents* to remove the extracted fields.
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
        if "x-text-input" in agent:
            text_input[agent_id] = agent.pop("x-text-input")
        spec = agent.get("spec")
        if isinstance(spec, dict) and "x-text-input" in spec:
            text_input[agent_id] = spec.pop("x-text-input")

    return _layout_overlay_doc(
        target_kind="MAS",
        target_name=mas_name,
        positions=positions,
        node_ids=node_ids,
        text_input=text_input,
    )


def extract_overlay_document_canvas(
    overlay_doc: dict[str, Any], *, overlay_name: str
) -> dict[str, Any] | None:
    """Pull canvas ``x-*`` off a spec overlay into a layout-only overlay."""
    positions = overlay_doc.pop("x-canvas-positions", None)
    node_ids = overlay_doc.pop("x-canvas-node-ids", None)
    text_input = overlay_doc.pop("x-text-input", None)
    return _layout_overlay_doc(
        target_kind="Overlay",
        target_name=overlay_name,
        positions=positions,
        node_ids=node_ids if isinstance(node_ids, dict) else None,
        text_input=text_input if isinstance(text_input, dict) else text_input,
    )


def _layout_overlay_doc(
    *,
    target_kind: str,
    target_name: str,
    positions: Any,
    node_ids: Any,
    text_input: Any,
) -> dict[str, Any] | None:
    if not positions and not node_ids and not text_input:
        return None
    overlay: dict[str, Any] = {
        "apiVersion": "mas/v1",
        "kind": "Overlay",
        "metadata": {
            "name": target_name,
            "description": (
                f"UI-only canvas layout for {target_kind} {target_name}. "
                "Ignored by the runtime."
            ),
        },
        "spec": {
            "target": {"kind": target_kind, "name": target_name},
            "patch": {},
        },
    }
    if positions is not None:
        overlay["x-canvas-positions"] = positions
    if node_ids:
        overlay["x-canvas-node-ids"] = node_ids
    if text_input:
        overlay["x-text-input"] = text_input
    return overlay


def _layout_persist_path(
    overlay_dir: Path, *, target_kind: str, target_name: str
) -> Path:
    matched = iter_layout_overlays(
        [overlay_dir],
        target_kind=target_kind,
        target_name=target_name,
        require_name=True,
    )
    if not matched and target_kind.upper() == "MAS":
        matched = iter_layout_overlays(
            [overlay_dir],
            target_kind=target_kind,
            target_name=target_name,
            require_name=False,
        )
    if matched:
        return matched[-1][0]
    dest = overlay_dir / f"{target_name}.yaml"
    if not dest.is_file():
        return dest
    loaded = _load_yaml_mapping(dest)
    if loaded is not None and overlay_is_layout_only(loaded):
        return dest
    return overlay_dir / f"{target_name}-layout.yaml"


def write_ui_canvas_overlay(
    mas_dir: Path,
    overlay: dict[str, Any],
    *,
    mas_name: str | None = None,
    target_kind: str = "MAS",
    target_name: str | None = None,
) -> Path:
    """Write *overlay* onto the layout-only overlay for the target document."""
    name = target_name or mas_name
    if not name:
        raise ValueError("target_name is required")
    overlay_dir = mas_dir / "overlays" if target_kind.upper() == "MAS" else mas_dir
    dest = _layout_persist_path(overlay_dir, target_kind=target_kind, target_name=name)
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


def persist_ui_canvas_yaml(
    mas_dir: Path,
    mas_yaml: str,
    agents: dict[str, str],
    *,
    mas_name: str,
) -> tuple[str, dict[str, str]]:
    """Move canvas ``x-*`` from MAS/agent YAML onto a layout-only Overlay."""
    try:
        mas_doc = yaml.safe_load(mas_yaml) or {}
    except Exception:
        return mas_yaml, agents
    if not isinstance(mas_doc, dict):
        return mas_yaml, agents
    parsed: dict[str, dict[str, Any]] = {}
    for name, text in agents.items():
        try:
            doc = yaml.safe_load(text) or {}
        except Exception:
            return mas_yaml, agents
        if not isinstance(doc, dict):
            return mas_yaml, agents
        parsed[name] = doc
    overlay = extract_ui_canvas_overlay(mas_doc, parsed, mas_name=mas_name)
    if overlay is None:
        return mas_yaml, agents
    write_ui_canvas_overlay(mas_dir, overlay, mas_name=mas_name)
    return _dump_yaml(mas_doc), {name: _dump_yaml(doc) for name, doc in parsed.items()}


def persist_overlay_canvas_yaml(
    overlay_dir: Path, overlay_yaml: str, *, overlay_name: str
) -> str:
    """Move canvas ``x-*`` off a spec overlay onto a layout-only Overlay."""
    try:
        doc = yaml.safe_load(overlay_yaml) or {}
    except Exception:
        return overlay_yaml
    if not isinstance(doc, dict) or overlay_is_layout_only(doc):
        return overlay_yaml
    layout = extract_overlay_document_canvas(doc, overlay_name=overlay_name)
    if layout is None:
        return overlay_yaml
    write_ui_canvas_overlay(
        overlay_dir, layout, target_kind="Overlay", target_name=overlay_name
    )
    return _dump_yaml(doc)


def _dump_yaml(doc: dict[str, Any]) -> str:
    return yaml.dump(doc, sort_keys=False, allow_unicode=True, width=100)
