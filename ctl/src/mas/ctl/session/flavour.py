#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Flavour selection + validation for chat / tui / run-mas.

A *flavour* (``kind: Flavour``) is the glue between the logic spec and the
infra: its ``spec.bind`` links each agent to infra rows and says what this
deployment does with it (:mod:`mas.ctl.infra.bind`). It defines nothing itself,
and it is **not** a place for LLM parameters (those live in the agent spec) or
infra coordinates — see ``docs/design/flavour-boundary.md``. Leftover keys
(``agent_comm``, ``tools``, ``control``, ``telemetry``) are accepted and read
by nothing. ``observability`` is a deprecated fallback when the agent declares
none.

``--flavour`` takes a bundled name (``flavours/<name>.yaml`` in
``mas.library.standard``) or the path of a Flavour YAML file (a value that ends
in ``.yaml`` / ``.yml`` or names an existing file). :func:`resolve_flavour`
resolves, validates, and returns the flavour's ``spec`` dict; callers read
its ``bind``, and ``observability`` as that fallback.
:func:`validate_flavour` is kept for callers that only need the validation side
effect.
"""

from __future__ import annotations

import contextlib
import importlib.resources
import os
import sys
from pathlib import Path
from typing import Any

import yaml

_FLAVOUR_PACKAGE = "mas.library.standard"
DEFAULT_FLAVOUR = "local"
# Flavours wired into the interactive path today. Offline chat uses llm_cache replay.
SUPPORTED_FLAVOURS = ("local",)


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
    raw = (name or DEFAULT_FLAVOUR).strip()
    path = _flavour_path(raw)
    if path is not None:
        return _resolve_flavour_file(path, overrides=overrides)
    resolved = raw.lower()
    if resolved not in SUPPORTED_FLAVOURS:
        supported = ", ".join(SUPPORTED_FLAVOURS)
        raise FlavourError(
            f"flavour {resolved!r} is not supported yet (bundled names: {supported}); "
            f"pass --flavour PATH to a Flavour file. "
            f"For offline runs attach llm_cache replay (raise_on_miss)."
        )
    data = _load_bundled_flavour(resolved)
    if not data:
        return {}
    return _checked_spec(data, label=resolved, overrides=overrides)


def _checked_spec(
    data: dict[str, Any],
    *,
    label: str,
    overrides: tuple[str, ...] | list[str] = (),
    warn_deprecated: bool = False,
) -> dict[str, Any]:
    """Apply ``--override .flavour...`` patches, schema-validate, and return the ``spec``.

    ``warn_deprecated`` prints the leftover-key warning for a Flavour file the user
    wrote. A bundled Flavour is the default of every run and the user cannot edit it,
    so it stays quiet.
    """
    if overrides:
        from mas.ctl.overrides import apply_cli_overrides, overrides_for_root

        flavour_overrides = overrides_for_root(overrides, "flavour")
        if flavour_overrides:
            data = apply_cli_overrides(data, flavour_overrides, root="flavour")

    from mas.ctl.validate import validate_data, validation_enabled

    if validation_enabled():
        result = validate_data(data, kind="flavour")
        if warn_deprecated:
            warn_deprecated_flavour_keys(data)
        try:
            result.raise_if_failed()
        except Exception as exc:  # normalise to a clean CLI error
            raise FlavourError(f"flavour {label!r} manifest is invalid: {exc}") from exc

    return data.get("spec") or {}


def warn_deprecated_flavour_keys(data: dict[str, Any]) -> None:
    """Print, on stderr, the leftover-key warning of a Flavour document the user wrote.

    The one place that prints it: ``--flavour PATH`` in ctl and in the lab bench call it.
    """
    from mas.ctl.validate.deprecations import collect_flavour_key_deprecation

    for _path, msg in collect_flavour_key_deprecation(data):
        print(f"warning: {msg}", file=sys.stderr)


def flavour_argument(value: str | None) -> str | None:
    """``--flavour`` as given, with a relative file path made absolute against the current directory.

    Commands that change into the manifest's directory call this first, so a path
    typed from the shell still names the same file.
    """
    if not value:
        return value
    path = _flavour_path(value.strip())
    return str(path.resolve()) if path is not None and path.is_file() else value


def _flavour_path(value: str) -> Path | None:
    """``value`` as a Flavour file path, or ``None`` when it is a name.

    A path ends in ``.yaml`` / ``.yml`` or holds a path separator. A bare word is a name, even when a
    file of that name sits in the current directory (``local``), and is refused later when no flavour
    is bundled under it.
    """
    separators = {"/", os.sep} | ({os.altsep} if os.altsep else set())
    if value.endswith((".yaml", ".yml")) or any(sep in value for sep in separators):
        return Path(value).expanduser()
    return None


def _resolve_flavour_file(path: Path, *, overrides: tuple[str, ...] | list[str] = ()) -> dict[str, Any]:
    """Load, patch and validate a Flavour YAML file; return its ``spec``."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise FlavourError(f"flavour file {str(path)!r} cannot be read: {exc}") from None
    if not isinstance(data, dict) or data.get("kind") != "Flavour":
        raise FlavourError(f"flavour file {str(path)!r} is not a kind: Flavour document")
    return _checked_spec(data, label=str(path), overrides=overrides, warn_deprecated=True)


def validate_flavour(name: str | None = None) -> None:
    """Resolve and schema-validate the selected flavour; apply nothing.

    Kept for callers that only need the validation side effect. Prefer
    :func:`resolve_flavour` for new call sites that also need ``bind``.
    """
    resolve_flavour(name)
