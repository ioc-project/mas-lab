#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""One composer for llm, tool, and send pipelines: ``per`` and ``priority``."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from mas.runtime.deprecation import warn_deprecated_once
from mas.runtime.engine.call_kinds import (
    normalize_call_kind,
    normalize_call_kinds,
    read_aliased_field,
    warn_legacy_key,
)
from mas.runtime.layers.base import LayerBase

logger = logging.getLogger(__name__)

LAYER_TYPE = "layer"
LEGACY_LAYER_TYPES = ("infra_middleware", "circuit_breaker")

_SHARED_PER = "all"
_DESTINATION_PER = "destination"


def layer_id(spec: dict[str, Any] | None) -> str:
    """Registry name of a pipeline step. ``middleware`` / ``id`` / ``type`` still resolve."""
    raw = spec or {}
    new = str(raw.get("layer") or "").strip()
    old = str(raw.get("middleware") or "").strip()
    if new and old and new.lower() != old.lower():
        raise ValueError("mix of 'layer' and 'middleware' is an error")
    if old and not new:
        warn_legacy_key("middleware", "layer", where=old)
        return old.lower()
    if new:
        return new.lower()
    token = str(raw.get("id") or raw.get("type") or "").strip().lower()
    return token


class LayerLoadError(ValueError):
    """A layer named in a pipeline cannot be used: it is not registered, or its module does not load."""


def _registered_layer(name: str) -> tuple[Any, str] | tuple[None, str]:
    """The registry row of layer *name* and the type it is registered under, or ``(None, "")``."""
    from mas.runtime.registry import get_registry

    registry = get_registry()
    variant = registry.resolve_by_type(LAYER_TYPE, name)
    if variant is not None:
        return variant, LAYER_TYPE
    for legacy in LEGACY_LAYER_TYPES:
        variant = registry.resolve_by_type(legacy, name)
        if variant is not None:
            warn_deprecated_once(
                f"layer-type:{legacy}:{name}",
                f"plugin type {legacy} is deprecated ({name}): register it as a layer",
            )
            return variant, legacy
    return None, ""


def _load_layer(name: str) -> type:
    """The class of layer *name*. An unregistered name or a module that fails to load raises :class:`LayerLoadError`."""
    variant, _ = _registered_layer(name)
    if variant is None:
        raise LayerLoadError(f"unknown layer {name!r}")
    try:
        return variant.load_class()
    except (ImportError, AttributeError, TypeError) as exc:
        raise LayerLoadError(f"layer {name!r} does not load: {exc}") from exc


def require_layer_class(name: str) -> type:
    """The class of layer *name* about to wrap a call. Anything that keeps it from loading raises."""
    from mas.runtime.registry import get_registry

    try:
        return _load_layer(name)
    except LayerLoadError as exc:
        if "unknown layer" not in str(exc):
            raise
        raise LayerLoadError(f"{exc}; available: {sorted(get_registry().list_names(LAYER_TYPE))}") from exc


def _layer_class(name: str) -> type | None:
    """The class of layer *name* to read its declared attributes, or ``None`` when it cannot be loaded.

    Listing and showing a pipeline must work for a layer whose library is not installed, so this does not
    raise: :func:`require_layer_class` does, when the layer is about to wrap a call.
    """
    if not name:
        return None
    try:
        return _load_layer(name)
    except LayerLoadError as exc:
        if "does not load" in str(exc):
            warn_deprecated_once(f"layer-unavailable:{name}", f"{exc}; reading its library row only")
        return None


def _layer_entry_attributes(name: str) -> dict[str, Any]:
    if not name:
        return {}
    from mas.runtime.registry import get_registry

    registry = get_registry()
    for plugin_type in (LAYER_TYPE, *LEGACY_LAYER_TYPES):
        hits = registry.entries_named(name, plugin_type)
        if hits:
            return dict(hits[0].attributes or {})
    return {}


def layers_declaring(attr: str) -> list[tuple[str, Any]]:
    """``(layer name, value)`` of every layer row whose attributes declare *attr*, in registry order.

    The runtime never names a layer. A library row says what its layer is for (``stores_llm_responses``,
    ``control_key``, ``default_steps``) and the runtime asks the registry which layers say so.
    """
    from mas.runtime.registry import get_registry

    out: list[tuple[str, Any]] = []
    for entry in get_registry().entries_of_type(LAYER_TYPE):
        value = (entry.attributes or {}).get(attr)
        if value not in (None, "", False, [], {}) and entry.shortcuts:
            out.append((str(entry.shortcuts[0]).lower(), value))
    return out


def response_store_layer() -> str:
    """The layer that stores llm responses (record and replay), as a library row declares it.

    ``LayerLoadError`` when no installed library provides one: there is no cache without that plugin.
    """
    names = [name for name, _ in layers_declaring("stores_llm_responses")]
    if not names:
        raise LayerLoadError("no installed layer declares stores_llm_responses; install a library that provides one")
    return names[0]


def _declared(name: str, attr: str, read: Callable[[type[LayerBase]], Any]) -> Any:
    """What layer *name* declares: its class (when it derives from ``LayerBase``), else its library row's *attr*."""
    cls = _layer_class(name)
    if cls is not None and issubclass(cls, LayerBase):
        value = read(cls)
        if value not in ("", (), None):
            return value
    return _layer_entry_attributes(name).get(attr)


def layer_per(spec: dict[str, Any] | None) -> str:
    """``all`` (one shared copy) or ``destination`` (one copy per leaf)."""
    raw = spec or {}
    explicit = str(raw.get("per") or "").strip().lower()
    if explicit in {_SHARED_PER, _DESTINATION_PER}:
        return explicit
    declared = str(_declared(layer_id(spec), "per", lambda c: c.per) or "").strip().lower()
    return declared if declared in {_SHARED_PER, _DESTINATION_PER} else _DESTINATION_PER


def layer_call_kinds(spec: dict[str, Any] | None) -> tuple[str, ...]:
    """Canonical ``for`` kinds (``llm``, ``tool``, ``send``). Undeclared is ``llm``."""
    raw = spec or {}
    if "for" in raw:
        return normalize_call_kinds(raw.get("for")) or ("llm",)
    return normalize_call_kinds(_declared(layer_id(spec), "call_kinds", lambda c: c.call_kinds)) or ("llm",)


def layer_priority(spec: dict[str, Any] | None) -> int:
    """Higher wraps outside. Ties break by name. Missing uses the layer's own, else 0."""
    raw = spec or {}
    if "priority" in raw:
        value = raw.get("priority")
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"layer priority must be an integer, got {value!r}")
        return value
    declared = _declared(layer_id(spec), "priority", lambda c: c.priority)
    return declared if isinstance(declared, int) and not isinstance(declared, bool) else 0


def step_id(spec: dict[str, Any] | None) -> str:
    """Name of a pipeline step, so an overlay or an experiment can address it: ``id``, else ``<layer>-<for>``."""
    raw = spec or {}
    named = str(raw.get("id") or "").strip()
    if named and (raw.get("layer") or raw.get("middleware")):
        return named
    return f"{layer_id(raw)}-{'-'.join(layer_call_kinds(raw))}"


def step_enabled(spec: dict[str, Any] | None) -> bool:
    """``enabled: false`` keeps a step listed without wrapping the call."""
    return (spec or {}).get("enabled") is not False


def stamp_layer_step(spec: dict[str, Any] | None, *, where: str = "") -> dict[str, Any]:
    """Copy a step, resolve leftover keys, and fill ``for`` / ``per`` / ``priority``."""
    step = dict(spec or {})
    mid = layer_id(step) or "layer"
    label = where or mid
    raw_for = read_aliased_field(step, "for", "applies_to", where=label)
    step.pop("applies_to", None)
    if raw_for is not None:
        step["for"] = list(normalize_call_kinds(raw_for))
    if "middleware" in step and "layer" not in step:
        warn_legacy_key("middleware", "layer", where=label)
        step["layer"] = str(step.get("middleware") or "").strip()
    elif "layer" not in step and layer_id(step):
        step["layer"] = layer_id(step)
    step.pop("middleware", None)
    step["for"] = list(layer_call_kinds(step))
    step["per"] = layer_per(step)
    step["priority"] = layer_priority(step)
    return step


def filter_pipeline_for_kind(
    pipeline: list[dict[str, Any]] | None,
    kind: str,
) -> list[dict[str, Any]]:
    """Keep steps whose ``for`` includes *kind*. Empty ``for`` is ``llm`` only."""
    want = normalize_call_kind(kind)
    if not want:
        return [dict(step) for step in (pipeline or []) if isinstance(step, dict)]
    out: list[dict[str, Any]] = []
    for step in pipeline or []:
        if not isinstance(step, dict):
            continue
        if want in layer_call_kinds(step):
            out.append(dict(step))
    return out


def order_steps(pipeline: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Highest ``priority`` first (outer). Ties by layer name."""
    steps = [dict(step) for step in (pipeline or []) if isinstance(step, dict)]
    return sorted(steps, key=lambda step: (-layer_priority(step), layer_id(step) or ""))


def split_pipeline_layers(
    pipeline: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split merged steps into common (``per: all``) vs per-destination."""
    common: list[dict[str, Any]] = []
    per_leaf: list[dict[str, Any]] = []
    for step in pipeline or []:
        if not isinstance(step, dict):
            continue
        stamped = dict(step)
        stamped["per"] = layer_per(stamped)
        target = common if stamped["per"] == _SHARED_PER else per_leaf
        target.append(stamped)
    cache_steps = [step for step in common if is_cache_layer(step)]
    if len(cache_steps) > 1:
        logger.warning(
            "infra pipeline has %s llm_cache steps; extra copies share one file and overwrite keys",
            len(cache_steps),
        )
    return common, per_leaf


def is_shared_layer(spec: dict[str, Any] | None) -> bool:
    return layer_per(spec) == _SHARED_PER


def is_cache_layer(spec: dict[str, Any] | None) -> bool:
    return bool(_declared(layer_id(spec), "stores_llm_responses", lambda c: c.stores_llm_responses))


def is_scheduler_layer(spec: dict[str, Any] | None) -> bool:
    return bool(_declared(layer_id(spec), "paces_llm_calls", lambda c: c.paces_llm_calls))


def observes_failures(spec: dict[str, Any] | None) -> bool:
    """True for a layer that must see the inner call raise (retry, circuit breaker)."""
    return bool(_declared(layer_id(spec), "observes_failures", lambda c: c.observes_failures))


def layer_role(spec: dict[str, Any] | None) -> str:
    """``transport`` (bottom), ``protocol`` (framing), or ``wrap`` (retry, cache, impair)."""
    cls = _layer_class(layer_id(spec))
    if cls is not None:
        if bool(getattr(cls, "protocol_layer", False)):
            return "protocol"
        if getattr(cls, "needs_lower", True) is False:
            return "transport"
    attrs = _layer_entry_attributes(layer_id(spec))
    if attrs.get("protocol_layer"):
        return "protocol"
    if str(attrs.get("role") or "").strip().lower() == "transport":
        return "transport"
    return "wrap"


def declared_protocol_layers(plugin_type: str, kind: str, spec: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Steps a protocol plugin lists as sugar for ``protocol: <kind>`` plus a url."""
    if not kind:
        return []
    from mas.runtime.registry import get_registry

    info = get_registry().resolve_by_type(plugin_type, kind)
    if info is None:
        return []
    try:
        cls = info.load_class()
    except (ImportError, AttributeError, TypeError):
        return []
    listed = getattr(cls, "protocol_layers", None)
    if not callable(listed):
        return []
    out: list[dict[str, Any]] = []
    for step in listed(spec) or []:
        if isinstance(step, dict):
            out.append(stamp_layer_step(step))
    return out


def merge_protocol_layers(
    pipeline: list[dict[str, Any]] | None,
    *,
    plugin_type: str,
    kind: str,
    spec: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Put the protocol's own steps in front of *pipeline* when that pipeline omitted them."""
    steps = [dict(step) for step in (pipeline or []) if isinstance(step, dict)]
    have = {layer_id(step) for step in steps}
    extra = [step for step in declared_protocol_layers(plugin_type, kind, spec) if layer_id(step) not in have]
    return extra + steps


def apply_layer(engine: Any, spec: dict[str, Any]) -> Any:
    """Wrap *engine* with one registered layer. An unknown or unloadable layer raises, never skips."""
    name = layer_id(spec)
    if not name:
        return engine
    plugin_cls = require_layer_class(name)
    wrapped = plugin_cls.wrap(engine, dict(spec.get("params") or {}), call_kinds=layer_call_kinds(spec))
    if not isinstance(wrapped, LayerBase):
        warn_deprecated_once(
            f"layer-base:{type(wrapped).__module__}.{type(wrapped).__qualname__}",
            f"layer {name!r} ({type(wrapped).__qualname__}) does not derive from LayerBase",
        )
    return wrapped


def compose(body: Any, pipeline: list[dict[str, Any]] | None, *, kind: str = "") -> Any:
    """Build the chain for one call kind. Higher priority wraps outside."""
    steps = [dict(step) for step in (pipeline or []) if isinstance(step, dict)]
    if kind:
        steps = filter_pipeline_for_kind(steps, kind)
    return wrap_call_stack(body, steps)


def wrap_call_stack(body: Any, pipeline: list[dict[str, Any]]) -> Any:
    """Run *pipeline* around one invoke body. Highest priority is outermost."""
    from mas.runtime.engine.infra_pipeline import BidirectionalPipelineEngine

    if not pipeline:
        return body
    from mas.runtime.layers.defaults import drop_default_stamp
    from mas.runtime.layers.lifecycle import call_start

    wrapped = body
    steps: list[dict[str, Any]] = []
    for spec in pipeline:
        if not isinstance(spec, dict) or not step_enabled(spec):
            continue
        step = dict(spec)
        drop_default_stamp(step)
        steps.append(step)
    if not steps:
        return body
    for spec in reversed(order_steps(steps)):
        wrapped = apply_layer(wrapped, spec)
    engine = BidirectionalPipelineEngine(inner=wrapped, pipeline_steps=list(steps))
    call_start(engine)
    return engine
