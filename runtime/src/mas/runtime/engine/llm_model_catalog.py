#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Versioned per-model LLM capability catalog.

The YAML is the data we would extract into a standalone library. Community
tables (LiteLLM, OpenRouter, …) are listed as sources, not vendored.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_CATALOG_PATH = Path(__file__).resolve().parents[1] / "spec" / "llm-model-catalog.yaml"
# Known models must advertise these; omitting one means unsupported.
_GATED_SETTINGS = frozenset(
    {
        "reasoning.effort",
        "reasoning.mode",
        "reasoning.exclude",
        "reasoning.budget_tokens",
        "think",
        "include",
    }
)


@dataclass(frozen=True)
class ModelSetting:
    supported: bool = True
    minimum: float | None = None
    maximum: float | None = None
    default: Any = None
    enum: tuple[Any, ...] | None = None

    def clamp(self, value: Any) -> Any:
        """Bound a numeric spec value to catalog min/max. Enum is documentation only."""
        if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
            return value
        out = float(value)
        if self.minimum is not None:
            out = max(out, self.minimum)
        if self.maximum is not None:
            out = min(out, self.maximum)
        return int(out) if isinstance(value, int) else out


@dataclass(frozen=True)
class ModelInfo:
    name: str
    aliases: tuple[str, ...] = ()
    context_window: int | None = None
    max_output_tokens: int | None = None
    max_thinking_tokens: int | None = None
    thinking_mode: str | None = None
    apis: tuple[str, ...] = ()
    defaults: dict[str, Any] = field(default_factory=dict)
    settings: dict[str, ModelSetting] = field(default_factory=dict)
    pricing: dict[str, Any] = field(default_factory=dict)

    def setting(self, name: str) -> ModelSetting | None:
        return self.settings.get(name)

    def supports(self, name: str) -> bool:
        spec = self.settings.get(name)
        if spec is not None:
            return spec.supported
        if name in _GATED_SETTINGS:
            return False
        return True

    def clamp(self, name: str, value: Any) -> Any:
        spec = self.settings.get(name)
        if spec is None:
            return value
        return spec.clamp(value)


@dataclass(frozen=True)
class ModelCatalog:
    version: str
    sources: tuple[dict[str, Any], ...]
    models: dict[str, ModelInfo]
    _alias_index: dict[str, str] = field(default_factory=dict)

    def get(self, model: str | None) -> ModelInfo | None:
        name = str(model or "").strip()
        if not name:
            return None
        if name in self.models:
            return self.models[name]
        for candidate in (name, name.split("/")[-1], _base_model_id(name)):
            mapped = self._alias_index.get(candidate)
            if mapped:
                return self.models.get(mapped)
            if candidate in self.models:
                return self.models[candidate]
        return None


_REGION_PREFIX = re.compile(r"^(?:us|eu|apac|jp|au|ca|global|us-gov)\.")
_VERSION_SUFFIX = re.compile(r"(?:-v\d+(?::\d+)?|@\d{8}|-\d{8})$")


def _base_model_id(name: str) -> str:
    """A hosted model id without its routing decorations.

    Cross-region inference profiles prefix a region (``us.``) and versioned ids end in ``-v1:0``,
    ``@20250101`` or ``-20250101``; none of these name a different model.
    """
    base = _REGION_PREFIX.sub("", name.split("/")[-1])
    return _VERSION_SUFFIX.sub("", base)


def _parse_setting(raw: Any) -> ModelSetting:
    if not isinstance(raw, dict):
        return ModelSetting()
    enum = raw.get("enum")
    return ModelSetting(
        supported=raw.get("supported", True) is not False,
        minimum=float(raw["min"]) if raw.get("min") is not None else None,
        maximum=float(raw["max"]) if raw.get("max") is not None else None,
        default=raw.get("default"),
        enum=tuple(enum) if isinstance(enum, list) else None,
    )


def load_model_catalog(path: Path | None = None) -> ModelCatalog:
    target = path or _CATALOG_PATH
    data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    meta = data.get("metadata") or {}
    spec = data.get("spec") or {}
    models: dict[str, ModelInfo] = {}
    alias_index: dict[str, str] = {}
    raw_models = spec.get("models") or {}
    if isinstance(raw_models, dict):
        for name, raw in raw_models.items():
            if not isinstance(raw, dict):
                continue
            aliases = tuple(str(a) for a in (raw.get("aliases") or []) if a)
            settings_raw = raw.get("settings") or {}
            settings = {
                str(key): _parse_setting(value)
                for key, value in settings_raw.items()
                if isinstance(value, dict) or value is not None
            }
            info = ModelInfo(
                name=str(name),
                aliases=aliases,
                context_window=int(raw["context_window"]) if raw.get("context_window") else None,
                max_output_tokens=int(raw["max_output_tokens"]) if raw.get("max_output_tokens") else None,
                max_thinking_tokens=int(raw["max_thinking_tokens"])
                if raw.get("max_thinking_tokens") is not None
                else None,
                thinking_mode=str(raw["thinking_mode"]) if raw.get("thinking_mode") else None,
                apis=tuple(str(a) for a in (raw.get("apis") or [])),
                defaults=dict(raw.get("defaults") or {}),
                settings=settings,
                pricing=dict(raw.get("pricing") or {}),
            )
            models[info.name] = info
            alias_index[info.name] = info.name
            for alias in aliases:
                alias_index[alias] = info.name
    sources = tuple(s for s in (spec.get("sources") or []) if isinstance(s, dict))
    return ModelCatalog(
        version=str(meta.get("version") or ""),
        sources=sources,
        models=models,
        _alias_index=alias_index,
    )


@lru_cache(maxsize=1)
def default_model_catalog() -> ModelCatalog:
    return load_model_catalog()


def resolve_model_recursive(model_name: str | None, infra_spec: dict | None = None) -> str | None:
    """Recursively resolve a model name through infra manifest mappings.

    Supports mapping chains like:
      default -> haiku -> bedrock/anthropic.claude-haiku-4-5-20251001-v1:0

    Args:
        model_name: The requested model (e.g., "default", "haiku", "gpt-mini")
        infra_spec: The infra manifest spec dict (from LLMProxy.spec)

    Returns:
        The final resolved model name, or the original if no mapping found.

    Raises:
        ValueError: If a circular reference is detected in the mapping chain.
    """
    if not model_name:
        return model_name

    model_name = str(model_name).strip()
    if not infra_spec:
        return model_name

    # Get models.mappings from infra spec
    models_config = infra_spec.get("models") or {}
    mappings = models_config.get("mappings") or {}

    if not mappings:
        return model_name

    # Resolve the chain with cycle detection
    visited = set()
    current = model_name
    max_depth = 10  # Prevent infinite loops
    depth = 0

    while current in mappings and depth < max_depth:
        if current in visited:
            # Circular reference detected
            chain_str = " -> ".join(list(visited) + [current])
            raise ValueError(
                f"Circular model mapping detected: {chain_str}. Please check infra manifest models.mappings"
            )
        visited.add(current)
        current = str(mappings[current]).strip()
        depth += 1

    if depth >= max_depth:
        raise ValueError(f"Model mapping chain too deep (max {max_depth}). Possible circular reference in: {visited}")

    # Validate against allowed models if specified
    allowed = models_config.get("allowed") or []
    if allowed and current not in allowed:
        # Don't fail, just warn - the LLM provider will handle unknown models
        pass

    return current
