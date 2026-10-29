#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Simplified infra/v1 models for v2 ctl (aligned with mas-lab InfraManifest)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _providers_from_document(raw: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for name, leaf in raw.items():
        token = str(name).strip()
        if token and isinstance(leaf, dict):
            out[token] = dict(leaf)
    return out


@dataclass
class ProxySpec:
    api_base: str = ""
    api_key_env: str = "OPENAI_API_KEY"
    timeout: float | None = None


@dataclass
class ModelDefaults:
    llm: str | None = None
    embed: str | None = None


@dataclass
class ModelsSpec:
    allowed: list[str] = field(default_factory=list)
    default_llm: str | None = None
    default_embed: str | None = None
    mappings: dict[str, str] = field(default_factory=dict)
    generation: dict[str, Any] = field(default_factory=dict)

    @property
    def defaults(self) -> ModelDefaults:
        return ModelDefaults(llm=self.default_llm, embed=self.default_embed)

    def resolve(self, model: str) -> str:
        return self.mappings.get(model, model)


@dataclass
class InfraManifest:
    name: str = ""
    kind: str = ""
    proxy: ProxySpec = field(default_factory=ProxySpec)
    models: ModelsSpec = field(default_factory=ModelsSpec)
    model_access: dict[str, Any] = field(default_factory=dict)
    protocol: str = ""
    pipeline: list[dict[str, Any]] = field(default_factory=list)
    runtime_engine: dict[str, Any] = field(default_factory=dict)
    tool_servers: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    applications: dict[str, dict[str, Any]] = field(default_factory=dict)
    providers: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | str) -> InfraManifest:
        from mas.ctl.infra.env_resolve import resolve_manifest_values
        from mas.runtime.spec.source import load_yaml_file

        p = Path(path).resolve()
        data = load_yaml_file(p)
        if not isinstance(data, dict):
            raise ValueError(f"{p}: expected mapping")
        return cls.from_document(resolve_manifest_values(data))

    @classmethod
    def from_document(cls, data: dict[str, Any]) -> InfraManifest:
        """Rebuild an effective infra model from a schema-shaped document."""
        meta = data.get("metadata") or {}
        spec = data.get("spec") or {}
        kind = data.get("kind", "")
        applications: dict[str, dict[str, Any]] = {}
        raw_endpoints = spec.get("endpoints") or {}
        if isinstance(raw_endpoints, dict):
            applications = {
                str(name): dict(endpoint) if isinstance(endpoint, dict) else {"url": str(endpoint)}
                for name, endpoint in raw_endpoints.items()
            }
        proxy_raw = spec.get("proxy") or spec.get("server") or {}
        models_raw = spec.get("models") or {}
        defaults = models_raw.get("defaults") or spec.get("defaults") or {}
        if "api_key_env" in proxy_raw:
            api_key_env = str(proxy_raw.get("api_key_env") or "")
        else:
            api_key_env = "OPENAI_API_KEY"
        return cls(
            name=str(meta.get("name", "")),
            kind=kind,
            proxy=ProxySpec(
                api_base=str(proxy_raw.get("api_base", "") or ""),
                api_key_env=api_key_env,
                timeout=float(proxy_raw["timeout"]) if proxy_raw.get("timeout") is not None else None,
            ),
            models=ModelsSpec(
                allowed=list(models_raw.get("allowed") or models_raw.get("available") or []),
                default_llm=defaults.get("llm"),
                default_embed=defaults.get("embed") or defaults.get("embedding"),
                mappings=dict(models_raw.get("mappings") or {}),
                generation=dict(models_raw.get("generation") or {}),
            ),
            model_access=dict(spec.get("model_access") or {}),
            protocol=str(spec.get("protocol") or "").strip(),
            tool_servers=[dict(item) for item in (spec.get("tool_servers") or []) if isinstance(item, dict)],
            pipeline=[dict(item) for item in (spec.get("interceptors") or []) if isinstance(item, dict)],
            runtime_engine=dict(spec.get("runtime_engine") or {}),
            raw=data,
            applications=applications,
            providers=_providers_from_document(spec.get("providers")),
        )

    def to_document(self) -> dict[str, Any]:
        """Serialize the effective infra model for schema-backed CLI patching."""
        proxy: dict[str, Any] = {
            "api_base": self.proxy.api_base,
            "api_key_env": self.proxy.api_key_env,
        }
        if self.proxy.timeout is not None:
            proxy["timeout"] = self.proxy.timeout
        defaults: dict[str, Any] = {}
        if self.models.default_llm is not None:
            defaults["llm"] = self.models.default_llm
        if self.models.default_embed is not None:
            defaults["embed"] = self.models.default_embed
        models: dict[str, Any] = {
            "allowed": list(self.models.allowed),
            "mappings": dict(self.models.mappings),
            "generation": dict(self.models.generation),
        }
        if defaults:
            models["defaults"] = defaults
        spec: dict[str, Any] = {
            "proxy": proxy,
            "models": models,
            "model_access": dict(self.model_access),
            "tool_servers": list(self.tool_servers),
            "endpoints": {str(name): dict(endpoint) for name, endpoint in self.applications.items()},
            "interceptors": self._document_interceptors(),
            "runtime_engine": dict(self.runtime_engine),
        }
        if self.providers:
            spec["providers"] = {str(name): dict(leaf) for name, leaf in self.providers.items()}
        if self.protocol:
            spec["protocol"] = self.protocol
        return {
            "apiVersion": "infra/v1",
            "kind": self.kind or "InfraBundle",
            "metadata": {"name": self.name},
            "spec": spec,
        }

    def _document_interceptors(self) -> list[dict[str, Any]]:
        """Shared steps, plus cache steps hoisted from leaves for CLI addressing.

        Non-cache leaf steps stay under ``spec.providers`` so an override does
        not clone ``fault_inject`` meant for one leaf onto every leaf.
        """
        interceptors = [dict(item) for item in self.pipeline]
        from mas.runtime.engine.infra_pipeline import is_cache_middleware

        have_cache = any(is_cache_middleware(step) for step in interceptors)
        if have_cache:
            return interceptors
        for leaf in self.providers.values():
            for step in (leaf or {}).get("pipeline") or []:
                if isinstance(step, dict) and is_cache_middleware(step):
                    interceptors.append(dict(step))
                    return interceptors
        return interceptors

    def to_llm_proxy_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "api_base": self.proxy.api_base,
            "api_key_env": self.proxy.api_key_env,
            "default_model": self.models.default_llm,
            "mappings": dict(self.models.mappings),
            "allowed": list(self.models.allowed),
            "model_access": dict(self.model_access),
            "pipeline": list(self.pipeline),
        }
        if self.proxy.timeout is not None:
            out["timeout"] = self.proxy.timeout
        if self.models.generation:
            out["generation"] = dict(self.models.generation)
        if self.protocol:
            out["protocol"] = self.protocol
        return out

    def to_leaf_dict(self) -> dict[str, Any]:
        """Connection fields for one LLMProxy leaf (no shared pipeline)."""
        out = self.to_llm_proxy_dict()
        out.pop("pipeline", None)
        return out
