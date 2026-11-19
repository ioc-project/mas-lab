#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""build_observability_plugin_set / attach_observability_plugin_set — the two
functions that let ctl (or anything outside runtime) get an ObsPluginSet
wired to one or more RuntimeInstances without ever importing
build_observability_plugins or constructing ObsPluginSet itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mas.runtime.boundary.obs.binding import ObservabilityBinding
from mas.runtime.boundary.obs.operator import ObservabilityOperator
from mas.runtime.boundary.obs.plugins import (
    ObsPluginSet,
    attach_observability_plugin_set,
    build_observability_plugin_set,
)


@dataclass
class _FakeDriver:
    observability: ObservabilityOperator | None = field(default_factory=ObservabilityOperator)
    ctx: object | None = None


@dataclass
class _FakeInstance:
    driver: _FakeDriver = field(default_factory=_FakeDriver)
    obs_plugin_set: object | None = None


def test_build_observability_plugin_set_returns_none_for_none_binding(tmp_path) -> None:
    from mas.runtime.transport import emit
    from mas.runtime.transport.config import ProtocolTransportConfig, TransportObservabilityConfig

    emit.reset()
    emit.bind_config(TransportObservabilityConfig(present=True, defaults=ProtocolTransportConfig()))
    assert build_observability_plugin_set(None, base_dir=tmp_path) is None
    assert emit.is_enabled() is False
    emit.reset()


def test_build_observability_plugin_set_returns_none_for_empty_plugin_list(tmp_path) -> None:
    binding = ObservabilityBinding(plugins=[])
    assert build_observability_plugin_set(binding, base_dir=tmp_path) is None


def test_build_observability_plugin_set_builds_real_plugins(tmp_path) -> None:
    binding = ObservabilityBinding(
        plugins=["native"],
        plugin_configs={"native": {"path": "events.jsonl"}},
    )
    plugin_set = build_observability_plugin_set(binding, base_dir=tmp_path, agent_id="planner")
    assert isinstance(plugin_set, ObsPluginSet)
    assert len(plugin_set.plugins) == 1


def test_attach_observability_plugin_set_subscribes_and_begins_run(tmp_path) -> None:
    binding = ObservabilityBinding(
        plugins=["native"],
        plugin_configs={"native": {"path": "events.jsonl"}},
    )
    plugin_set = build_observability_plugin_set(binding, base_dir=tmp_path, agent_id="planner")
    instance = _FakeInstance()

    attach_observability_plugin_set(plugin_set, instance, agent_id="planner")

    assert instance.obs_plugin_set is plugin_set
    assert plugin_set._run_started
    assert instance.driver.observability in plugin_set._all_operators


def test_attach_observability_plugin_set_shared_across_instances_begins_run_once(tmp_path) -> None:
    binding = ObservabilityBinding(
        plugins=["native"],
        plugin_configs={"native": {"path": "events.jsonl"}},
    )
    shared = build_observability_plugin_set(binding, base_dir=tmp_path, agent_id="entry")
    inst_a = _FakeInstance()
    inst_b = _FakeInstance()

    attach_observability_plugin_set(shared, inst_a, agent_id="agent-a")
    attach_observability_plugin_set(shared, inst_b, agent_id="agent-b")

    assert inst_a.obs_plugin_set is shared and inst_b.obs_plugin_set is shared
    assert len(shared._all_operators) == 2
    # begin_run is idempotent -- attaching a second instance must not start
    # a second run or raise.
    assert shared._run_started


def test_attach_observability_plugin_set_respects_explicit_disablement(tmp_path) -> None:
    """driver.observability is None -> instrumentation was explicitly turned
    off for this instance; attaching must record the set for bookkeeping but
    never fabricate an operator to re-enable it."""
    binding = ObservabilityBinding(plugins=["native"], plugin_configs={"native": {"path": "events.jsonl"}})
    plugin_set = build_observability_plugin_set(binding, base_dir=tmp_path, agent_id="planner")
    instance = _FakeInstance(driver=_FakeDriver(observability=None))

    attach_observability_plugin_set(plugin_set, instance, agent_id="planner")

    assert instance.obs_plugin_set is plugin_set
    assert plugin_set._all_operators == []
    assert not plugin_set._run_started


def test_from_parts_with_native_binding_enables_the_bus_and_binds_a_sink(tmp_path) -> None:
    """Mutation: from_parts used build_observability_plugins, so bind_config never ran."""
    from mas.runtime.driver.instance import RuntimeInstance
    from mas.runtime.transport import emit

    emit.reset()
    inst = RuntimeInstance.from_parts(
        obs_binding=ObservabilityBinding(
            plugins=["native"],
            plugin_configs={"native": {"path": str(tmp_path / "events.jsonl")}},
        ),
        obs_base_dir=tmp_path,
        obs_agent_id="planner",
    )
    try:
        assert emit.is_enabled()
        assert inst.obs_plugin_set is not None
        assert inst.obs_plugin_set.plugins[0] in emit._fanout._sinks  # noqa: SLF001
    finally:
        if inst.obs_plugin_set is not None:
            inst.obs_plugin_set.close()
        emit.reset()


def test_child_instance_without_binding_leaves_the_session_bus_alone(tmp_path) -> None:
    """Mutation: from_parts() turned the process bus off, silencing every agent spawned after the session was set up."""
    from mas.runtime.driver.instance import RuntimeInstance
    from mas.runtime.transport import emit
    from mas.runtime.transport.config import ProtocolTransportConfig, TransportObservabilityConfig

    emit.reset()
    session = TransportObservabilityConfig(present=True, defaults=ProtocolTransportConfig())
    emit.bind_config(session)
    RuntimeInstance.from_parts()
    assert emit.is_enabled() is True
    assert emit._config is session  # noqa: SLF001
    emit.reset()


def test_rebuild_without_attach_does_not_add_a_second_sink(tmp_path) -> None:
    """Mutation: conversation rebuild constructed plugins and bind_sink'd them beside the parent."""
    from mas.runtime.driver.instance import RuntimeInstance
    from mas.runtime.transport import emit

    emit.reset()
    binding = ObservabilityBinding(
        plugins=["native"],
        plugin_configs={"native": {"path": str(tmp_path / "events.jsonl")}},
    )
    parent = RuntimeInstance.from_parts(
        obs_binding=binding,
        obs_base_dir=tmp_path,
        obs_agent_id="planner",
    )
    try:
        assert len(emit._fanout._sinks) == 1  # noqa: SLF001
        child = RuntimeInstance.from_parts(
            obs_binding=binding,
            obs_base_dir=tmp_path,
            obs_agent_id="planner",
            attach_obs_plugins=False,
        )
        assert child.obs_plugin_set is None
        assert len(emit._fanout._sinks) == 1  # noqa: SLF001
        assert emit.is_enabled()
    finally:
        if parent.obs_plugin_set is not None:
            parent.obs_plugin_set.close()
        emit.reset()


def test_child_instance_with_its_own_binding_does_not_replace_the_session_config(tmp_path) -> None:
    """Mutation: a child spec with its own obs block rebound the process config the CLI had set."""
    from mas.runtime.driver.instance import RuntimeInstance
    from mas.runtime.transport import emit
    from mas.runtime.transport.config import ProtocolTransportConfig, TransportObservabilityConfig

    emit.reset()
    session = TransportObservabilityConfig(present=True, defaults=ProtocolTransportConfig())
    emit.bind_config(session)
    inst = RuntimeInstance.from_parts(
        obs_binding=ObservabilityBinding(
            plugins=["native"],
            plugin_configs={"native": {"path": str(tmp_path / "child.jsonl")}},
        ),
        obs_base_dir=tmp_path,
        obs_agent_id="child",
    )
    try:
        assert emit._config is session  # noqa: SLF001
    finally:
        if inst.obs_plugin_set is not None:
            inst.obs_plugin_set.close()
        emit.reset()
