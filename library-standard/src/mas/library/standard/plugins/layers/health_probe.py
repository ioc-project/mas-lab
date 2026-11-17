#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Health probe layer: checks, ahead of the model's next call, that what the spec says is probeable still answers.

The circuit breaker is reactive: it learns a tool or peer is down when a call fails. This layer is proactive.
Every ``interval_s``, at the next LLM call, it runs the probe each target declares:

* a tool with ``probe: {arguments: {...}}`` (a safe, repeatable call) in ``spec.tools``;
* a peer with ``probe: true`` in ``spec.communication.peers``, checked through its link (an A2A link fetches the
  remote agent card afresh; a link without a probe is skipped).

A probe that fails takes the target out of the running spec (``enabled: false``, ``disabled_by: health_probe``),
so the model is not offered it and governance blocks a call to it as unavailable. A probe that passes puts back
what this layer took away, and nothing else: a target the circuit breaker or an operator disabled stays as it is.

State. Per target (``tool:<name>``, ``peer:<id>``) a mapping ``{checked_at, failing}`` in the session's running
spec, written through the same compare-and-set port as the breaker. Outside a session it is kept in memory.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from typing import Any

from mas.library.standard.lib.stack.call import LayerLifecycle
from mas.runtime.engine.ainvoke import ainvoke_call, apreview_call
from mas.runtime.engine.infra_pipeline import call_ctx
from mas.runtime.layers.probe import peer_prober_for, tool_prober_for
from mas.runtime.layers.state import MemorySpecPort, RunningSpecPort, running_spec_of, spec_port_for, transition

logger = logging.getLogger(__name__)

LAYER = "health_probe"
_BY = LAYER
_KEYS = frozenset({"interval_s", "enabled", "plugin"})
_DEFAULT_INTERVAL_S = 30.0


def parse_params(raw: Mapping[str, Any] | None) -> tuple[float, bool]:
    data = {k: v for k, v in dict(raw or {}).items() if k != "plugin"}
    unknown = set(data) - _KEYS
    if unknown:
        raise ValueError(f"health_probe: unknown field {sorted(str(k) for k in unknown)[0]!r}")
    interval = data.get("interval_s", _DEFAULT_INTERVAL_S)
    if not isinstance(interval, (int, float)) or isinstance(interval, bool) or interval <= 0:
        raise ValueError("health_probe.interval_s must be a number > 0")
    return float(interval), data.get("enabled") is not False


def declared_tool_probes(spec: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """``{tool name: probe arguments}`` for each tool the spec gives a ``probe``."""
    out: dict[str, dict[str, Any]] = {}
    for tool in spec.get("tools") or []:
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("name") or tool.get("id") or "").strip()
        declared = tool.get("spec") if isinstance(tool.get("spec"), dict) else tool
        probe = declared.get("probe")
        if name and isinstance(probe, dict):
            arguments = probe.get("arguments")
            out[name] = dict(arguments) if isinstance(arguments, dict) else {}
    return out


def declared_peer_probes(spec: Mapping[str, Any]) -> list[str]:
    """Ids of the peers the spec marks ``probe: true``."""
    communication = spec.get("communication")
    peers = communication.get("peers") if isinstance(communication, dict) else None
    return [
        str(peer["id"]).strip()
        for peer in peers or []
        if isinstance(peer, dict) and peer.get("id") and peer.get("probe")
    ]


class HealthProbeLayer(LayerLifecycle):
    """Passes every call through; at the next LLM call after ``interval_s`` it probes what the spec declares."""

    per = "destination"
    call_kinds = ("llm", "tool")
    priority = 50

    def __init__(self, inner: Any = None, *, clock: Callable[[], float] = time.time, **params: Any) -> None:
        self.inner = inner
        self.interval_s, self.enabled = parse_params(params)
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
    ) -> "HealthProbeLayer":
        wrapped = cls(inner=inner, **dict(params or {}))
        if call_kinds is not None:
            wrapped.call_kinds = call_kinds
        return wrapped

    def _port(self) -> RunningSpecPort:
        return spec_port_for(call_ctx(self.inner)) or self._memory

    def _due(self, port: RunningSpecPort, target: str) -> bool:
        checked = float(port.read(LAYER, target).get("checked_at") or 0)
        return self._clock() - checked >= self.interval_s

    def _record(self, port: RunningSpecPort, target: str, *, failing: bool) -> bool:
        """Note the check and return whether this layer had the target marked failing before."""
        before, _ = transition(
            port,
            LAYER,
            target,
            lambda _s: {"checked_at": self._clock(), "failing": failing},
            why="failed" if failing else "passed",
        )
        return bool(before.get("failing"))

    async def atick(self) -> None:
        await super().atick()
        if not self.enabled:
            return
        port = self._port()
        spec = running_spec_of(call_ctx(self.inner))
        tool_prober, peer_prober = tool_prober_for(self.inner), peer_prober_for(self.inner)
        if tool_prober is not None:
            for name, arguments in declared_tool_probes(spec).items():
                if self._due(port, f"tool:{name}"):
                    await self._probe_tool(port, tool_prober, name, arguments)
        if peer_prober is not None:
            for peer in declared_peer_probes(spec):
                if self._due(port, f"peer:{peer}"):
                    await self._probe_peer(port, peer_prober, peer)

    async def _probe_tool(self, port: RunningSpecPort, prober: Any, name: str, arguments: dict[str, Any]) -> None:
        target = f"tool:{name}"
        try:
            await prober.aprobe_tool(name, arguments)
        except Exception as exc:  # noqa: BLE001 - any failure of the probe means the tool is not answering
            self._record(port, target, failing=True)
            if port.disable_tool(name, by=_BY, reason=f"{LAYER} probe failed for {target}: {exc}"):
                logger.warning("health probe: %s is not answering (%s); taken out of the spec", target, exc)
            return
        if self._record(port, target, failing=False):
            port.enable_tool(name, by=_BY, reason=f"{LAYER} probe passed for {target}")

    async def _probe_peer(self, port: RunningSpecPort, prober: Any, peer: str) -> None:
        target = f"peer:{peer}"
        try:
            result = await prober.aprobe_peer(peer)
        except Exception as exc:  # noqa: BLE001
            self._record(port, target, failing=True)
            if port.disable_peer(peer, by=_BY, reason=f"{LAYER} probe failed for {target}: {exc}"):
                logger.warning("health probe: %s is not answering (%s); taken out of the spec", target, exc)
            return
        if result is None:  # the link has no probe: nothing learned, nothing changed
            return
        if self._record(port, target, failing=False):
            port.enable_peer(peer, by=_BY, reason=f"{LAYER} probe passed for {target}")

    async def ainvoke(self, io: Any) -> Any:
        return await ainvoke_call(self.inner, io)

    async def aexchange_preview(self, op: str, *, correlation_id: int = 0) -> str:
        head = await apreview_call(self.inner, op, correlation_id=correlation_id)
        label = "[health_probe]"
        return f"{label}\n{head}".strip() if head else label
