#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Alias manifests for the runtime registry."""

from __future__ import annotations

from functools import lru_cache
from importlib import resources
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from mas.runtime.workspace_config import RuntimeWorkspaceConfig

_ALIAS_RESOURCE = "aliases.yaml"
_ALIAS_SCHEMA_RESOURCE = "aliases.schema.yaml"


def _load_resource_yaml(resource_name: str) -> dict[str, Any]:
    data = resources.files("mas.runtime").joinpath(resource_name).read_text(encoding="utf-8")
    raw = yaml.safe_load(data) or {}
    return raw if isinstance(raw, dict) else {}


def _load_alias_mapping(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    return {
        str(alias).strip().lower(): str(urn) for alias, urn in raw.items() if str(alias).strip() and str(urn).strip()
    }


def _load_alias_manifest(raw: dict[str, Any]) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    aliases = raw.get("aliases")
    if isinstance(aliases, dict):
        return _load_alias_mapping(aliases)
    spec = raw.get("spec") or {}
    if isinstance(spec, dict):
        return _load_alias_mapping(spec.get("aliases"))
    return {}


@lru_cache(maxsize=1)
def _alias_schema() -> dict[str, Any]:
    return _load_resource_yaml(_ALIAS_SCHEMA_RESOURCE)


def validate_alias_manifest(raw: Any, *, name: str = "aliases") -> dict[str, str]:
    if isinstance(raw, dict) and {"apiVersion", "kind", "metadata", "spec"}.issubset(raw):
        manifest = raw
    elif isinstance(raw, dict):
        manifest = {
            "apiVersion": "mas/v1",
            "kind": "PluginAliasManifest",
            "metadata": {"name": name},
            "spec": {"aliases": raw},
        }
    else:
        raise ValueError(f"{name}: alias manifest must be a mapping")

    try:
        Draft202012Validator(_alias_schema()).validate(manifest)
    except ValidationError as exc:
        raise ValueError(f"{name}: invalid alias manifest: {exc.message}") from exc
    return _load_alias_manifest(manifest)


def load_default_aliases() -> dict[str, str]:
    raw = _load_resource_yaml(_ALIAS_RESOURCE)
    return validate_alias_manifest(raw, name="runtime-default-aliases")


def load_config_aliases(config: RuntimeWorkspaceConfig | None = None) -> dict[str, str]:
    ws = config or RuntimeWorkspaceConfig.load()
    return validate_alias_manifest(ws.aliases, name="workspace-aliases")


def load_deprecated_aliases() -> dict[str, str]:
    """Aliases that still resolve, with a one-time warning, until a later release removes them."""
    raw = _load_resource_yaml(_ALIAS_RESOURCE)
    spec = raw.get("spec") if isinstance(raw.get("spec"), dict) else {}
    validate_alias_manifest(raw, name="runtime-default-aliases")
    return _load_alias_mapping(spec.get("deprecated_aliases"))


def load_aliases(config: RuntimeWorkspaceConfig | None = None) -> dict[str, str]:
    aliases = load_default_aliases()
    aliases.update(load_config_aliases(config))
    return aliases
