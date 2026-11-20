#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``native.transport.defaults.enabled`` switches only the ``transport.*`` events, never the events file."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from mas.ctl.session.manifest_config import observability_config_from_manifest
from mas.ctl.session.observability import setup_instance_obs
from mas.runtime.boundary.obs.operator import ObservabilityOperator
from mas.runtime.schema.transport import MESSAGE_SENT, SOURCE_HOOK, TransportEvent
from mas.runtime.transport import emit


@dataclass
class _Driver:
    observability: ObservabilityOperator = field(default_factory=ObservabilityOperator)
    ctx: object | None = None


@dataclass
class _Instance:
    driver: _Driver = field(default_factory=_Driver)
    obs_plugin_set: object | None = None


@pytest.fixture(autouse=True)
def _reset_bus():
    yield
    emit.reset()


def _records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.mark.parametrize("transport_on", [False, True])
def test_events_file_exists_and_only_transport_events_follow_the_switch(tmp_path: Path, transport_on: bool):
    manifest = {
        "spec": {"observability": [{"native": {"transport": {"defaults": {"enabled": transport_on}}}}]},
    }
    events = tmp_path / "run" / "traces" / "events.jsonl"
    config = observability_config_from_manifest(manifest, cli_events_file=str(events))
    plugin_set = setup_instance_obs(_Instance(), config, base_dir=tmp_path, agent_id="a")
    assert plugin_set is not None
    emit.emit_transport(
        TransportEvent(
            kind=MESSAGE_SENT,
            protocol="example",
            side="client",
            correlation_key="k",
            source=SOURCE_HOOK,
            timestamp=1.0,
        )
    )
    plugin_set.close()
    assert events.is_file()
    kinds = [r.get("kind", "") for r in _records(events)]
    assert any(k.startswith("transport.") for k in kinds) is transport_on
    assert kinds, "kernel events stay in the file"
