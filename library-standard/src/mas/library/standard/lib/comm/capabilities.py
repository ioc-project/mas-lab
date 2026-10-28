#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Flags a link plugin declares once; the stack reads them without building it.

A plugin that declares nothing is unknown (``None``). Unknown is incompatible
with the security flags ``isolation``, ``e2e``, ``reliable``, and
``persistent_groups``: those claims need an explicit True. OR-merge is
additive — a decorator cannot turn a flag off.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

CAPABILITY_FLAGS = (
    "unicast",
    "anycast",
    "group",
    "ordered",
    "reliable",
    "e2e",
    "persistent_groups",
    "isolation",
)
SECURITY_FLAGS = frozenset({"isolation", "e2e", "reliable", "persistent_groups"})


def capability_set(**flags: bool) -> dict[str, bool]:
    """Every flag, ``False`` unless set here."""
    unknown = set(flags) - set(CAPABILITY_FLAGS)
    if unknown:
        raise ValueError(f"unknown capability flags {sorted(unknown)}")
    return {name: bool(flags.get(name, False)) for name in CAPABILITY_FLAGS}


def merge_capabilities(*sets: Mapping[str, bool] | None) -> dict[str, bool] | None:
    """OR-merge. ``None`` (unknown) makes every security flag False and stays unknown."""
    if any(caps is None for caps in sets):
        return None
    merged = capability_set()
    for caps in sets:
        for name in CAPABILITY_FLAGS:
            if caps.get(name):
                merged[name] = True
    return merged


def binding_type(binding: Any) -> str:
    if isinstance(binding, str):
        return binding.strip()
    if isinstance(binding, Mapping):
        return str(binding.get("type") or binding.get("protocol") or binding.get("kind") or "").strip()
    return ""


def binding_params(binding: Any) -> dict[str, Any]:
    if isinstance(binding, Mapping) and isinstance(binding.get("params"), Mapping):
        return dict(binding["params"])
    return {}


def declared_capabilities(binding: Any, *, registry: Any = None) -> dict[str, bool] | None:
    """Flags the plugin behind an ``agent_comm`` binding declares, or ``None``."""
    if registry is None:
        from mas.runtime.registry import get_registry

        registry = get_registry()
    kind = binding_type(binding)
    try:
        info = registry.resolve_spec("agent_comm", {"type": kind} if kind else None)
        cls = info.load_class()
    except Exception:
        return None
    hook = getattr(cls, "declared_capabilities", None)
    if callable(hook):
        flags = hook(binding_params(binding), registry=registry)
    else:
        flags = getattr(cls, "CAPABILITIES", None)
    if not isinstance(flags, Mapping):
        return None
    return merge_capabilities(flags)
