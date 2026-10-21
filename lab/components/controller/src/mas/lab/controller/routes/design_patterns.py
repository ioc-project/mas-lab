#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Design-pattern catalog endpoint for the agent / overlay canvas builders."""

from __future__ import annotations

import logging

from fastapi import APIRouter
from mas.lab.controller.routes._api import deps

logger = logging.getLogger(__name__)

router = APIRouter()

# Config keys advertised to the UI. Sourced from the design-pattern-config
# fragment (``mas/v1#design-pattern-config``); the constant below is a fallback
# for deployments that don't ship the runtime schema docs.
_CONFIG_KEYS_FALLBACK = (
    "max_steps",
    "max_rounds",
    "critique_turns",
    "enable_peer_context",
    "parallel",
)


def _config_keys() -> list[str]:
    """Property names from the design-pattern-config fragment (best effort)."""
    try:
        from mas.lab.schemas.paths import runtime_schema_dir
        from mas.runtime.spec.source import load_yaml_file

        path = runtime_schema_dir() / "fragments" / "design-pattern-config.schema.yaml"
        doc = load_yaml_file(path)
        keys = list((doc.get("properties") or {}).keys())
        if keys:
            return keys
    except Exception as exc:  # pragma: no cover - schema is optional at runtime
        logger.debug("design-pattern-config fragment unavailable: %s", exc)
    return list(_CONFIG_KEYS_FALLBACK)


def _pattern_type(urn: str) -> str:
    """Manifest name for ``spec.design_pattern.type`` — final URN segment.

    e.g. ``mas.dp.deterministic_staged_debate`` -> ``deterministic_staged_debate``.
    """
    return urn.rsplit(".", 1)[-1] if "." in urn else urn


def _library_of(module: str) -> str:
    """Grouping key derived from the plugin module (OSS vs experimental).

    ``mas.library.standard.plugins...`` -> ``standard``
    ``mas.library.lab.plugins...``      -> ``lab``
    """
    parts = module.split(".")
    if len(parts) >= 3 and parts[0] == "mas" and parts[1] == "library":
        return parts[2]
    return "unknown"


def _label(type_name: str, description: str) -> str:
    """Human label: registry description if present, else a titleized name."""
    desc = (description or "").strip()
    if desc:
        return desc.splitlines()[0]
    return type_name.replace("_", " ").title()


def _resolve_default(raw: str) -> str:
    """Map the default binding (e.g. ``react@v1``) onto a pattern ``type``."""
    if not raw:
        return ""
    try:
        from mas.runtime.registry import get_registry

        registry = get_registry()
        urn = registry.urn_for(raw) or registry.urn_for(raw.split("@", 1)[0])
        if urn:
            return _pattern_type(urn)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("could not resolve default design pattern %r: %s", raw, exc)
    return raw.split("@", 1)[0]


@router.get("/api/design-patterns", tags=["Registry"])
async def list_design_patterns():
    """Design-pattern plugins for the agent / overlay canvas builders.

    Reshapes ``LabRegistry.list("design_pattern")`` into a UI-friendly catalog:
    each entry carries a manifest ``type``, a display ``label``, the source
    ``library`` (so the UI can group OSS vs experimental patterns) and the
    ``config_keys`` a pattern may accept.
    """
    from mas.lab.controller.lab_registry import get_lab_registry

    ws = getattr(deps.get_manifest_store(), "_workspace", None)
    reg = get_lab_registry(ws)

    config_keys = _config_keys()
    patterns = []
    for entry in reg.list("design_pattern"):
        urn = entry.get("urn", "")
        type_name = _pattern_type(urn)
        patterns.append(
            {
                "type": type_name,
                "label": _label(type_name, entry.get("description", "")),
                "description": (entry.get("description") or "").strip(),
                "library": _library_of(entry.get("module", "")),
                "config_keys": config_keys,
            }
        )

    # Standard (OSS) patterns first, then everything else, alphabetically.
    patterns.sort(key=lambda p: (p["library"] != "standard", p["type"]))

    default_binding = (reg.agent_defaults().get("design_pattern") or {}).get("type", "")
    return {"patterns": patterns, "default": _resolve_default(default_binding)}
