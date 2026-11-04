#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Dynamic CLI extension loader for optional ``mas-lab`` components.

Core ``mas-lab`` keeps OSS commands only. Enterprise / optional features are
provided by installable packages through entry points.

Entry point contract:

- Group: ``mas.lab.cli.components``
- Value: a component class (or instance) implementing ``register(app)``
"""
from __future__ import annotations

import logging
from importlib.metadata import entry_points
from typing import Any, Protocol

import click

logger = logging.getLogger(__name__)

_EP_GROUP = "mas.lab.cli.components"


class CliComponent(Protocol):
    """Contract for optional CLI components discovered via entry points."""

    def register(self, app: click.Group) -> str | None:
        """Register commands on the target app and return a command name."""


def _resolve_component(obj: Any) -> CliComponent | None:
    """Resolve an entry-point target into a CLI component instance."""
    if isinstance(obj, type):
        try:
            candidate = obj()
        except TypeError:
            return None
        if hasattr(candidate, "register") and callable(candidate.register):
            return candidate
    if hasattr(obj, "register") and callable(obj.register):
        return obj
    return None


def register_extension_components(app: click.Group) -> list[str]:
    """Load and register optional CLI components from entry points.

    Returns the list of command names registered successfully by components.
    """
    registered: list[str] = []

    try:
        eps = entry_points()
        selected = eps.select(group=_EP_GROUP)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Could not enumerate CLI extensions: %s", exc)
        return registered

    for ep in selected:
        try:
            target = ep.load()
            component = _resolve_component(target)
            if component is None:
                logger.warning(
                    "Skipping CLI extension '%s': target is not a CLI component",
                    ep.name,
                )
                continue
            command_name = component.register(app)
            if command_name:
                registered.append(command_name)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to load CLI extension '%s': %s", ep.name, exc)

    return registered
