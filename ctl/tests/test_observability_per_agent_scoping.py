#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""FT8 — per-agent observability scoping.

An agent manifest's own non-empty ``spec.observability`` already causes
``RuntimeInstance.from_spec`` to build and subscribe a private plugin set at
materialization time (see ``mas.runtime.spec.parser.parse_agent_spec``).
Before this fix, ``setup_shared_obs`` blindly attached the run's shared set to
every instance regardless, so a self-scoped agent's operator ended up
subscribed to *two* plugin sets (its own + the shared one) instead of being
cleanly isolated. These tests cover the partition/finalize helpers that fix
that.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import SimpleNamespace

from mas.ctl.adapters.obs.config import ObservabilityConfig
from mas.ctl.session.observability import (
    finalize_scoped_observability,
    partition_instances_by_observability,
    setup_shared_obs,
)
from mas.runtime.boundary.obs.binding import ObservabilityBinding
from mas.runtime.boundary.obs.operator import ObservabilityOperator
from mas.runtime.boundary.obs.plugins import ObsPluginSet, build_observability_plugins


@dataclass
class _FakeDriver:
    observability: ObservabilityOperator = field(default_factory=ObservabilityOperator)
    ctx: object | None = None


@dataclass
class _FakeInstance:
    driver: _FakeDriver = field(default_factory=_FakeDriver)
    obs_plugin_set: object | None = None


def _own_plugin_set(tmp_path, filename: str) -> ObsPluginSet:
    binding = ObservabilityBinding(plugins=["native"], plugin_configs={"native": {"path": filename}})
    plugins = build_observability_plugins(binding, base_dir=tmp_path, agent_id="agent")
    return ObsPluginSet(plugins=plugins)


def _default_native_plugin_set(tmp_path) -> ObsPluginSet:
    binding = ObservabilityBinding(plugins=["native"])
    plugins = build_observability_plugins(binding, base_dir=tmp_path, agent_id="agent")
    return ObsPluginSet(plugins=plugins)


def test_partition_splits_by_existing_plugin_set(tmp_path) -> None:
    scoped_instance = _FakeInstance(obs_plugin_set=_own_plugin_set(tmp_path, "scoped.jsonl"))
    default_instance = _FakeInstance()

    shared, scoped = partition_instances_by_observability(
        {"specialist": scoped_instance, "moderator": default_instance}
    )

    assert list(shared) == ["moderator"]
    assert list(scoped) == ["specialist"]


def test_partition_missing_obs_plugin_set_joins_shared() -> None:
    """Compose/bench stubs without obs_plugin_set join the shared run, not crash."""
    stub = SimpleNamespace(driver=SimpleNamespace(observability=None))
    shared, scoped = partition_instances_by_observability({"step-a": stub, "step-b": stub})
    assert list(shared) == ["step-a", "step-b"]
    assert scoped == {}


def test_default_native_plugin_set_joins_shared(tmp_path) -> None:
    own_set = _default_native_plugin_set(tmp_path)
    planner = _FakeInstance(obs_plugin_set=own_set)
    own_set.subscribe_to(planner.driver.observability, agent_id="planner")
    specialist = _FakeInstance(obs_plugin_set=_own_plugin_set(tmp_path, "scoped.jsonl"))

    shared, scoped = partition_instances_by_observability(
        {"planner": planner, "specialist": specialist}
    )

    assert list(shared) == ["planner"]
    assert list(scoped) == ["specialist"]
    assert planner.obs_plugin_set is None
    assert planner.driver.observability._subscribers == []


def test_native_default_relative_path_joins_shared_sink(tmp_path) -> None:
    """observability-native.yaml patches path: traces/events.jsonl — still shared."""
    own_set = _own_plugin_set(tmp_path, "traces/events.jsonl")
    planner = _FakeInstance(obs_plugin_set=own_set)
    own_set.subscribe_to(planner.driver.observability, agent_id="planner")

    shared, scoped = partition_instances_by_observability({"planner": planner})
    config = ObservabilityConfig(enabled=True, plugins=["native"], events_file="shared.jsonl")
    shared_set = setup_shared_obs(shared, config, base_dir=tmp_path, entry_agent_id="planner")

    assert scoped == {}
    assert shared_set is not None
    assert planner.obs_plugin_set is shared_set
    assert len(planner.driver.observability._subscribers) == len(shared_set.plugins)


def test_scoped_instance_not_double_subscribed_by_shared_set(tmp_path) -> None:
    """Reproduces the pre-fix bug: without partitioning, setup_shared_obs would
    attach a second plugin set to an already-self-scoped instance's operator.
    """
    own_set = _own_plugin_set(tmp_path, "own.jsonl")
    scoped_instance = _FakeInstance(obs_plugin_set=own_set)
    own_set.subscribe_to(scoped_instance.driver.observability, agent_id="specialist")

    plain_instance = _FakeInstance()

    instances = {"specialist": scoped_instance, "moderator": plain_instance}
    shared, scoped = partition_instances_by_observability(instances)

    config = ObservabilityConfig(enabled=True, plugins=["native"], events_file="shared.jsonl")
    shared_set = setup_shared_obs(shared, config, base_dir=tmp_path, entry_agent_id="moderator")

    assert shared_set is not None
    # The scoped instance's operator must still be subscribed to only its own
    # plugin set — the shared set was never attached to it.
    assert len(scoped_instance.driver.observability._subscribers) == len(own_set.plugins)
    assert scoped_instance.obs_plugin_set is own_set
    # The non-scoped instance did join the shared set.
    assert plain_instance.obs_plugin_set is shared_set


def test_finalize_scoped_observability_emits_isolated_trace(tmp_path) -> None:
    own_set = _own_plugin_set(tmp_path, "own.jsonl")
    scoped_instance = _FakeInstance(obs_plugin_set=own_set)
    own_set.subscribe_to(scoped_instance.driver.observability, agent_id="specialist")

    recorders = finalize_scoped_observability({"specialist": scoped_instance})
    assert len(recorders) == 1
    for recorder in recorders:
        recorder.close()

    events_path = tmp_path / "own.jsonl"
    kinds = [json.loads(line)["kind"] for line in events_path.read_text().splitlines() if line.strip()]
    assert kinds.count("mas_call_start") == 1
    assert kinds.count("mas_call_end") == 1

    shared_path = tmp_path / "shared.jsonl"
    assert not shared_path.exists()


def test_finalize_scoped_observability_skips_missing_plugin_set() -> None:
    assert finalize_scoped_observability({"a": _FakeInstance(obs_plugin_set=None)}) == []


def test_mixed_run_shared_and_scoped_both_emit_independently(tmp_path) -> None:
    """Regression-shaped coverage gap the review flagged: setup_shared_obs and
    finalize_scoped_observability were each tested in isolation, never in the
    same run together — a bug where finalizing scoped instances interferes
    with a concurrently shared operator (or vice versa) would not have been
    caught."""
    own_set = _own_plugin_set(tmp_path, "own.jsonl")
    scoped_instance = _FakeInstance(obs_plugin_set=own_set)
    own_set.subscribe_to(scoped_instance.driver.observability, agent_id="specialist")

    plain_instance = _FakeInstance()
    instances = {"specialist": scoped_instance, "moderator": plain_instance}
    shared, scoped = partition_instances_by_observability(instances)

    config = ObservabilityConfig(enabled=True, plugins=["native"], events_file="shared.jsonl")
    shared_set = setup_shared_obs(shared, config, base_dir=tmp_path, entry_agent_id="moderator")
    scoped_recorders = finalize_scoped_observability(scoped)

    assert shared_set is not None
    assert len(scoped_recorders) == 1
    for recorder in scoped_recorders:
        recorder.close()
    shared_set.close()

    own_path = tmp_path / "own.jsonl"
    shared_path = tmp_path / "shared.jsonl"
    assert own_path.exists()
    assert shared_path.exists()

    own_kinds = [json.loads(line)["kind"] for line in own_path.read_text().splitlines() if line.strip()]
    shared_kinds = [json.loads(line)["kind"] for line in shared_path.read_text().splitlines() if line.strip()]
    # Each run bracket recorded exactly once, in its own file — neither run's
    # events leaked into the other's sink.
    assert own_kinds.count("mas_call_start") == 1
    assert own_kinds.count("mas_call_end") == 1
    assert shared_kinds.count("mas_call_start") == 1
    assert shared_kinds.count("mas_call_end") == 1
