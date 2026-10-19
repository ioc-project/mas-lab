#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Load YAML layouts from the demo-ui package (include expansion)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from mas.lab.controller.ui_stream.refs import DataSourceRef, ref_from_mapping


def load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"layout {path} must be a mapping")
    return data


def _resolve_layout_file(layouts_root: Path | list[Path], name: str) -> Path:
    roots = layouts_root if isinstance(layouts_root, list) else [layouts_root]
    wanted = [name, f"{name}.yaml"] if not str(name).endswith(".yaml") else [name]
    for root in roots:
        for item in wanted:
            for path in (root / item, root / "examples" / item):
                if path.is_file():
                    return path
    raise FileNotFoundError(f"layout {name!r} not found")


def _deep_merge(base: Any, extra: Any) -> Any:
    if not isinstance(base, dict) or not isinstance(extra, dict):
        return extra
    out = dict(base)
    for key, value in extra.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def expand_lanes(doc: dict[str, Any]) -> dict[str, Any]:
    """Tile ``ref`` / ``pane`` once per ``lanes`` entry (side-by-side compare)."""
    lanes = doc.get("lanes")
    template = doc.get("ref") or doc.get("pane")
    if not lanes or not template:
        return doc
    if not isinstance(lanes, list):
        raise ValueError("lanes must be a list")
    children: list[dict[str, Any]] = []
    for lane in lanes:
        if not isinstance(lane, dict):
            raise ValueError("each lane must be a mapping")
        child: dict[str, Any] = {
            "type": "panel",
            "size": lane.get("size") or "1fr",
            "include": template,
            "dataSource": dict(lane.get("dataSource") or {}),
        }
        if lane.get("title"):
            child["title"] = lane["title"]
        if lane.get("override"):
            child["override"] = lane["override"]
        children.append(child)
    out = dict(doc)
    out.pop("lanes", None)
    out.pop("ref", None)
    out.pop("pane", None)
    out["layout"] = {"type": "hstack", "children": children}
    return out


def expand_includes(node: Any, layouts_root: Path | list[Path], inherited: DataSourceRef) -> Any:
    if isinstance(node, list):
        return [expand_includes(item, layouts_root, inherited) for item in node]
    if not isinstance(node, dict):
        return node
    merged = inherited.merge(ref_from_mapping(node.get("dataSource")))
    if "include" in node:
        included_path = _resolve_layout_file(layouts_root, str(node["include"]))
        included = load_yaml(included_path)
        subtree = included.get("layout") if "layout" in included else included
        override = node.get("override")
        if isinstance(override, dict) and isinstance(subtree, dict):
            subtree = _deep_merge(subtree, override)
        wrapped = dict(node)
        wrapped.pop("include", None)
        wrapped.pop("ref", None)
        wrapped.pop("override", None)
        if isinstance(subtree, dict):
            child = expand_includes(subtree, layouts_root, merged)
            wrapped["children"] = [child]
            wrapped.setdefault("type", "panel")
            wrapped["dataSource"] = merged.as_dict()
            return expand_includes(wrapped, layouts_root, merged)
        wrapped["children"] = expand_includes(subtree, layouts_root, merged)
        wrapped["dataSource"] = merged.as_dict()
        return wrapped
    out = dict(node)
    if "dataSource" in node or any(k in node for k in ("children", "content", "layout")):
        out["dataSource"] = merged.as_dict()
    if "children" in node:
        out["children"] = expand_includes(node["children"], layouts_root, merged)
    if "content" in node:
        out["content"] = expand_includes(node["content"], layouts_root, merged)
    if "layout" in node:
        out["layout"] = expand_includes(node["layout"], layouts_root, merged)
    return out


def layout_group(path: Path, root: Path) -> str:
    try:
        rel = path.resolve().relative_to(root.resolve())
    except ValueError:
        rel = Path(path.name)
    if "examples" in rel.parts:
        return "examples"
    marker = root.resolve()
    if marker.name == "layouts":
        marker = marker.parent
    if marker.name == "demo-ui":
        marker = marker.parent
    name = marker.name.strip() or "layouts"
    return "examples" if name in {".", "mas-lab", "mas-lab-demo-ui"} else name


def list_layout_catalog(roots: list[Path]) -> list[dict[str, Any]]:
    """Title + slug rows for the root gallery and the layout picker."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for root in roots:
        for path in list(root.glob("*.yaml")) + list(root.glob("examples/*.yaml")):
            if path.name.startswith("overlay-") or path.name in seen:
                continue
            seen.add(path.name)
            title = path.stem.replace("-", " ")
            try:
                doc = load_yaml(path)
                raw = str(doc.get("title") or "").strip()
                if raw:
                    title = raw
            except Exception:
                pass
            rows.append(
                {
                    "slug": path.name,
                    "title": title,
                    "group": layout_group(path, root),
                }
            )
    rows.sort(key=lambda row: (str(row.get("group") or ""), str(row.get("title") or "").lower()))
    return rows


def load_layout(layouts_root: Path | list[Path], name: str) -> dict[str, Any]:
    path = _resolve_layout_file(layouts_root, name)
    doc = expand_lanes(load_yaml(path))
    root_ref = ref_from_mapping(doc.get("dataSource"))
    return expand_includes(doc, layouts_root, root_ref)
