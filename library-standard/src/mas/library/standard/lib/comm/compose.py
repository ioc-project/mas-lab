#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Instantiate and bind a list of comm layers: link at the bottom, bind above."""

from __future__ import annotations

import inspect
from typing import Any


def instantiate_layer(kind: str, item: Any, extra: dict[str, Any]) -> Any:
    """Build one layer from a live object or a ``{type, params}`` binding."""
    if not isinstance(item, dict):
        return item
    extra = dict(extra)
    from mas.runtime.registry import get_registry

    registry = extra.pop("registry", None) or get_registry()
    binding = {
        "type": item.get("type") or item.get("protocol") or item.get("kind"),
        "params": dict(item.get("params") or {}),
    }
    info = registry.resolve_spec(kind, binding)
    cls = info.load_class()
    kwargs = dict(binding.get("params") or {})
    signature = inspect.signature(cls.__init__)
    for key, value in extra.items():
        if key in signature.parameters:
            kwargs[key] = value
    return cls(**kwargs)


def instantiate_layers(kind: str, items: list[Any], extra: dict[str, Any]) -> list[Any]:
    return [instantiate_layer(kind, item, extra) for item in items]


def bind_bottom_up(layers: list[Any]) -> None:
    """Each layer above the bottom binds the layer below it."""
    from mas.library.standard.plugins.agentcomm.layer import BoundLayer

    if not layers:
        raise ValueError("stack requires at least one layer")
    bottom = layers[0]
    if not callable(getattr(bottom, "send", None)):
        raise ValueError("the bottom layer must be a link with send")
    if isinstance(bottom, BoundLayer):
        raise ValueError("a bind layer cannot sit at the bottom; put a link first")
    for index in range(1, len(layers)):
        layer = layers[index]
        bind = getattr(layer, "bind", None)
        if not callable(bind):
            raise ValueError("layers above the bottom must bind to the layer below")
        bind(layers[index - 1])
