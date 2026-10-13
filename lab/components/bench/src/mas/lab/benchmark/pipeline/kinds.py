#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Reserved telemetry envelope kinds (lifecycle, artifacts, control)."""

from __future__ import annotations

LIFECYCLE_PREFIX = "run."
ARTIFACT_PREFIX = "artifact."
CONTROL_PREFIX = "control."
SCOPE_PREFIX = "scope."

CONTROL_KINDS = frozenset(
    {
        "control.structure",
        "control.context",
        "control.inspect",
        "control.hitl",
        "control.ack",
    }
)

LIFECYCLE_KINDS = frozenset(
    {
        "run.started",
        "run.attempt_reset",
        "run.terminal",
    }
)


def is_control_kind(kind: str) -> bool:
    return str(kind or "").startswith(CONTROL_PREFIX)


def is_lifecycle_kind(kind: str) -> bool:
    return str(kind or "").startswith(LIFECYCLE_PREFIX)


def is_artifact_kind(kind: str) -> bool:
    return str(kind or "").startswith(ARTIFACT_PREFIX)


def is_native_export_kind(kind: str) -> bool:
    """Kinds that belong in traces/events.jsonl (native export, not envelope-only)."""
    from mas.library.standard.lib.observability.emit.jsonl import envelope_only_kind

    text = str(kind or "")
    return bool(text) and not envelope_only_kind(text)


def reserved_kind(kind: str) -> bool:
    return (
        is_control_kind(kind)
        or is_lifecycle_kind(kind)
        or is_artifact_kind(kind)
        or str(kind or "").startswith(SCOPE_PREFIX)
    )
