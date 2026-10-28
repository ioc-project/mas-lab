#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Detect a recorded agent handoff from tool semantics, not from a name guess."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from mas.runtime.boundary.delegation.policy import parse_delegate_tool_name

AGENT_COMM_CONCEPT = "agent_comm"

_ARG_KEYS = ("arguments", "args", "parameters", "tool_arguments")


def _as_map(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _call_arguments(
    event: Mapping[str, Any] | None,
    arguments: Mapping[str, Any] | None,
) -> Mapping[str, Any] | None:
    mapped = _as_map(arguments)
    if mapped is not None:
        return mapped
    if event is None:
        return None
    for key in _ARG_KEYS:
        mapped = _as_map(event.get(key))
        if mapped is not None:
            return mapped
    return None


def _call_semantics(
    event: Mapping[str, Any] | None,
    semantics: Mapping[str, Any] | None,
) -> Mapping[str, Any] | None:
    mapped = _as_map(semantics)
    if mapped is not None:
        return mapped
    if event is None:
        return None
    return _as_map(event.get("semantics"))


def handoff_target(
    tool_name: str | None,
    *,
    event: Mapping[str, Any] | None = None,
    arguments: Mapping[str, Any] | None = None,
    semantics: Mapping[str, Any] | None = None,
) -> str:
    """Return the target agent of a recorded handoff, or empty.

    A handoff is a tool whose recorded ``semantics.concept`` is
    ``agent_comm``, or a legacy tool named ``delegate_to_<id>``. A user
    tool named ``send``, or a tool that only has a ``target`` argument,
    is not a handoff.
    """
    name = str(tool_name or "").strip()
    sem = _call_semantics(event, semantics)
    if sem is not None and str(sem.get("concept") or "").strip() == AGENT_COMM_CONCEPT:
        args = _call_arguments(event, arguments)
        subject = str(sem.get("subject") or "").strip()
        if not subject and args is not None:
            keys: Any = sem.get("subject_arg") or "target"
            if isinstance(keys, str):
                keys = [keys]
            if isinstance(keys, list):
                for key in keys:
                    val = args.get(key)
                    if isinstance(val, str) and val.strip():
                        subject = val.strip()
                        break
        if subject:
            return subject
        return parse_delegate_tool_name(name) or ""
    return parse_delegate_tool_name(name) or ""
