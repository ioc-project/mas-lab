#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Validated checkpoint cadence and retention policy from ``spec.checkpoint``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class CheckpointSpecError(ValueError):
    """Checkpoint policy is malformed or uses an unsupported option."""


@dataclass(frozen=True)
class CheckpointPolicy:
    """Checkpointing behavior selected by one agent manifest.

    ``mode`` is cadence (when to capture). ``storage_kind`` is location
    (memory, disk, or both). When storage is omitted, ``in_memory`` mode
    means memory-only; ``on_event`` / ``every_turn`` mean disk.
    """

    mode: str = "none"
    triggers: tuple[str, ...] = ()
    every_n_turns: int | None = None
    retention_mode: str = "all"
    retention_n: int = 5
    portability: str = "self_contained"
    auto_resume_latest: bool = False
    storage_kind: str = ""
    storage_path: str = ""

    def resolved_storage(self) -> str:
        """``memory``, ``disk``, ``hybrid``, or ``none`` after applying aliases."""
        if self.storage_kind:
            return self.storage_kind
        if self.mode == "in_memory":
            return "memory"
        if self.mode in {"every_turn", "on_event"}:
            return "disk"
        return "none"

    def uses_disk(self) -> bool:
        return self.resolved_storage() in {"disk", "hybrid"}

    def uses_memory(self) -> bool:
        return self.resolved_storage() in {"memory", "hybrid"}

    def is_shallow(self) -> bool:
        """True when the checkpoint stores pointers into a persisted archive."""
        return self.portability == "reference"

    def is_deep(self) -> bool:
        """True when the checkpoint hydrates every archive payload."""
        return self.portability == "self_contained"


_MODES = {"none", "in_memory", "on_event", "every_turn"}
_STORAGE_KINDS = {"memory", "disk", "hybrid"}
_TRIGGERS = {"after_llm_call", "after_tool_call", "before_destructive_tool", "every_n_turns"}
_RETENTION_MODES = {"all", "single", "last_n"}
_PORTABILITY_ALIASES = {
    "self_contained": "self_contained",
    "deep": "self_contained",
    "reference": "reference",
    "shallow": "reference",
}


def canonicalize_portability(raw: Any) -> str:
    """Map ``deep``/``shallow`` onto ``self_contained``/``reference``."""
    key = str(raw or "self_contained").strip()
    kind = _PORTABILITY_ALIASES.get(key)
    if kind is None:
        raise CheckpointSpecError(
            "spec.checkpoint.portability must be self_contained/deep or reference/shallow"
        )
    return kind


def _parse_storage(raw: Any) -> tuple[str, str]:
    """Return ``(kind, path)`` from a string or ``{kind, path}`` object."""
    if raw is None:
        return "", ""
    if isinstance(raw, str):
        kind = raw.strip()
        path = ""
    elif isinstance(raw, dict):
        unknown = set(raw) - {"kind", "path"}
        if unknown:
            raise CheckpointSpecError(
                f"spec.checkpoint.storage: unknown field {sorted(unknown)[0]!r}"
            )
        kind = str(raw.get("kind") or "").strip()
        path = str(raw.get("path") or "").strip()
    else:
        raise CheckpointSpecError("spec.checkpoint.storage must be a string or object")
    if kind and kind not in _STORAGE_KINDS:
        raise CheckpointSpecError(
            f"spec.checkpoint.storage.kind must be one of {sorted(_STORAGE_KINDS)}"
        )
    return kind, path


def parse_checkpoint_policy(raw: Any) -> CheckpointPolicy:
    """Parse strict ``spec.checkpoint`` fields with behavior-preserving defaults."""
    if raw is None:
        return CheckpointPolicy()
    if not isinstance(raw, dict):
        raise CheckpointSpecError("spec.checkpoint must be an object")
    unknown = set(raw) - {
        "mode",
        "triggers",
        "every_n_turns",
        "retention",
        "portability",
        "auto_resume_latest",
        "storage",
    }
    if unknown:
        raise CheckpointSpecError(f"spec.checkpoint: unknown field {sorted(unknown)[0]!r}")

    mode = raw.get("mode", "none")
    if mode not in _MODES:
        raise CheckpointSpecError(f"spec.checkpoint.mode must be one of {sorted(_MODES)}")
    storage_kind, storage_path = _parse_storage(raw.get("storage"))
    triggers_raw = raw.get("triggers", [])
    if not isinstance(triggers_raw, list) or any(
        not isinstance(trigger, str) or trigger not in _TRIGGERS for trigger in triggers_raw
    ):
        raise CheckpointSpecError(f"spec.checkpoint.triggers must contain only {sorted(_TRIGGERS)}")
    triggers = tuple(dict.fromkeys(triggers_raw))

    every_n_turns = raw.get("every_n_turns")
    if every_n_turns is not None and (
        not isinstance(every_n_turns, int)
        or isinstance(every_n_turns, bool)
        or every_n_turns < 1
    ):
        raise CheckpointSpecError("spec.checkpoint.every_n_turns must be an integer >= 1")

    retention = raw.get("retention", {})
    if not isinstance(retention, dict) or set(retention) - {"mode", "n"}:
        raise CheckpointSpecError("spec.checkpoint.retention must contain only mode and n")
    retention_mode = retention.get("mode", "all")
    if retention_mode not in _RETENTION_MODES:
        raise CheckpointSpecError(f"spec.checkpoint.retention.mode must be one of {sorted(_RETENTION_MODES)}")
    retention_n = retention.get("n", 5)
    if not isinstance(retention_n, int) or isinstance(retention_n, bool) or retention_n < 1:
        raise CheckpointSpecError("spec.checkpoint.retention.n must be an integer >= 1")

    portability = canonicalize_portability(raw.get("portability", "self_contained"))
    auto_resume_latest = raw.get("auto_resume_latest", False)
    if not isinstance(auto_resume_latest, bool):
        raise CheckpointSpecError("spec.checkpoint.auto_resume_latest must be a boolean")
    policy = CheckpointPolicy(
        mode=mode,
        triggers=triggers,
        every_n_turns=every_n_turns,
        retention_mode=retention_mode,
        retention_n=retention_n,
        portability=portability,
        auto_resume_latest=auto_resume_latest,
        storage_kind=storage_kind,
        storage_path=storage_path,
    )
    if auto_resume_latest and not policy.uses_disk():
        raise CheckpointSpecError("spec.checkpoint.auto_resume_latest requires disk or hybrid storage")
    return policy