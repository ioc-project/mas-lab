#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Wire observability from manifest config -> runtime instance.

Ctl converts config into an ``ObservabilityBinding`` (pure data) and asks
runtime to build/attach plugins via ``build_observability_plugin_set`` /
``attach_observability_plugin_set``. Default-native detection uses
``NativeObservabilityPlugin.events_jsonl_path``; plugin construction still
lives in runtime.
This module only orchestrates *when* that happens (single instance, a
pre-built dict of instances, or instances discovered lazily one at a time)
and wraps the result in ctl's own ``SessionObservabilityRecorder`` lifecycle
handle — it never imports ``build_observability_plugins`` or constructs an
``ObsPluginSet`` itself, the same way it never resolves a ``design_pattern``
plugin directly (that happens inside runtime's own
``mas.runtime.boundary.context.dp_inject``).
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from mas.ctl.adapters.obs.config import ObservabilityConfig
from mas.ctl.adapters.obs.session import SessionObservabilityRecorder
from mas.library.standard.plugins.observability.native_plugin import NativeObservabilityPlugin
from mas.runtime.boundary.obs.binding import ObservabilityBinding
from mas.runtime.boundary.obs.plugins import (
    ObsPluginSet,
    attach_observability_plugin_set,
    build_observability_plugin_set,
)

if TYPE_CHECKING:
    from mas.runtime.driver.instance import RuntimeInstance


def obs_config_to_binding(config: ObservabilityConfig) -> ObservabilityBinding | None:
    """Convert ctl ObservabilityConfig -> runtime ObservabilityBinding."""
    if not config.enabled:
        return None
    plugins = list(config.plugins) if config.plugins else ["native"]
    plugin_configs = dict(config.plugin_configs or {})
    if config.events_file:
        native_cfg = dict(plugin_configs.get("native") or {})
        native_cfg.setdefault("path", config.events_file)
        plugin_configs["native"] = native_cfg
    binding = ObservabilityBinding(
        plugins=plugins,
        plugin_configs=plugin_configs,
        events_file=config.events_file,
        stdout=config.events_stdout,
    )
    if config.mas_id:
        binding = binding.with_mas_id(config.mas_id)
    return binding


def setup_instance_obs(
    instance: "RuntimeInstance",
    config: ObservabilityConfig,
    *,
    base_dir: Path,
    agent_id: str | None = None,
) -> ObsPluginSet | None:
    """Build plugins, subscribe to instance's operator, and begin run. Returns plugin_set."""
    binding = obs_config_to_binding(config)
    effective_agent_id = agent_id or config.agent_id
    plugin_set = build_observability_plugin_set(binding, base_dir=base_dir, agent_id=effective_agent_id)
    if plugin_set is None:
        return None
    attach_observability_plugin_set(plugin_set, instance, agent_id=effective_agent_id)
    return plugin_set


def setup_shared_obs(
    instances: "dict[str, RuntimeInstance]",
    config: ObservabilityConfig,
    *,
    base_dir: Path,
    entry_agent_id: str,
) -> ObsPluginSet | None:
    """One plugin set shared across all agents in a multi-agent run."""
    binding = obs_config_to_binding(config)
    shared_set = build_observability_plugin_set(binding, base_dir=base_dir, agent_id=entry_agent_id)
    if shared_set is None:
        return None
    for agent_id, instance in instances.items():
        attach_observability_plugin_set(shared_set, instance, agent_id=agent_id, begin_run=False)
    # begin_run on the entry agent's operator
    entry_instance = instances.get(entry_agent_id) or next(iter(instances.values()), None)
    if entry_instance is not None and entry_instance.driver.observability is not None:
        shared_set.begin_run(entry_instance.driver.observability)
    return shared_set


def _is_default_native_plugin_set(plugin_set: ObsPluginSet) -> bool:
    """True when *plugin_set* is native-only writing the default traces/events.jsonl.

    Agent overlays such as ``observability-native`` and ``with-guardrail``
    declare ``observability: [native]`` (optionally with path
    ``traces/events.jsonl``). After Agent-on-MAS fan-out that list lands on
    every agency row, so materialization builds a private plugin set per
    agent. That is "enable native for this run", not an isolated sink — the
    ctl shared ``events.jsonl`` already covers it. A custom filename or an
    extra plugin (otel, …) stays scoped.
    """
    if len(plugin_set.plugins) != 1:
        return False
    plugin = plugin_set.plugins[0]
    if not isinstance(plugin, NativeObservabilityPlugin):
        return False
    path = plugin.events_jsonl_path()
    if path is None:
        return True
    return path.name == "events.jsonl" and path.parent.name == "traces"


def _plugin_set_of(instance: object) -> ObsPluginSet | None:
    """Private plugin set on a runtime instance, or ``None`` if the instance has none.

    ``RuntimeInstance`` always declares ``obs_plugin_set``. Test and compose
    stubs may omit the field; they join the shared run, same as an instance
    whose set is explicitly ``None``.
    """
    try:
        plugin_set = instance.obs_plugin_set  # type: ignore[attr-defined]
    except AttributeError:
        return None
    return plugin_set


def _release_plugin_set(instance: "RuntimeInstance") -> None:
    """Unsubscribe a private set without ``close()`` (that would emit ``mas_call_end``)."""
    plugin_set = _plugin_set_of(instance)
    op = instance.driver.observability
    if plugin_set is not None and op is not None:
        for plugin in plugin_set.plugins:
            op.unsubscribe(plugin)
    instance.obs_plugin_set = None


def partition_instances_by_observability(
    instances: "dict[str, RuntimeInstance]",
) -> "tuple[dict[str, RuntimeInstance], dict[str, RuntimeInstance]]":
    """Split materialized *instances* into the shared-set group and self-scoped agents.

    Materialization (``RuntimeInstance.from_spec``) already builds a private
    plugin set from an agent's own non-empty ``spec.observability`` — see
    ``mas.runtime.spec.parser.parse_agent_spec``, which returns a binding only
    when that agent's manifest declares a non-empty plugin list. An instance
    that already carries a *custom* set (``instance.obs_plugin_set is not None``
    and not default-native) must be left alone: folding it into the shared set
    via :func:`setup_shared_obs` would attach a *second* plugin set to the same
    operator (subscribing is additive, not a replacement — see
    :meth:`ObsPluginSet.subscribe_to`), so its events would land in both its
    own sink and the shared events.jsonl instead of being cleanly scoped to
    just its own. Default ``native`` / ``traces/events.jsonl`` is released and
    joins the shared set. Instances with no self-declared observability (the
    common case) join the shared set as before.
    """
    shared: "dict[str, RuntimeInstance]" = {}
    scoped: "dict[str, RuntimeInstance]" = {}
    for agent_id, instance in instances.items():
        plugin_set = _plugin_set_of(instance)
        if plugin_set is not None and not _is_default_native_plugin_set(plugin_set):
            scoped[agent_id] = instance
            continue
        if plugin_set is not None:
            _release_plugin_set(instance)
        shared[agent_id] = instance
    return shared, scoped


def finalize_scoped_observability(
    scoped: "dict[str, RuntimeInstance]",
) -> list[SessionObservabilityRecorder]:
    """Begin the run bracket on each self-scoped instance's own plugin set.

    Counterpart to :func:`partition_instances_by_observability`: those instances
    were already built with their own plugin set attached at materialization
    time but never had ``begin_run`` called (materialization only subscribes;
    ctl decides when a run starts). Returns one recorder per instance so
    callers close (and thereby flush) each private sink at run end, the same
    way :func:`setup_shared_obs`'s caller closes the shared one.
    """
    recorders: list[SessionObservabilityRecorder] = []
    for instance in scoped.values():
        plugin_set = _plugin_set_of(instance)
        if plugin_set is None:
            continue
        op = instance.driver.observability
        if op is not None:
            plugin_set.begin_run(op)
        recorders.append(SessionObservabilityRecorder(plugin_set=plugin_set))
    return recorders


def setup_run_observability(
    instances: "dict[str, RuntimeInstance]",
    obs_config: ObservabilityConfig | None,
    *,
    base_dir: Path,
    entry_agent_id: str,
) -> "tuple[ObsPluginSet | None, list[SessionObservabilityRecorder]]":
    """Partition *instances*, wire the shared set, and begin every self-scoped
    instance's own set — the full per-run observability setup shared by
    single-agent and sequential-workflow execution (see ``run_mas.py``).

    Returns ``(shared_plugin_set, scoped_recorders)``; both are empty/None
    when *obs_config* is ``None`` (observability disabled for this run).
    """
    if obs_config is None:
        return None, []
    shared_instances, scoped_instances = partition_instances_by_observability(instances)
    plugin_set = None
    if shared_instances:
        plugin_set = setup_shared_obs(
            shared_instances, obs_config, base_dir=base_dir, entry_agent_id=entry_agent_id
        )
    return plugin_set, finalize_scoped_observability(scoped_instances)


# ---------------------------------------------------------------------------
# Backward compatibility — legacy callers still used by runner.py and tests
# ---------------------------------------------------------------------------


def setup_observability(
    instance: "RuntimeInstance",
    config: ObservabilityConfig,
    *,
    base_dir: Path,
) -> SessionObservabilityRecorder | None:
    """Legacy path: ObservabilityConfig → runtime plugins → ObsPluginSet.

    If ``from_spec`` already built a plugin set from the agent overlay, reuse
    it. Subscribing a second set on the same operator duplicates every span.
    """
    existing = _plugin_set_of(instance)
    if existing is not None and existing.plugins:
        return SessionObservabilityRecorder(plugin_set=existing)
    binding = obs_config_to_binding(config)
    plugin_set = build_observability_plugin_set(binding, base_dir=base_dir, agent_id=config.agent_id)
    if plugin_set is None:
        return None
    attach_observability_plugin_set(plugin_set, instance, agent_id=config.agent_id)
    return SessionObservabilityRecorder(plugin_set=plugin_set)


def create_shared_observability(
    config: ObservabilityConfig,
    *,
    base_dir: Path,
):
    """Shared plugin set for multi-agent MAS runs (shared events.jsonl).

    Returns (shared_set, setup_fn) where shared_set.close() ends the run.
    setup_fn(instance, agent_id) subscribes the instance and returns a recorder.
    """
    binding = obs_config_to_binding(config)
    entry_agent_id = config.agent_id
    shared_set = build_observability_plugin_set(binding, base_dir=base_dir, agent_id=entry_agent_id)
    if shared_set is None:
        return None, None

    def setup(instance: "RuntimeInstance", agent_id: str) -> SessionObservabilityRecorder:
        # begin_run is idempotent (see ObsPluginSet.begin_run) -- passing
        # begin_run=True here is safe even though several instances share
        # this same plugin_set; only the first attach actually starts the run.
        attach_observability_plugin_set(shared_set, instance, agent_id=agent_id)
        return SessionObservabilityRecorder(plugin_set=shared_set, owns_plugin_set=False)

    return shared_set, setup


def setup_observability_from_binding(
    instance: "RuntimeInstance",
    binding: ObservabilityBinding,
    *,
    base_dir: Path,
    agent_id: str = "",
) -> SessionObservabilityRecorder | None:
    """New path: ObservabilityBinding → runtime plugins → ObsPluginSet."""
    if binding is None or not binding.plugins:
        return None

    plugin_set = _plugin_set_of(instance)
    if plugin_set is None:
        plugin_set = build_observability_plugin_set(binding, base_dir=base_dir, agent_id=agent_id)
        if plugin_set is None:
            return None

    attach_observability_plugin_set(plugin_set, instance, agent_id=agent_id)
    return SessionObservabilityRecorder(plugin_set=plugin_set)
