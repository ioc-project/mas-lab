#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Normalize overlay documents to mas/v1 kind: Overlay before validate/merge."""

from __future__ import annotations

from typing import Any


def normalize_overlay(data: dict[str, Any], *, name: str = "overlay") -> dict[str, Any]:
    """Normalize legacy and canonical overlays to ``mas/v1`` ``Overlay`` shape.

    This function is intentionally validation-oriented: it keeps legacy overlays
    loadable by coercing common historical shapes into a schema-valid envelope.
    """
    if not isinstance(data, dict):
        raise ValueError("overlay must be a mapping")

    kind = str(data.get("kind") or "")
    if kind != "Overlay":
        raise ValueError(
            f"unsupported overlay shape (kind={kind!r}); expected apiVersion: mas/v1, kind: Overlay"
        )

    out = dict(data)
    out["apiVersion"] = "mas/v1"

    md = out.get("metadata")
    if not isinstance(md, dict):
        md = {}
    md_name = md.get("name") or md.get("id") or name
    md_desc = md.get("description", "")
    out["metadata"] = {"name": str(md_name), "description": str(md_desc)}

    spec = out.get("spec")
    if not isinstance(spec, dict):
        raise ValueError("Overlay spec must be a mapping")

    target = spec.get("target") if isinstance(spec.get("target"), dict) else None
    patch = spec.get("patch")
    if not isinstance(patch, dict):
        patch = {}
    overrides = spec.get("overrides", [])
    if not isinstance(overrides, list) or not all(isinstance(item, str) for item in overrides):
        raise ValueError("Overlay spec.overrides must be a list of PATH=VALUE strings")

    # Fold legacy top-level spec keys into patch if they are not already present.
    for k, v in spec.items():
        if k in {"target", "patch", "overrides"}:
            continue
        patch.setdefault(k, v)

    if not isinstance(target, dict):
        target = {}
    if not target.get("kind"):
        # Don't infer target kind — require explicit spec.target.kind
        # Overlays without a target will fail later in merge_overlay validation
        pass

    # Coerce legacy MAS-level design_pattern to moderator agent patch.
    if target.get("kind") == "MAS" and "design_pattern" in patch:
        agents = patch.get("agents")
        if not isinstance(agents, dict):
            agents = {}
        mod = agents.get("moderator")
        if not isinstance(mod, dict):
            mod = {}
        mod.setdefault("design_pattern", patch.get("design_pattern"))
        agents["moderator"] = mod
        patch["agents"] = agents
        patch.pop("design_pattern", None)

    # Preserve unsupported legacy fields as extension fields for strict schema.
    for legacy_key in ("plugins",):
        if legacy_key in patch:
            patch[f"x-{legacy_key}"] = patch.pop(legacy_key)

    if not isinstance(patch, dict):
        raise ValueError("Overlay spec.patch is required and must be a mapping")
    out["spec"] = {"target": target, "patch": patch}
    if overrides:
        out["spec"]["overrides"] = list(overrides)
    return out
