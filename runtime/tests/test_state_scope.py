#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Every field of the kernel state declares its scope, and a new turn clears the turn-scoped ones."""

from __future__ import annotations

from dataclasses import MISSING, fields

import pytest
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.orchestrator import RuntimeKernel
from mas.runtime.kernel.state import QProduct
from mas.runtime.registry import get_registry
from mas.runtime.schema.ingress import UserInputReceived

SCOPES = {"turn", "session", "flow", "pattern"}


def _initial(f):
    return f.default_factory() if f.default_factory is not MISSING else f.default


def _dirty(value):
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 7
    if isinstance(value, str):
        return value + "x"
    if isinstance(value, dict):
        return {"k": 1}
    if isinstance(value, list):
        return [1]
    return value


def test_every_state_field_declares_a_scope() -> None:
    # Kills: adding a field to QProduct without saying whether a new turn clears it.
    missing = [f.name for f in fields(QProduct) if f.metadata.get("scope") not in SCOPES]
    assert missing == []


def _patterns() -> dict[str, type]:
    """Every registered design pattern class, once (aliases resolve to the same class)."""
    registry = get_registry()
    found: dict[str, type] = {}
    for name in registry.list_names("design_pattern"):
        variant = registry.resolve_by_type("design_pattern", name)
        if variant is not None:
            cls = variant.load_class()
            found.setdefault(f"{cls.__module__}.{cls.__qualname__}", cls)
    return found


@pytest.mark.parametrize("pattern_cls", list(_patterns().values()), ids=list(_patterns()))
def test_a_user_input_from_idle_clears_every_turn_scoped_field(pattern_cls: type) -> None:
    # Kills: a turn-scoped field cleared only inside one pattern's transition (the leak that kept an
    # approval or a retry count across turns), or a turn-scoped field no pattern clears.
    turn = [f for f in fields(QProduct) if f.metadata.get("scope") == "turn"]
    assert turn
    kernel = RuntimeKernel(config=KernelConfig(), plugin=pattern_cls())
    for f in turn:
        setattr(kernel.q, f.name, _dirty(_initial(f)))
        assert getattr(kernel.q, f.name) != _initial(f)
    kernel.transition(UserInputReceived(user_turn_id="u1", text="hi"))
    leaked = {f.name: getattr(kernel.q, f.name) for f in turn if getattr(kernel.q, f.name) != _initial(f)}
    assert leaked == {}
