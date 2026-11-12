#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Resolve and instantiate comm layers from ``{type, params}`` bindings."""

from __future__ import annotations

import inspect
from typing import Any


def layer_binding(item: dict[str, Any]) -> dict[str, Any]:
    """``{type, params}`` from a binding that may spell the type as ``protocol`` or ``kind``."""
    return {
        "type": item.get("type") or item.get("protocol") or item.get("kind"),
        "params": dict(item.get("params") or {}),
    }


def resolve_layer_class(kind: str, binding: dict[str, Any], registry: Any = None) -> type:
    """The class for one ``{type, params}`` binding: a plugin of ``kind`` first, then a ``stack_layer`` plugin.

    A link (an ``agent_comm`` plugin, the transport) and a generic layer
    (a ``stack_layer`` plugin) have distinct ids, so the lookup does not depend on where
    the entry sits in a stack. ``agent_comm/stack`` layers and the chains a Flavour binds
    resolve alike.
    """
    if registry is None:
        from mas.runtime.registry import get_registry

        registry = get_registry()
    try:
        info = registry.resolve_spec(kind, binding)
    except KeyError:
        try:
            info = registry.resolve_spec("layer", binding)
        except KeyError:
            info = registry.resolve_spec("stack_layer", binding)
    return info.load_class()


def accepted_kwargs(cls: type, extra: dict[str, Any]) -> dict[str, Any]:
    """The entries of ``extra`` that ``cls.__init__`` names. ``**kwargs`` does not take the others."""
    parameters = inspect.signature(cls.__init__).parameters
    return {key: value for key, value in extra.items() if key in parameters and key != "self"}


def instantiate_layer(kind: str, item: Any, extra: dict[str, Any]) -> Any:
    """Build one layer from a live object or a ``{type, params}`` binding."""
    if not isinstance(item, dict):
        return item
    extra = dict(extra)
    registry = extra.pop("registry", None)
    binding = layer_binding(item)
    cls = resolve_layer_class(kind, binding, registry)
    kwargs = dict(binding.get("params") or {})
    kwargs.update(accepted_kwargs(cls, extra))
    return cls(**kwargs)


def instantiate_layers(kind: str, items: list[Any], extra: dict[str, Any]) -> list[Any]:
    return [instantiate_layer(kind, item, extra) for item in items]
