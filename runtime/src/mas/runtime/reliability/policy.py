#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Spec-driven retry settings (engine I/O). The circuit breaker is a layer plugin."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Mapping

from mas.runtime.reliability.classes import FailureClass


def _binding_error(message: str) -> Exception:
    from mas.runtime.spec.gov import SpecBindingError

    return SpecBindingError(message)


_FAILURE_CLASSES = {item.value: item for item in FailureClass}


def _parse_classes(raw: Any, *, field_name: str, default: tuple[FailureClass, ...]) -> tuple[FailureClass, ...]:
    if raw is None:
        return default
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        raise _binding_error(f"{field_name} must be a list of failure-class names")
    out: list[FailureClass] = []
    for item in raw:
        key = str(item).strip().lower()
        if key not in _FAILURE_CLASSES:
            raise _binding_error(f"{field_name}: unknown failure class {item!r} (expected {sorted(_FAILURE_CLASSES)})")
        out.append(_FAILURE_CLASSES[key])
    return tuple(out) or default


@dataclass(frozen=True)
class RetryPolicy:
    """How many times to re-issue the same HTTP/tool call (infra layer)."""

    max_attempts: int = 4
    backoff_s: float = 0.5
    backoff_multiplier: float = 2.0
    jitter: bool = True
    retry_on: tuple[FailureClass, ...] = (
        FailureClass.TRANSIENT,
        FailureClass.UNAVAILABLE,
    )
    require_idempotent: bool = False
    max_backoff_s: float | None = None

    def delay_for(self, attempt: int) -> float:
        """Sleep before attempt ``attempt`` (0-based, after the first failure)."""
        delay = max(0.0, self.backoff_s) * (self.backoff_multiplier ** max(0, attempt))
        if self.max_backoff_s is not None:
            delay = min(delay, max(0.0, self.max_backoff_s))
        if self.jitter and delay > 0:
            delay += random.uniform(0.0, delay * 0.25)
        return delay

    def allows(self, failure_class: FailureClass, *, idempotent: bool) -> bool:
        if failure_class not in self.retry_on:
            return False
        if self.require_idempotent and failure_class == FailureClass.TRANSIENT and not idempotent:
            return False
        return True

    def to_mapping(self) -> dict[str, Any]:
        out = {
            "max_attempts": self.max_attempts,
            "backoff_s": self.backoff_s,
            "backoff_multiplier": self.backoff_multiplier,
            "jitter": self.jitter,
            "retry_on": [item.value for item in self.retry_on],
            "require_idempotent": self.require_idempotent,
        }
        if self.max_backoff_s is not None:
            out["max_backoff_s"] = self.max_backoff_s
        return out

    @classmethod
    def llm_default(cls) -> RetryPolicy:
        """Chat completions: retry dropped sockets, 429/5xx, and connect-refused."""
        return cls(
            retry_on=(FailureClass.TRANSIENT, FailureClass.UNAVAILABLE),
        )

    @classmethod
    def tools_default(cls) -> RetryPolicy:
        return cls(
            max_attempts=2,
            retry_on=(FailureClass.TRANSIENT, FailureClass.UNAVAILABLE),
            require_idempotent=True,
        )

    @classmethod
    def from_mapping(cls, raw: Any, *, field_name: str, default: RetryPolicy | None = None) -> RetryPolicy:
        base = default or cls()
        if raw is None:
            return base
        if not isinstance(raw, Mapping):
            raise _binding_error(f"{field_name} must be an object")
        allowed = {
            "max_attempts",
            "backoff_s",
            "backoff_multiplier",
            "jitter",
            "retry_on",
            "require_idempotent",
            "max_backoff_s",
        }
        unknown = set(raw) - allowed
        if unknown:
            raise _binding_error(f"{field_name}: unknown field {sorted(unknown)[0]!r}")
        max_attempts = raw.get("max_attempts", base.max_attempts)
        if not isinstance(max_attempts, int) or isinstance(max_attempts, bool) or max_attempts < 1:
            raise _binding_error(f"{field_name}.max_attempts must be an integer >= 1")
        backoff_s = raw.get("backoff_s", base.backoff_s)
        if not isinstance(backoff_s, (int, float)) or isinstance(backoff_s, bool) or backoff_s < 0:
            raise _binding_error(f"{field_name}.backoff_s must be a number >= 0")
        multiplier = raw.get("backoff_multiplier", base.backoff_multiplier)
        if not isinstance(multiplier, (int, float)) or isinstance(multiplier, bool) or multiplier < 1:
            raise _binding_error(f"{field_name}.backoff_multiplier must be a number >= 1")
        jitter = base.jitter if raw.get("jitter") is None else bool(raw.get("jitter"))
        require_idempotent = (
            base.require_idempotent if raw.get("require_idempotent") is None else bool(raw.get("require_idempotent"))
        )
        max_backoff = base.max_backoff_s if raw.get("max_backoff_s") is None else raw.get("max_backoff_s")
        if max_backoff is not None and (
            not isinstance(max_backoff, (int, float)) or isinstance(max_backoff, bool) or max_backoff < 0
        ):
            raise _binding_error(f"{field_name}.max_backoff_s must be a number >= 0 or null")
        return cls(
            max_attempts=max_attempts,
            backoff_s=float(backoff_s),
            backoff_multiplier=float(multiplier),
            jitter=jitter,
            retry_on=_parse_classes(raw.get("retry_on"), field_name=f"{field_name}.retry_on", default=base.retry_on),
            require_idempotent=require_idempotent,
            max_backoff_s=None if max_backoff is None else float(max_backoff),
        )


def llm_retry_policy(**overrides: Any) -> RetryPolicy:
    """RetryPolicy for live LLM HTTP."""
    policy = RetryPolicy.llm_default()
    if overrides:
        policy = RetryPolicy.from_mapping(overrides, field_name="llm.retry", default=policy)
    return policy


@dataclass(frozen=True)
class ReliabilitySettings:
    """Engine knobs parsed from ``spec.control``.

    Retry and the circuit breaker are call layers; this only reads and checks the deprecated
    ``spec.control`` spelling. Ingress ``error_policy`` lives on ``retry_on_error``.
    """

    llm_retry: RetryPolicy = field(default_factory=RetryPolicy.llm_default)
    tool_retry: RetryPolicy = field(default_factory=RetryPolicy.tools_default)

    @classmethod
    def from_spec(cls, spec: Mapping[str, Any] | None) -> ReliabilitySettings:
        control = (spec or {}).get("control") if isinstance(spec, Mapping) else None
        control = control if isinstance(control, Mapping) else {}
        retry_raw = control.get("retry")
        if retry_raw is None:
            retry_raw = {}
        elif not isinstance(retry_raw, Mapping):
            raise _binding_error("spec.control.retry must be an object")
        else:
            unknown_retry = set(retry_raw) - {"llm", "tools"}
            if unknown_retry:
                raise _binding_error(f"spec.control.retry: unknown field {sorted(unknown_retry)[0]!r}")
        llm = RetryPolicy.from_mapping(
            retry_raw.get("llm"),
            field_name="spec.control.retry.llm",
            default=RetryPolicy.llm_default(),
        )
        tools = RetryPolicy.from_mapping(
            retry_raw.get("tools"),
            field_name="spec.control.retry.tools",
            default=RetryPolicy.tools_default(),
        )
        breaker = control.get("circuit_breaker")
        if breaker is not None:
            if not isinstance(breaker, Mapping):
                raise _binding_error("spec.control.circuit_breaker must be an object or null")
            # its params are checked by the circuit_breaker layer when the layer is built
        return cls(llm_retry=llm, tool_retry=tools)
