#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Resolve agent design_pattern manifest fields to registry plugin ids (compose only)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from mas.runtime.agent_defaults import default_pattern_plugin_id
from mas.runtime.spec.plugin_binding import plugin_binding_id

# Manifest ``design_pattern.type`` aliases → registry id suffix (before @v1).
_TYPE_ALIASES: dict[str, str] = {
    "reflection": "introspection",
    "plan-execute": "plan_execute",
    "plan_execute": "plan_execute",
    "tree-of-thoughts": "tree_of_thoughts",
    "tree_of_thoughts": "tree_of_thoughts",
}


def resolve_design_pattern_registry_id(design_pattern: dict[str, Any] | str | None) -> str:
    """Map agent spec.design_pattern name or {type|ref} to internal registry id."""
    binding = (
        design_pattern
        if isinstance(design_pattern, dict)
        else {"type": design_pattern}
        if isinstance(design_pattern, str)
        else {}
    )
    if not plugin_binding_id(binding, field="spec.design_pattern"):
        return default_pattern_plugin_id()
    ref = binding.get("ref")
    if isinstance(ref, str) and ref.strip():
        if "://" in ref or ref.startswith(("./", "../")):
            return default_pattern_plugin_id()
        bare = ref.split("@", 1)[0]
        return f"{bare}@v1"
    ptype = str(binding.get("type") or "react")
    normalized = _TYPE_ALIASES.get(ptype, ptype)
    return f"{normalized}@v1"


def pattern_for_agent(
    mas_config: dict[str, Any],
    agent_id: str,
    *,
    mas_base_dir: Path | None = None,
) -> str:
    """Read design_pattern from MAS entry or referenced agent manifest."""
    spec = mas_config.get("spec", mas_config)
    entries = spec.get("agents") or (spec.get("agency") or {}).get("agents") or []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name", entry.get("id", ""))
        if name != agent_id:
            continue
        dp = entry.get("design_pattern") or (entry.get("spec") or {}).get("design_pattern")
        if dp:
            return resolve_design_pattern_registry_id(dp)
        ref = entry.get("ref") or entry.get("manifest")
        if ref and mas_base_dir:
            agent_path = (mas_base_dir / ref).resolve()
            if agent_path.is_file():
                raw = yaml.safe_load(agent_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    dp = (raw.get("spec") or {}).get("design_pattern")
                    return resolve_design_pattern_registry_id(dp)
        return default_pattern_plugin_id()
    return default_pattern_plugin_id()
