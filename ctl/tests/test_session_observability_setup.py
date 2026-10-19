#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Coverage for ctl.session.observability setup glue after the switch to
runtime's build/attach_observability_plugin_set delegation."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from mas.ctl.adapters.obs.config import ObservabilityConfig
from mas.ctl.session.observability import (
    obs_config_to_binding,
    setup_instance_obs,
    setup_observability,
    setup_observability_from_binding,
    setup_shared_obs,
)
from mas.runtime.boundary.obs.binding import ObservabilityBinding
from mas.runtime.boundary.obs.operator import ObservabilityOperator


@dataclass
class _FakeDriver:
    observability: ObservabilityOperator = field(default_factory=ObservabilityOperator)
    ctx: object | None = None


@dataclass
class _FakeInstance:
    driver: _FakeDriver = field(default_factory=_FakeDriver)
    obs_plugin_set: object | None = None


def test_obs_config_to_binding_disabled_returns_none() -> None:
    assert obs_config_to_binding(ObservabilityConfig(enabled=False)) is None


def test_obs_config_to_binding_defaults_native_and_events_path(tmp_path: Path) -> None:
    cfg = ObservabilityConfig(enabled=True, events_file=str(tmp_path / "e.jsonl"))
    binding = obs_config_to_binding(cfg)
    assert binding is not None
    assert binding.plugins == ["native"]
    assert binding.plugin_configs["native"]["path"] == str(tmp_path / "e.jsonl")


def test_obs_config_to_binding_injects_mas_id_into_otel_config() -> None:
    cfg = ObservabilityConfig(
        enabled=True,
        plugins=["otel", "native"],
        mas_id="sample-app",
    )
    binding = obs_config_to_binding(cfg)
    assert binding is not None
    assert binding.plugin_configs["otel"]["app_name"] == "sample-app"
    assert binding.plugin_configs["otel"]["mas_id"] == "sample-app"
    assert binding.plugin_configs["native"]["app_name"] == "sample-app"
    assert binding.plugin_configs["native"]["mas_id"] == "sample-app"


def test_with_mas_id_overwrites_blank_plugin_config() -> None:
    binding = ObservabilityBinding(
        plugins=["otel"],
        plugin_configs={"otel": {"app_name": "", "mas_id": "  "}},
    )
    stamped = binding.with_mas_id("sample-app")
    assert stamped.plugin_configs["otel"]["app_name"] == "sample-app"
    assert stamped.plugin_configs["otel"]["mas_id"] == "sample-app"


def test_obs_config_to_binding_rejects_path_mas_id() -> None:
    cfg = ObservabilityConfig(enabled=True, plugins=["otel"], mas_id="/opt/apps/trip-planner")
    with pytest.raises(ValueError, match="filesystem path"):
        obs_config_to_binding(cfg)


def test_with_mas_id_rejects_path() -> None:
    binding = ObservabilityBinding(plugins=["otel"])
    with pytest.raises(ValueError, match="filesystem path"):
        binding.with_mas_id("/opt/apps/trip-planner")


def test_setup_instance_obs_builds_and_attaches(tmp_path: Path) -> None:
    inst = _FakeInstance()
    cfg = ObservabilityConfig(enabled=True, plugins=["native"], agent_id="planner")
    plugin_set = setup_instance_obs(inst, cfg, base_dir=tmp_path)
    assert plugin_set is not None
    assert inst.obs_plugin_set is plugin_set
    plugin_set.close()


def test_setup_instance_obs_disabled_returns_none(tmp_path: Path) -> None:
    assert setup_instance_obs(_FakeInstance(), ObservabilityConfig(enabled=False), base_dir=tmp_path) is None


def test_setup_shared_obs_attaches_all_and_begins_run(tmp_path: Path) -> None:
    a, b = _FakeInstance(), _FakeInstance()
    cfg = ObservabilityConfig(enabled=True, plugins=["native"], agent_id="a")
    shared = setup_shared_obs({"a": a, "b": b}, cfg, base_dir=tmp_path, entry_agent_id="a")
    assert shared is not None
    assert a.obs_plugin_set is shared and b.obs_plugin_set is shared
    shared.close()


def test_setup_shared_obs_disabled_returns_none(tmp_path: Path) -> None:
    assert setup_shared_obs({}, ObservabilityConfig(enabled=False), base_dir=tmp_path, entry_agent_id="a") is None


def test_setup_observability_legacy_returns_recorder(tmp_path: Path) -> None:
    inst = _FakeInstance()
    cfg = ObservabilityConfig(enabled=True, plugins=["native"], agent_id="planner")
    rec = setup_observability(inst, cfg, base_dir=tmp_path)
    assert rec is not None
    rec.plugin_set.close()


def test_setup_observability_reuses_existing_plugin_set(tmp_path: Path) -> None:
    inst = _FakeInstance()
    cfg = ObservabilityConfig(enabled=True, plugins=["native"], agent_id="qa-agent")
    first = setup_instance_obs(inst, cfg, base_dir=tmp_path)
    rec = setup_observability(inst, cfg, base_dir=tmp_path)
    assert rec is not None
    assert rec.plugin_set is first
    first.close()


def test_setup_observability_from_binding_reuses_existing_set(tmp_path: Path) -> None:
    inst = _FakeInstance()
    binding = ObservabilityBinding(plugins=["native"])
    rec = setup_observability_from_binding(inst, binding, base_dir=tmp_path, agent_id="planner")
    assert rec is not None
    assert inst.obs_plugin_set is rec.plugin_set
    rec.plugin_set.close()


def test_setup_observability_from_binding_empty_plugins_returns_none(tmp_path: Path) -> None:
    rec = setup_observability_from_binding(
        _FakeInstance(), ObservabilityBinding(plugins=[]), base_dir=tmp_path
    )
    assert rec is None
