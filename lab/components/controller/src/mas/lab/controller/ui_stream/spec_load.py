#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Load a MAS topology snapshot from library files using catalog ids."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mas.lab.controller.ui_stream.run_index import overlay_rel, resolve_mas_files
from mas.lab.controller.ui_stream.spec_snapshot import load_yaml_text, snapshot_from_documents


def _read_path(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return load_yaml_text(path.read_text(encoding="utf-8"))


def snapshot_for_ref(lib_dir: Path, *, experiment: str | None, overlay: str | None) -> dict[str, Any]:
    files = resolve_mas_files(lib_dir, experiment)
    if files is None:
        raise FileNotFoundError("could not infer MAS spec from experiment id")
    manifest, mas_dir = files
    mas_doc = _read_path(manifest)
    spec = mas_doc.get("spec") if isinstance(mas_doc.get("spec"), dict) else mas_doc
    agency = (spec or {}).get("agency") or {}
    agent_docs: dict[str, dict[str, Any]] = {}
    for item in agency.get("agents") or []:
        if isinstance(item, str):
            aid = Path(item).stem
            rel = item
        elif isinstance(item, dict):
            aid = str(item.get("id") or "").strip()
            rel = str(item.get("ref") or "").strip()
        else:
            continue
        if not aid:
            continue
        if rel:
            agent_docs[aid] = _read_path(mas_dir / rel)
        else:
            agent_docs[aid] = {}
    overlay_doc = None
    overlay_path = overlay_rel(lib_dir, overlay)
    if overlay_path is not None:
        overlay_doc = load_yaml_text(overlay_path.read_text(encoding="utf-8"))
    return snapshot_from_documents(mas_doc, agent_docs, overlay_doc)
