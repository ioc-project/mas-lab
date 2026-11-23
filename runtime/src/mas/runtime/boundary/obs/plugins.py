#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Registry-backed observability plugin wiring.

Plugin construction lives here, and only here. Callers outside runtime
(ctl's session/executor/benchmark code) receive an ``ObservabilityBinding``
(pure data — see ``binding.py``) and hand it to :func:`build_observability_plugin_set`
/ :func:`attach_observability_plugin_set`; they never import
:func:`build_observability_plugins` or instantiate :class:`ObsPluginSet`
themselves. This mirrors how ``design_pattern`` plugins are resolved by
``mas.runtime.boundary.context.dp_inject`` purely from a plugin id, entirely
inside runtime.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING

from mas.runtime import hostenv
from mas.runtime.boundary.obs.binding import ObservabilityBinding
from mas.runtime.boundary.obs.observability_plugin import ObservabilityPlugin
from mas.runtime.boundary.obs.operator import ObservabilityOperator
from mas.runtime.registry import get_registry
from mas.runtime.transport.config import ProtocolTransportConfig, TransportObservabilityConfig
from mas.runtime.transport.emit import bind_config

if TYPE_CHECKING:
    from mas.runtime.driver.instance import RuntimeInstance


def _load_observability_plugin(
    name: str,
    binding: ObservabilityBinding,
    *,
    base_dir: Path,
    agent_id: str,
) -> ObservabilityPlugin | None:
    variant = get_registry().resolve_by_type("observability", name)
    if variant is None:
        return None

    plugin_cls = variant.load_class()
    builder = getattr(plugin_cls, "from_binding", None)
    if callable(builder):
        return builder(binding, base_dir=base_dir, agent_id=agent_id)
    return plugin_cls()


def build_observability_plugins(
    binding: ObservabilityBinding,
    *,
    base_dir: Path,
    agent_id: str | None = None,
) -> list[ObservabilityPlugin]:
    resolved_agent_id = agent_id or ""
    names = list(binding.plugins)

    plugins: list[ObservabilityPlugin] = []
    for name in names:
        plugin = _load_observability_plugin(name, binding, base_dir=base_dir, agent_id=resolved_agent_id)
        if plugin is not None:
            plugins.append(plugin)
    return plugins


@dataclass
class ObsPluginSet:
    """Lifecycle wrapper for a set of instantiated observability plugins."""

    plugins: list[ObservabilityPlugin] = field(default_factory=list)
    _operator: ObservabilityOperator | None = field(default=None, init=False, repr=False)
    _all_operators: list = field(default_factory=list, init=False, repr=False)
    _mas_call_id: str = field(default="", init=False, repr=False)
    _run_started: bool = field(default=False, init=False, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)

    def subscribe_to(self, op: ObservabilityOperator, *, agent_id: str, run_id: str = "") -> None:
        if op in self._all_operators:
            return
        for plugin in self.plugins:
            op.subscribe(plugin)
        op.enable_async_plugins()
        op.set_context(agent_id=agent_id, run_id=run_id or hostenv.get("UI_RUN_ID", ""))
        self._all_operators.append(op)
        if self._operator is None:
            self._operator = op

    def begin_run(self, op: ObservabilityOperator) -> None:
        if self._run_started:
            return
        self._run_started = True
        self._operator = op
        if op not in self._all_operators:
            self._all_operators.append(op)
        self._mas_call_id = str(uuid.uuid4())
        op.push_call_frame(self._mas_call_id)
        op.record_session("mas_call_start", call_id=self._mas_call_id)
        # record_session dispatches asynchronously (enable_async_plugins was
        # already called by subscribe_to), so without draining here there is
        # no happens-before guarantee that mas_call_start has actually been
        # processed — and so no guarantee the shared plugin's run-global
        # mas_call_id has propagated — before any agent's own turn starts.
        # Every delegated agent's own async worker thread reads that shared
        # state; racing ahead of it
        # left a delegate's very first execution_start permanently missing
        # its parent_call_id. Draining here (once, at setup, off the hot
        # path) establishes that ordering for every agent sharing this set.
        op.drain_plugin_queue()

    def end_run(self) -> None:
        op = self._operator
        if op is None or not self._mas_call_id:
            return
        op.record_session("mas_call_end", call_id=self._mas_call_id, status="success")
        op.pop_call_frame(self._mas_call_id)
        self._mas_call_id = ""
        self._run_started = False

    def flush(self) -> None:
        for op in self._all_operators:
            op.drain_plugin_queue()
        for plugin in self.plugins:
            plugin.flush()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.end_run()
        self.flush()
        for plugin in self.plugins:
            plugin.close()
        for op in self._all_operators:
            op.shutdown_plugin_worker()


def _transport_plugin_defaults() -> dict[str, ProtocolTransportConfig]:
    registry = get_registry()
    out: dict[str, ProtocolTransportConfig] = {}
    for entry in registry.get_by_category("transport_classifier"):
        attrs = entry.attributes or {}
        name = entry.urn.rsplit(".", 1)[-1]
        out[name] = ProtocolTransportConfig(
            sdk_spans=bool(attrs.get("default_sdk_spans", False)),
            propagate_metadata=bool(attrs.get("default_propagate_metadata", False)),
        )
    return out


def _bind_transport(config: TransportObservabilityConfig) -> None:
    defaults = _transport_plugin_defaults()
    known = set(defaults)
    unknown = sorted(set(config.protocols) - known)
    if unknown and config.present:
        raise ValueError(
            f"unknown transport protocol(s) {unknown}; register a transport_classifier plugin or omit them"
        )
    merged = TransportObservabilityConfig(
        present=config.present,
        defaults=config.defaults,
        protocols=config.protocols,
        plugin_defaults={**defaults, **dict(config.plugin_defaults)},
    )
    bind_config(merged)


def _resolved_transport(binding: ObservabilityBinding) -> TransportObservabilityConfig:
    if not binding.transport.present and binding.plugins:
        return TransportObservabilityConfig.from_plugins(binding.plugins, binding.plugin_configs)
    return binding.transport


def bind_transport_if_idle(binding: ObservabilityBinding | None) -> None:
    """Embedders: bind the process bus from *binding* unless one is already bound.

    Never turns the bus off. A child instance built without a binding, or with its
    own spec, must not undo or replace what the session setup installed.
    """
    from mas.runtime.transport.emit import is_enabled

    if binding is None or is_enabled():
        return
    _bind_transport(_resolved_transport(binding))


def build_observability_plugin_set(
    binding: ObservabilityBinding | None,
    *,
    base_dir: Path,
    agent_id: str | None = None,
) -> ObsPluginSet | None:
    """Build one :class:`ObsPluginSet` from *binding*, or ``None`` if there's
    nothing to build (no binding, or a binding with an explicitly empty
    plugin list).

    This is the only function that should ever turn an ``ObservabilityBinding``
    into instantiated plugins. Callers that need the set attached to one or
    more :class:`~mas.runtime.driver.instance.RuntimeInstance` should follow
    up with :func:`attach_observability_plugin_set`.
    """
    if binding is None:
        _bind_transport(TransportObservabilityConfig())
        return None
    binding = replace(binding, transport=_resolved_transport(binding))
    # Always bound: a run with no transport config must turn the process bus off, not inherit the last run's.
    _bind_transport(binding.transport)
    if not binding.plugins:
        return None
    plugins = build_observability_plugins(binding, base_dir=base_dir, agent_id=agent_id or "")
    return ObsPluginSet(plugins=plugins)


def attach_observability_plugin_set(
    plugin_set: ObsPluginSet,
    instance: "RuntimeInstance",
    *,
    agent_id: str,
    begin_run: bool = True,
) -> None:
    """Subscribe *plugin_set* to *instance*'s operator and record it on the
    instance. A no-op (beyond recording the set) if observability is
    explicitly disabled on this instance (``driver.observability is None``)
    — never re-enables what was deliberately turned off.

    ``begin_run`` is safe to pass ``True`` from every caller sharing one
    *plugin_set* across several instances: :meth:`ObsPluginSet.begin_run` is
    itself idempotent, so only the first attach actually starts the run.
    """
    driver = instance.driver
    op = driver.observability
    instance.obs_plugin_set = plugin_set
    if op is None:
        return
    if not isinstance(op, ObservabilityOperator):
        op = ObservabilityOperator()
        driver.observability = op
    plugin_set.subscribe_to(op, agent_id=agent_id)
    if begin_run:
        plugin_set.begin_run(op)


__all__ = [
    "ObsPluginSet",
    "build_observability_plugins",
    "build_observability_plugin_set",
    "attach_observability_plugin_set",
]
