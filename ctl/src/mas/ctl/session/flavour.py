#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Flavour selection + validation for chat / tui / run-mas.

A *flavour* is a deployment posture — a ``kind: Flavour`` manifest bundled in
``mas.library.standard`` (``flavours/<name>.yaml``). It selects protocols and
observability/control plugins. It is **not** a place for LLM parameters
(those live in the agent spec) or infra coordinates — see
``docs/design/flavour-boundary.md``.

``--flavour`` selects deployment posture. :func:`resolve_flavour` resolves,
validates, and returns the flavour's ``spec`` dict; callers use its exposure
protocol and observability defaults. :func:`validate_flavour` is kept for
callers that only need the validation side effect.
"""

from __future__ import annotations

import contextlib
import importlib.resources
from typing import Any

import yaml

_FLAVOUR_PACKAGE = "mas.library.standard"
DEFAULT_FLAVOUR = "local"
# Flavours wired into the interactive path today. Offline chat uses llm_cache replay.
SUPPORTED_FLAVOURS = ("local", "mcp")


class FlavourError(ValueError):
    """Raised when a flavour name is unknown / unsupported, or its manifest is invalid."""


def _load_bundled_flavour(name: str) -> dict[str, Any]:
    """Load ``flavours/<name>.yaml`` from library-standard, or ``{}`` if absent."""
    resource = importlib.resources.files(_FLAVOUR_PACKAGE).joinpath("flavours", f"{name}.yaml")
    try:
        with contextlib.ExitStack() as stack:
            path = stack.enter_context(importlib.resources.as_file(resource))
            if not path.exists():
                return {}
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        # library-standard not installed (mas-ctl only hard-deps mas-runtime).
        return {}
    return data if isinstance(data, dict) else {}


def resolve_flavour(name: str | None = None, *, overrides: tuple[str, ...] | list[str] = ()) -> dict[str, Any]:
    """Resolve, schema-validate, and return the selected flavour's ``spec`` dict.

    Raises :class:`FlavourError` for an unknown/unsupported name or an invalid
    flavour manifest. Defaults to ``local``. A missing library-standard (so the
    bundled flavour can't be loaded) degrades to ``{}`` — "no deployment-posture
    overrides" — rather than breaking the run.
    """
    resolved = (name or DEFAULT_FLAVOUR).strip().lower()
    if resolved not in SUPPORTED_FLAVOURS:
        supported = ", ".join(SUPPORTED_FLAVOURS)
        raise FlavourError(
            f"flavour {resolved!r} is not supported yet (supported: {supported}). "
            f"For offline runs attach llm_cache replay (raise_on_miss); "
            f"see all bundled flavours with `mas-ctl flavour list`."
        )
    data = _load_bundled_flavour(resolved)
    if not data:
        return {}

    if overrides:
        from mas.ctl.overrides import apply_cli_overrides, overrides_for_root

        flavour_overrides = overrides_for_root(overrides, "flavour")
        if flavour_overrides:
            data = apply_cli_overrides(data, flavour_overrides, root="flavour")

    from mas.ctl.validate import validate_data, validation_enabled

    if validation_enabled():
        result = validate_data(data, kind="flavour")
        try:
            result.raise_if_failed()
        except Exception as exc:  # normalise to a clean CLI error
            raise FlavourError(f"flavour {resolved!r} manifest is invalid: {exc}") from exc

    return data.get("spec") or {}


def validate_flavour(name: str | None = None) -> None:
    """Resolve and schema-validate the selected flavour; apply nothing.

    Kept for callers that only need the validation side effect. Prefer
    :func:`resolve_flavour` for new call sites that also need the flavour's
    surviving deployment concerns (e.g. observability plugin selection).
    """
    resolve_flavour(name)
