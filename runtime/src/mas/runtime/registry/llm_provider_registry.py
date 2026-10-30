#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""LLM-provider registry — model → protocol-plugin routing, matching tools.

Wire protocols (``openai`` now; ``bedrock`` later) are ``llm_provider`` plugins.
``spec.models[].kind`` claims which protocol owns which model name, the same
way ``spec.providers[].kind`` claims which plugin owns which tool name.

Cache is a decorator around the routed provider, not a protocol. Offline CI
replays a recorded live provider through ``llm_cache`` (``raise_on_miss``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mas.runtime.contracts.tool_semantics import existing_attr
from mas.runtime.registry.llm_provider_protocol import require_achat_completion

_NON_PROTOCOL_KINDS = frozenset({"cache", "llm_cache", "mock"})


def leaf_llm_kind(provider: Any) -> str:
    """Walk cache wrappers / routers to the protocol or offline inner kind."""
    seen: set[int] = set()
    current = provider
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        kind = str(existing_attr(current, "kind") or existing_attr(current, "provider_id") or "").strip().lower()
        if kind in _NON_PROTOCOL_KINDS:
            current = existing_attr(current, "inner")
            continue
        if kind:
            return kind
        current = existing_attr(current, "inner")
    return ""


def llm_provider_class(kind: str) -> type:
    """Resolve ``llm_provider`` plugin kind through the plugin URN registry."""
    from mas.runtime.registry import get_registry

    info = get_registry().resolve_by_type("llm_provider", kind)
    if info is None:
        raise KeyError(f"no llm_provider plugin registered for kind {kind!r}")
    return info.load_class()


def default_llm_provider_kind() -> str:
    from mas.runtime.registry import get_registry

    return get_registry().default_for("llm_provider") or "openai"


def protocol_kind_from_infra(llm_proxy: dict[str, Any] | None) -> str:
    """Wire-protocol kind from resolved infra (never cache/mock)."""
    proxy = llm_proxy or {}
    kind = str(proxy.get("protocol") or "").strip()
    if kind.lower() in _NON_PROTOCOL_KINDS:
        kind = ""
    if not kind:
        fallback = str(proxy.get("provider") or "").strip()
        if fallback.lower() not in _NON_PROTOCOL_KINDS:
            kind = fallback
    return kind or default_llm_provider_kind()


def llm_provider_kind(llm_proxy: dict[str, Any] | None) -> str:
    """Select the wire-protocol plugin kind (infra ``protocol`` or library default)."""
    return protocol_kind_from_infra(llm_proxy)


def instantiate_llm_provider(
    kind: str,
    spec: dict[str, Any] | None = None,
) -> Any:
    """Instantiate an LLM provider plugin by kind (``from_provider_spec`` first)."""
    cls = llm_provider_class(kind)
    require_achat_completion(cls, name=kind)
    payload = dict(spec or {})
    provider: Any
    for name in ("from_provider_spec", "from_infra_spec"):
        factory = getattr(cls, name, None)
        if callable(factory):
            provider = factory(payload)
            require_achat_completion(provider, name=kind)
            return provider
    provider = cls()
    require_achat_completion(provider, name=kind)
    return provider


def canonical_model_name(model: str, leaf: dict[str, Any] | None) -> str:
    """Follow ``models.mappings`` aliases on one LLMProxy leaf (cycle is an error)."""
    mappings = dict((leaf or {}).get("mappings") or {})
    current = str(model or "").strip()
    seen: set[str] = set()
    while current and current in mappings:
        if current in seen:
            raise ValueError(f"infra model mapping cycle involving {model!r}")
        seen.add(current)
        nxt = str(mappings[current] or "").strip()
        if not nxt or nxt == current:
            break
        current = nxt
    return current


def advertised_models(leaf: dict[str, Any] | None) -> tuple[set[str], bool]:
    """Names this LLMProxy leaf claims, plus whether it is the default (``*``) owner.

    A leaf with no ``allowed`` list is the star, like a local tool provider.
    """
    raw = (leaf or {}).get("allowed") or []
    allowed = [str(item).strip() for item in raw if str(item).strip()]
    if not allowed:
        return set(), True
    names = set(allowed)
    mappings = dict((leaf or {}).get("mappings") or {})
    for src, dst in mappings.items():
        token = str(src).strip()
        if token:
            names.add(token)
        dest = str(dst or "").strip()
        if dest:
            names.add(canonical_model_name(dest, leaf))
    return names, False


def leaf_for_model(
    model: str,
    leaves: dict[str, dict[str, Any]] | None,
    *,
    default: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Pick the LLMProxy leaf that provides *model*, like tool-name routing.

    One leaf: always that leaf (``allowed`` stays informational).
    Several: ``models.allowed`` / mappings are the claim list; an empty
    ``allowed`` is the unique default owner. A named model that no leaf
    claims is an error (``default`` is not used as a silent first-leaf
    fallback). Pin-to-infra is a later change.
    """
    items = {str(k).strip(): dict(v) for k, v in (leaves or {}).items() if str(k).strip()}
    if not items:
        if default is not None:
            return dict(default)
        raise KeyError(f"no LLMProxy leaf owns model {model!r}")
    if len(items) == 1:
        return next(iter(items.values()))

    key = str(model or "").strip()
    stars: list[dict[str, Any]] = []
    owners: list[dict[str, Any]] = []
    seen: set[int] = set()
    for leaf in items.values():
        names, is_star = advertised_models(leaf)
        if is_star:
            stars.append(leaf)
            continue
        canon = canonical_model_name(key, leaf) if key else ""
        if key and (key in names or canon in names):
            ident = id(leaf)
            if ident not in seen:
                owners.append(leaf)
                seen.add(ident)
    if len(owners) > 1:
        raise ValueError(f"model {key!r} is claimed by more than one LLMProxy")
    if len(owners) == 1:
        return owners[0]
    if len(stars) > 1:
        raise ValueError("two LLMProxy leaves claim all models (empty models.allowed)")
    if len(stars) == 1:
        return stars[0]
    if default is not None and not key:
        return dict(default)
    raise KeyError(f"model {key!r} is not claimed by any of {len(items)} LLMProxy connections")


def apply_llm_endpoint_defaults(
    spec: dict[str, Any],
    llm_proxy: dict[str, Any] | None,
) -> dict[str, Any]:
    """Fill unset connection fields from the matching LLM infra leaf.

    Match is model name → LLMProxy leaf (``models.allowed`` / mappings), then
    ``spec.models[].kind`` → infra ``protocol``. Overlay/spec values win over
    infra, like ``apply_tool_server_defaults``.
    """
    proxy = dict(llm_proxy or {})
    out = dict(spec)
    if not str(out.get("api_base") or "").strip() and proxy.get("api_base"):
        out["api_base"] = proxy["api_base"]
    if not str(out.get("api_key_env") or "").strip() and proxy.get("api_key_env"):
        out["api_key_env"] = proxy["api_key_env"]
    if out.get("timeout") is None and proxy.get("timeout") is not None:
        out["timeout"] = proxy["timeout"]
    if "llm_proxy" not in out:
        out["llm_proxy"] = proxy
    if not str(out.get("kind") or "").strip():
        out["kind"] = protocol_kind_from_infra(proxy)
    return out


def iter_model_provider_specs(
    manifest: dict[str, Any] | None,
    *,
    llm_proxy: dict[str, Any] | None = None,
    llm_proxies: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Yield protocol-plugin specs from ``spec.models[]``, matching ``spec.providers[]``.

    Each entry's ``kind`` is the wire protocol. Connection comes from the
    LLMProxy leaf that claims the model name. Entries that share kind+endpoint
    become one plugin. With no ``spec.models``, each leaf claims its
    ``allowed`` list (or ``*``).
    """
    proxy = dict(llm_proxy or {})
    leaves = {str(k).strip(): dict(v) for k, v in (llm_proxies or {}).items() if str(k).strip()}
    if not leaves and proxy:
        leaves = {"default": proxy}
    default_kind = protocol_kind_from_infra(proxy)
    spec_block = (manifest or {}).get("spec") or {}
    models = spec_block.get("models") or []
    if not isinstance(models, list) or not models:
        specs: list[dict[str, Any]] = []
        for leaf in leaves.values() or [proxy]:
            leaf_dict = dict(leaf or proxy)
            kind = protocol_kind_from_infra(leaf_dict)
            claimed, is_star = advertised_models(leaf_dict)
            spec_payload: dict[str, Any] = {
                "kind": kind,
                "models": "*" if is_star or not claimed else sorted(claimed),
            }
            if leaf_dict.get("pipeline"):
                spec_payload["pipeline"] = list(leaf_dict.get("pipeline") or [])
            specs.append(apply_llm_endpoint_defaults(spec_payload, leaf_dict))
        return specs or [apply_llm_endpoint_defaults({"kind": default_kind, "models": "*"}, proxy)]

    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    for entry in models:
        if not isinstance(entry, dict):
            continue
        model = str(entry.get("model") or "").strip()
        leaf = leaf_for_model(model, leaves, default=proxy) if leaves else proxy
        leaf_dict = dict(leaf or proxy)
        kind = (
            str(
                entry.get("kind") or entry.get("protocol") or protocol_kind_from_infra(leaf_dict) or default_kind
            ).strip()
            or default_kind
        )
        if kind.lower() in _NON_PROTOCOL_KINDS:
            raise ValueError(
                f"spec.models[].kind {kind!r} is not a wire protocol; "
                "cache wraps the routed provider and is not claimed here"
            )
        conn = (kind, str(leaf_dict.get("api_base") or ""), str(leaf_dict.get("api_key_env") or ""))
        slot = groups.setdefault(
            conn,
            {
                "kind": kind,
                "leaf": leaf_dict,
                "models": [],
                "reasoning": None,
                "reasoning_effort": None,
                "extra": None,
                "pipeline": list(leaf_dict.get("pipeline") or []),
            },
        )
        if model and model not in slot["models"]:
            slot["models"].append(model)
        canon = canonical_model_name(model, leaf_dict) if model else ""
        if canon and canon not in slot["models"]:
            slot["models"].append(canon)
        if slot["reasoning"] is None and entry.get("reasoning") is not None:
            slot["reasoning"] = entry.get("reasoning")
        if slot["reasoning_effort"] is None and entry.get("reasoning_effort"):
            slot["reasoning_effort"] = entry.get("reasoning_effort")
        if slot["extra"] is None and isinstance(entry.get("extra"), dict):
            slot["extra"] = entry.get("extra")

    specs = []
    for slot in groups.values():
        spec_payload: dict[str, Any] = {"kind": slot["kind"], "models": slot["models"] or "*"}
        if slot["reasoning"] is not None:
            spec_payload["reasoning"] = slot["reasoning"]
        if slot["reasoning_effort"]:
            spec_payload["reasoning_effort"] = slot["reasoning_effort"]
        if slot["extra"] is not None:
            spec_payload["extra"] = slot["extra"]
        if slot.get("pipeline"):
            spec_payload["pipeline"] = list(slot.get("pipeline") or [])
        specs.append(apply_llm_endpoint_defaults(spec_payload, slot["leaf"]))
    return specs or [apply_llm_endpoint_defaults({"kind": default_kind, "models": "*"}, proxy)]


class LLMProviderRegistry:
    """Registry mapping model names to the protocol plugin that owns them.

    Same calling shape as :class:`ToolProviderRegistry`: register plugins, then
    ``chat_completion(model=...)`` is a lookup. ``models: "*"`` is the default
    owner for any name that was not claimed explicitly.
    """

    def __init__(self) -> None:
        self._bindings: list[tuple[Any, str | tuple[str, ...]]] = []
        self._routes: dict[str, Any] | None = None
        self._star: Any | None = None

    def register_provider(
        self,
        provider: Any,
        *,
        models: str | tuple[str, ...] | list[str] = "*",
    ) -> None:
        chat = existing_attr(provider, "chat_completion")
        if not callable(chat):
            raise TypeError(f"LLM provider must implement chat_completion, got {type(provider)}")
        require_achat_completion(provider, name=type(provider).__name__)
        claim: str | tuple[str, ...]
        if models == "*" or models is None:
            claim = "*"
        elif isinstance(models, str):
            claim = (models,)
        else:
            claim = tuple(str(n) for n in models if n)
        self._bindings.append((provider, claim))
        self._routes = None
        self._star = None

    def initialize(self) -> None:
        if self._routes is not None:
            return
        routes: dict[str, Any] = {}
        star: Any | None = None
        for provider, claim in self._bindings:
            if claim == "*":
                if star is not None and star is not provider:
                    raise ValueError("two llm_provider plugins claimed models: '*'")
                star = provider
                continue
            names = claim if isinstance(claim, tuple) else (claim,)
            for name in names:
                existing = routes.get(name)
                if existing is not None and existing is not provider:
                    raise ValueError(
                        f"model {name!r} is claimed by two llm_provider plugins "
                        f"({type(existing).__name__} and {type(provider).__name__})"
                    )
                routes[name] = provider
        self._routes = routes
        self._star = star

    def provider_for(self, model: str) -> Any:
        if self._routes is None:
            self.initialize()
        key = str(model or "").strip()
        if key and key in (self._routes or {}):
            return self._routes[key]
        if self._star is not None:
            return self._star
        if len(self._bindings) == 1:
            return self._bindings[0][0]
        raise KeyError(f"no llm_provider plugin owns model {model!r}")

    def providers(self) -> list[Any]:
        return [binding[0] for binding in self._bindings]

    @property
    def kind(self) -> str:
        if self._routes is None:
            self.initialize()
        inner = self._star or (self._bindings[0][0] if self._bindings else None)
        return str(existing_attr(inner, "kind") or existing_attr(inner, "provider_id") or "")

    def chat_completion(self, *, model: str, **kwargs: Any) -> dict[str, Any]:
        return self.provider_for(model).chat_completion(model=model, **kwargs)

    async def achat_completion(self, *, model: str, **kwargs: Any) -> dict[str, Any]:
        return await self.provider_for(model).achat_completion(model=model, **kwargs)


def enforce_llm_provider_async_contract(reg: Any) -> None:
    """Fail plugin load if any ``llm_provider`` class omits ``achat_completion``."""
    from mas.runtime.registry import PluginUnavailable

    for entry in reg.get_by_category("llm_provider"):
        try:
            info = entry.resolve()
        except PluginUnavailable:
            continue
        require_achat_completion(info.load_class(), name=entry.urn)


def llm_providers_from_manifest(
    manifest: dict[str, Any] | None,
    *,
    llm_proxy: dict[str, Any] | None = None,
    llm_proxies: dict[str, dict[str, Any]] | None = None,
    stream: bool = False,
    reasoning_effort: str | None = None,
    reasoning: dict[str, Any] | None = None,
) -> Any:
    """Instantiate protocol plugins from ``spec.models[]`` + infra, like tools.

    One plugin per distinct kind+endpoint. A single plugin is returned
    directly; two or more are wrapped in :class:`LLMProviderRegistry`.
    """
    specs = iter_model_provider_specs(manifest, llm_proxy=llm_proxy, llm_proxies=llm_proxies)
    bindings: list[tuple[Any, str | tuple[str, ...] | list[str]]] = []
    from mas.runtime.engine.infra_pipeline import (
        filter_pipeline_for_kind,
        split_pipeline_layers,
        wrap_llm_provider_pipeline,
    )

    for spec in specs:
        kind = str(spec.get("kind") or "").strip()
        if not kind:
            raise ValueError("spec.models entry is missing kind")
        payload = dict(spec)
        payload["stream"] = stream
        if reasoning is not None:
            payload.setdefault("reasoning", reasoning)
        if reasoning_effort:
            payload.setdefault("reasoning_effort", reasoning_effort)
        pipeline = list(payload.pop("pipeline", None) or [])
        if not pipeline:
            pipeline = list((payload.get("llm_proxy") or {}).get("pipeline") or [])
        _, per_leaf = split_pipeline_layers(pipeline)
        provider = wrap_llm_provider_pipeline(
            instantiate_llm_provider(kind, payload),
            filter_pipeline_for_kind(per_leaf, "llm"),
        )
        bindings.append((provider, spec.get("models", "*")))
    if not bindings:
        raise ValueError("no llm_provider plugin could be instantiated")
    if len(bindings) == 1:
        return bindings[0][0]
    registry = LLMProviderRegistry()
    for provider, models in bindings:
        registry.register_provider(provider, models=models)
    registry.initialize()
    return registry


def wrap_llm_provider_cache(
    inner: Any,
    *,
    cache_path: Path | None = None,
    cache_read: bool = True,
    cache_write: bool = True,
    cache_raise_on_miss: bool = False,
) -> Any:
    """Decorator: disk cache in front of a routed protocol plugin (not a protocol)."""
    if inner is None:
        return inner
    kind = str(existing_attr(inner, "kind") or existing_attr(inner, "provider_id") or "").strip().lower()
    if kind in _NON_PROTOCOL_KINDS:
        return inner
    return instantiate_llm_provider(
        "cache",
        {
            "inner": inner,
            "cache_path": cache_path,
            "allow_read": cache_read,
            "allow_write": cache_write,
            "raise_on_miss": cache_raise_on_miss,
        },
    )


def llm_provider_from_infra(
    llm_proxy: dict[str, Any] | None,
    *,
    llm_proxies: dict[str, dict[str, Any]] | None = None,
    stream: bool = False,
    reasoning_effort: str | None = None,
    reasoning: dict[str, Any] | None = None,
    cache_path: Path | None = None,
    wrap_cache: bool = False,
    cache_read: bool = True,
    cache_write: bool = True,
    cache_raise_on_miss: bool = False,
    manifest: dict[str, Any] | None = None,
) -> Any:
    """Build the routed protocol provider, optionally wrapping it with cache."""
    inner = llm_providers_from_manifest(
        manifest,
        llm_proxy=llm_proxy,
        llm_proxies=llm_proxies,
        stream=stream,
        reasoning_effort=reasoning_effort,
        reasoning=reasoning,
    )
    if not wrap_cache:
        return inner
    return wrap_llm_provider_cache(
        inner,
        cache_path=cache_path,
        cache_read=cache_read,
        cache_write=cache_write,
        cache_raise_on_miss=cache_raise_on_miss,
    )
