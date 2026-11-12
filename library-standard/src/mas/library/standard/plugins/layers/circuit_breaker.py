#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Circuit breaker layer: fails fast around llm and tool calls, takes a down tool away, brings it back.

Everything the breaker is lives here: its rules (:class:`BreakerPolicy`), where it keeps its state, and what
it does to the spec. The runtime only supplies generic parts: the layer hooks and the running-spec port.

State. Per target (``llm:<api base>``, ``tool:<name>``) a small mapping: ``failures``, ``opened_at`` (wall
clock, so it survives a snapshot), ``half_open`` and ``probe_at``. Inside a managed session it is kept in the session's
running spec through a :class:`~mas.runtime.layers.state.RunningSpecPort`: each transition is a
proposal the session's spec machine decides, so it is ordered by that machine, a snapshot restores it, and
the layer carries no lock or guard of its own. Outside a session it is kept in memory.

A tool that declares a ``probe`` in the spec (``probe: {arguments: {...}}``, a safe repeatable call) is
taken out of the running spec when its circuit opens, so the model stops offering it. The model cannot call a
tool that is gone, so the layer probes it: once ``reset_timeout_s`` has passed, at the next LLM call, it runs
the declared probe. A probe that succeeds closes the circuit and puts the tool back in the spec; one that
fails opens the circuit again for another ``reset_timeout_s``. It restores only what it took away.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from mas.library.standard.lib.stack.call import LayerLifecycle
from mas.runtime.engine.call_kinds import call_kind_matches
from mas.runtime.engine.infra_pipeline import call_ctx
from mas.runtime.layers.probe import tool_prober_for
from mas.runtime.layers.state import MemorySpecPort, RunningSpecPort, spec_port_for, transition
from mas.runtime.layers.target import call_target, classifier_for
from mas.runtime.reliability.classes import CircuitOpenError, FailureClass
from mas.runtime.reliability.classify import classify_tool_exception
from mas.runtime.reliability.log import LOGGER, extra
from mas.runtime.reliability.retry import as_classified

LAYER = "circuit_breaker"
_BY = LAYER
_TOOL = "tool:"
_FAILURE_CLASSES = {item.value: item for item in FailureClass}
_KEYS = frozenset({"failure_threshold", "reset_timeout_s", "on", "enabled", "plugin"})


def _parse_on(raw: Any) -> tuple[FailureClass, ...]:
    if raw is None or raw is True:
        return (FailureClass.UNAVAILABLE,)
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        raise ValueError("circuit_breaker.on must be a list of failure-class names")
    out: list[FailureClass] = []
    for item in raw:
        key = str(item).strip().lower()
        if key not in _FAILURE_CLASSES:
            raise ValueError(
                f"circuit_breaker.on: unknown failure class {item!r} (expected {sorted(_FAILURE_CLASSES)})"
            )
        out.append(_FAILURE_CLASSES[key])
    return tuple(out) or (FailureClass.UNAVAILABLE,)


@dataclass(frozen=True)
class BreakerPolicy:
    """The rules: fail fast after ``failure_threshold`` consecutive failures of a class in ``on``, probe later."""

    failure_threshold: int = 5
    reset_timeout_s: float = 30.0
    on: tuple[FailureClass, ...] = (FailureClass.UNAVAILABLE,)
    enabled: bool = True

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> BreakerPolicy:
        data = dict(raw or {})
        if True in data and "on" not in data:  # YAML reads a bare ``on:`` key as True
            data["on"] = data.pop(True)
        unknown = set(data) - _KEYS
        if unknown:
            raise ValueError(f"circuit_breaker: unknown field {sorted(str(k) for k in unknown)[0]!r}")
        threshold = data.get("failure_threshold", cls.failure_threshold)
        if not isinstance(threshold, int) or isinstance(threshold, bool) or threshold < 1:
            raise ValueError("circuit_breaker.failure_threshold must be an integer >= 1")
        reset = data.get("reset_timeout_s", cls.reset_timeout_s)
        if not isinstance(reset, (int, float)) or isinstance(reset, bool) or reset < 0:
            raise ValueError("circuit_breaker.reset_timeout_s must be a number >= 0")
        return cls(
            failure_threshold=threshold,
            reset_timeout_s=float(reset),
            on=_parse_on(data.get("on")),
            enabled=data.get("enabled") is not False,
        )

    def probe_in_flight(self, state: Mapping[str, Any], now: float) -> bool:
        """A probe was admitted less than ``reset_timeout_s`` ago and has not reported yet."""
        return bool(state.get("half_open")) and (now - float(state.get("probe_at") or 0)) < self.reset_timeout_s

    def is_open(self, state: Mapping[str, Any], now: float) -> bool:
        opened_at = float(state.get("opened_at") or 0)
        if opened_at <= 0:
            return False
        return (now - opened_at) < self.reset_timeout_s or self.probe_in_flight(state, now)

    def probe_due(self, state: Mapping[str, Any], now: float) -> bool:
        opened_at = float(state.get("opened_at") or 0)
        return opened_at > 0 and (now - opened_at) >= self.reset_timeout_s and not self.probe_in_flight(state, now)

    def admit(self, state: Mapping[str, Any], target: str, now: float) -> dict[str, Any]:
        """The state after a call is let through. Raises :class:`CircuitOpenError` while the circuit is open.

        After ``reset_timeout_s`` exactly one call is let through as the probe: the state is marked
        half-open with the time of the probe, and the session's compare-and-set gives that mark to one caller.
        Every other call is refused until the probe reports. A probe that never reports (its caller died) is
        replaced after another ``reset_timeout_s``.
        """
        if not self.enabled or float(state.get("opened_at") or 0) <= 0:
            return dict(state)
        if self.probe_due(state, now):
            LOGGER.info(
                "circuit half-open for %s; allowing one probe",
                target,
                extra=extra(target=target, outcome="circuit_half_open"),
            )
            return {**state, "half_open": True, "probe_at": now}
        since = float(state["probe_at"]) if self.probe_in_flight(state, now) else float(state["opened_at"])
        remaining = self.reset_timeout_s - (now - since)
        LOGGER.warning(
            "circuit open for %s; retry after %.1fs",
            target,
            remaining,
            extra=extra(target=target, outcome="circuit_open", delay_s=round(remaining, 3)),
        )
        raise CircuitOpenError(target, reset_timeout_s=remaining)

    def after_success(self, state: Mapping[str, Any], target: str) -> dict[str, Any]:
        """The state after a call succeeded: closed (empty)."""
        if float(state.get("opened_at") or 0) > 0 or state.get("half_open"):
            LOGGER.info(
                "circuit closed for %s after success",
                target,
                extra=extra(target=target, outcome="circuit_closed"),
            )
        return {}

    def after_failure(
        self, state: Mapping[str, Any], target: str, failure_class: FailureClass, now: float
    ) -> dict[str, Any]:
        """The state after a call failed. A failure of a class not in ``on`` changes nothing, except that
        a failed probe always opens the circuit again."""
        if not self.enabled:
            return dict(state)
        if failure_class not in self.on and not state.get("half_open"):
            return dict(state)
        if state.get("half_open"):
            LOGGER.warning(
                "circuit re-opened for %s after failed probe (%s)",
                target,
                failure_class.value,
                extra=extra(
                    target=target,
                    failure_class=failure_class.value,
                    outcome="circuit_open",
                    attempt=self.failure_threshold,
                ),
            )
            return {"failures": self.failure_threshold, "opened_at": now, "half_open": False}
        failures = int(state.get("failures") or 0) + 1
        if failures < self.failure_threshold:
            return {"failures": failures}
        LOGGER.warning(
            "circuit open for %s after %s consecutive %s failures",
            target,
            failures,
            failure_class.value,
            extra=extra(
                target=target,
                failure_class=failure_class.value,
                outcome="circuit_open",
                attempt=failures,
                max_attempts=self.failure_threshold,
            ),
        )
        return {"failures": failures, "opened_at": now}


def _opened(state: Mapping[str, Any]) -> bool:
    return float(state.get("opened_at") or 0) > 0


class CircuitBreakerLayer(LayerLifecycle):
    """Fail-fast around the inner call, with state in the running spec and a way back for tools."""

    per = "destination"
    call_kinds = ("llm", "tool")
    priority = 60

    def __init__(self, inner: Any = None, *, clock: Callable[[], float] = time.time, **params: Any) -> None:
        self.inner = inner
        self.policy = BreakerPolicy.from_mapping({k: v for k, v in params.items() if k != "plugin"})
        self._clock = clock
        self._memory = MemorySpecPort()
        self.middleware_id = LAYER

    @classmethod
    def wrap(
        cls,
        inner: Any,
        params: dict[str, Any] | None = None,
        *,
        call_kinds: tuple[str, ...] | None = None,
    ) -> "CircuitBreakerLayer":
        wrapped = cls(inner=inner, **dict(params or {}))
        if call_kinds is not None:
            wrapped.call_kinds = call_kinds
        return wrapped

    @property
    def enabled(self) -> bool:
        return self.policy.enabled

    @property
    def failure_threshold(self) -> int:
        return self.policy.failure_threshold

    @property
    def reset_timeout_s(self) -> float:
        return self.policy.reset_timeout_s

    @property
    def on(self) -> tuple[FailureClass, ...]:
        return self.policy.on

    def _port(self) -> RunningSpecPort:
        return spec_port_for(call_ctx(self.inner)) or self._memory

    def _step(
        self,
        port: RunningSpecPort,
        target: str,
        step: Callable[[dict[str, Any]], dict[str, Any]],
        *,
        why: str | Callable[[dict[str, Any], dict[str, Any]], str],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        return transition(port, LAYER, target, step, why=why)

    def before_call(self, target: str, port: RunningSpecPort | None = None) -> None:
        """Let a call through, or raise :class:`CircuitOpenError` while the circuit for *target* is open."""
        self._step(port or self._memory, target, lambda s: self.policy.admit(s, target, self._clock()), why="half-open")

    def record_success(self, target: str, port: RunningSpecPort | None = None) -> None:
        port = port or self._memory
        before, after = self._step(
            port, target, lambda s: self.policy.after_success(s, target) if s else s, why="closed"
        )
        if _opened(before) and not after:
            self._restore_tool(port, target)

    def record_failure(self, target: str, failure_class: FailureClass, port: RunningSpecPort | None = None) -> None:
        port = port or self._memory
        before, after = self._step(
            port,
            target,
            lambda s: self.policy.after_failure(s, target, failure_class, self._clock()),
            why=lambda b, a: "opened" if _opened(a) and not _opened(b) else "counting",
        )
        if _opened(after) and not _opened(before):
            self._withdraw_tool(port, target)

    def is_open(self, target: str, port: RunningSpecPort | None = None) -> bool:
        return self.policy.is_open((port or self._memory).read(LAYER, target), self._clock())

    def _withdraw_tool(self, port: RunningSpecPort, target: str) -> None:
        """A tool that can be probed leaves the running spec while its circuit is open."""
        if not target.startswith(_TOOL):
            return
        name = target[len(_TOOL) :]
        if port.tool_probe(name) is not None:
            port.disable_tool(name, by=_BY, reason=f"{LAYER} opened {target}")

    def _restore_tool(self, port: RunningSpecPort, target: str) -> None:
        if target.startswith(_TOOL):
            port.enable_tool(target[len(_TOOL) :], by=_BY, reason=f"{LAYER} closed {target}")

    def tick(self) -> None:
        """Probe every tool this layer took away whose circuit has been open for ``reset_timeout_s``."""
        super().tick()
        if not self.policy.enabled or "tool" not in self.call_kinds:
            return
        port = self._port()
        prober = tool_prober_for(self.inner)
        if prober is None:
            return
        for target, state in port.targets(LAYER).items():
            if not target.startswith(_TOOL) or not self.policy.probe_due(state, self._clock()):
                continue
            name = target[len(_TOOL) :]
            arguments = port.tool_probe(name)
            if arguments is None:
                continue
            self._probe(port, prober, target, name, arguments)

    def _probe(self, port: RunningSpecPort, prober: Any, target: str, name: str, arguments: dict[str, Any]) -> None:
        self._step(port, target, lambda s: self.policy.admit(s, target, self._clock()), why="half-open")
        try:
            prober.probe_tool(name, arguments)
        except Exception as exc:
            self.record_failure(target, as_classified(exc, classify_tool_exception).failure_class, port)
            return
        self.record_success(target, port)

    def exchange_preview(self, op: str, *, correlation_id: int = 0) -> str:
        head = str(self.inner.exchange_preview(op, correlation_id=correlation_id) or "")
        label = "[circuit_breaker]"
        return f"{label}\n{head}".strip() if head else label

    def _enter(self, io: Any) -> tuple[str, RunningSpecPort] | None:
        if not call_kind_matches(io.op, self.call_kinds):
            return None
        target = call_target(self.inner, io)
        if target is None:
            return None
        port = self._port()
        self.before_call(target, port)
        return target, port

    def invoke(self, io: Any) -> Any:
        entered = self._enter(io)
        if entered is None:
            return self.inner.invoke(io)
        target, port = entered
        try:
            result = self.inner.invoke(io)
        except CircuitOpenError:
            raise
        except Exception as exc:
            self.record_failure(target, as_classified(exc, classifier_for(io)).failure_class, port)
            raise
        self.record_success(target, port)
        return result

    async def ainvoke(self, io: Any) -> Any:
        entered = self._enter(io)
        if entered is None:
            return await self.inner.ainvoke(io)
        target, port = entered
        try:
            result = await self.inner.ainvoke(io)
        except CircuitOpenError:
            raise
        except Exception as exc:
            self.record_failure(target, as_classified(exc, classifier_for(io)).failure_class, port)
            raise
        self.record_success(target, port)
        return result
