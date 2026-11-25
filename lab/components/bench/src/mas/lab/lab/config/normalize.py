#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Rewrite legacy experiment design/schedule keys to plugin + params.

Call this once at config load. Dataclasses, the plan pipeline, and plugins see
only the new form.
"""

from __future__ import annotations

from typing import Any

from mas.lab.deprecations import warn_deprecated

_OLD_DESIGN_PARAM_KEYS = ("couplings", "vary", "pin_scenarios", "pin_items")
_OLD_SCHEDULE_ORDER_KEYS = ("ordering", "strategy")
EXECUTOR_HOST_KEYS = (
    "timeout",
    "runner",
    "reset_state",
    "worker_processes",
    "worker_max_runs",
)
RETRY_LEGACY_KEYS = {"max_attempts": "max_attempts", "retry_backoff_s": "backoff_s"}
"""Old ``execution`` / ``schedule`` key to its name in ``retry.params``."""
_SCHEDULE_KEYS = frozenset({"plugin", "params", "parallel_scenarios", "pause_between_runs"})
_EXECUTOR_KEYS = frozenset({"plugin", "params", *EXECUTOR_HOST_KEYS})


def _both_spellings(old_present: bool, new_present: bool, *, what: str) -> None:
    if old_present and new_present:
        raise ValueError(f"{what}: give the old keys or plugin/params, not both")


def _coerce_params(src: dict[str, Any], *, what: str) -> None:
    if "params" not in src or src["params"] is None:
        src["params"] = {}
    if not isinstance(src["params"], dict):
        raise ValueError(f"{what}.params must be a mapping")


def _coerce_plugin(src: dict[str, Any], *, what: str, default: str | None) -> None:
    if "plugin" not in src:
        if default is not None:
            src["plugin"] = default
        else:
            return
    if not isinstance(src["plugin"], str) or not src["plugin"].strip():
        raise ValueError(f"{what}.plugin must be a non-empty string")
    src["plugin"] = src["plugin"].strip()


def normalize_design(data: dict[str, Any] | None, *, where: str) -> dict[str, Any]:
    """Return design as ``plugin``, ``max_executions``, ``params``."""
    src = dict(data or {})
    if "pin" in src:
        raise ValueError("design.pin is not read; use pin_scenarios / pin_items with one_factor")

    old_mode = "mode" in src
    old_params = any(key in src for key in _OLD_DESIGN_PARAM_KEYS)
    new_plugin = "plugin" in src
    new_params = "params" in src
    _both_spellings(old_mode, new_plugin, what="design")
    _both_spellings(old_params, new_params, what="design")
    if old_params and new_plugin:
        raise ValueError("design: give the old keys or plugin/params, not both")
    if old_mode and new_params:
        raise ValueError("design: give the old keys or plugin/params, not both")

    if old_mode or old_params:
        warn_deprecated("experiment.design.mode", where=where)
        plugin = src.pop("mode", "cartesian")
        params: dict[str, Any] = {}
        for key in _OLD_DESIGN_PARAM_KEYS:
            if key in src:
                params[key] = src.pop(key)
        src["plugin"] = plugin
        src["params"] = params

    _coerce_plugin(src, what="design", default="cartesian")
    _coerce_params(src, what="design")
    allowed = {"plugin", "max_executions", "params"}
    extra = sorted(set(src) - allowed)
    if extra:
        raise ValueError(f"{where}: design has unknown keys {extra}")
    return src


def normalize_schedule(data: dict[str, Any] | None, *, where: str) -> dict[str, Any]:
    """Return schedule with ``plugin`` (optional) and ``params``.

    Omitted plugin means the generator's ``default_scheduler``. ``ordering`` /
    ``strategy`` are rewritten to ``plugin``.
    """
    src = dict(data or {})
    old_order = any(key in src for key in _OLD_SCHEDULE_ORDER_KEYS)
    new_plugin = "plugin" in src
    _both_spellings(old_order, new_plugin, what="schedule")
    if old_order:
        warn_deprecated("experiment.schedule.ordering", where=where)
        plugin = src.pop("ordering", None) or src.pop("strategy", None)
        src.pop("strategy", None)
        src.pop("ordering", None)
        if plugin:
            src["plugin"] = plugin
    _coerce_params(src, what="schedule")
    _coerce_plugin(src, what="schedule", default=None)
    extra = sorted(set(src) - _SCHEDULE_KEYS)
    if extra:
        raise ValueError(f"{where}: schedule has unknown keys {extra}")
    return src


def _peel(src: dict[str, Any], names: dict[str, str]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Move the keys of ``names`` (old name to new name) off a mapping; an empty value is not moved."""
    rest, peeled = dict(src), {}
    for old, new in names.items():
        value = rest.pop(old, None)
        if value is not None:
            peeled[new] = value
    return rest, peeled


def peel_executor_keys(schedule: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Move executor host keys off a schedule mapping. Caller emits the deprecation."""
    return _peel(schedule, {key: key for key in EXECUTOR_HOST_KEYS})


def peel_retry_keys(src: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Move retry keys off a legacy mapping, renamed for ``retry.params``."""
    return _peel(src, RETRY_LEGACY_KEYS)


def normalize_plugin_block(data: dict[str, Any] | None, *, what: str, where: str, default: str) -> dict[str, Any]:
    """``{plugin, params}`` with the default plugin filled in; nothing else is allowed."""
    src = dict(data or {})
    _coerce_params(src, what=what)
    _coerce_plugin(src, what=what, default=default)
    extra = sorted(set(src) - {"plugin", "params"})
    if extra:
        raise ValueError(f"{where}: {what} has unknown keys {extra}")
    return src


def exclusive_merge(left: dict[str, Any], right: dict[str, Any], *, what: str) -> dict[str, Any]:
    """Union of two mappings. The same key in both is an error."""
    overlap = sorted(set(left) & set(right))
    if overlap:
        raise ValueError(f"{what} both set {overlap[0]}; give the old keys or the new block, not both")
    out = dict(left)
    out.update(right)
    return out


def combine_executor_sources(*, peeled: dict[str, Any], explicit: dict[str, Any]) -> dict[str, Any]:
    """Merge keys rewritten from leftover ``execution`` / ``schedule`` with ``executor:``.

    The same host key in both places is an error. ``plugin`` / ``params`` live only
    on the new block.
    """
    explicit = dict(explicit or {})
    params = dict(explicit.get("params") or {})
    explicit_host = {key for key in EXECUTOR_HOST_KEYS if key in explicit or key in params}
    overlap = sorted(set(peeled) & explicit_host)
    if overlap:
        raise ValueError(
            f"legacy execution/schedule and executor both set {overlap[0]}; give the old keys or executor, not both"
        )
    out = dict(peeled)
    out.update(explicit)
    return out


def normalize_executor(data: dict[str, Any] | None, *, where: str) -> dict[str, Any]:
    """Return executor as ``plugin``, host keys, and ``params``.

    Host keys may sit at the top level or in ``params`` (not both). Omitted
    plugin is ``native``.
    """
    src = dict(data or {})
    _coerce_params(src, what="executor")
    params = src["params"]
    overlap = sorted(key for key in EXECUTOR_HOST_KEYS if key in src and key in params)
    if overlap:
        raise ValueError(f"executor: give {overlap[0]} at the top level or in params, not both")
    for key in list(params):
        if key in EXECUTOR_HOST_KEYS:
            src[key] = params.pop(key)
    _coerce_plugin(src, what="executor", default="native")
    extra = sorted(set(src) - _EXECUTOR_KEYS)
    if extra:
        raise ValueError(f"{where}: executor has unknown keys {extra}")
    return src
