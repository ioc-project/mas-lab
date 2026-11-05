#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for compose_run agent binding."""

from __future__ import annotations

from pathlib import Path

import pytest
from mas.ctl.compose.runner import ComposeRequest, compose_run


def _mas_lab_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _library_samples_manifest() -> Path | None:
    manifest = _mas_lab_root() / "library-samples" / "apps" / "trip-planner" / "mas.yaml"
    return manifest if manifest.is_file() else None


def test_compose_run_multi_agent_bind_from_library_samples():
    manifest = _library_samples_manifest()
    if manifest is None:
        pytest.skip("library-samples trip-planner not in workspace")

    result = compose_run(
        ComposeRequest(
            manifest=manifest,
            validate=False,
            infra_refs=["standard:openai"],
        )
    )

    agents = result.effective_bind.get("agents") or []
    assert len(agents) >= 2
    agent_ids = {a["agent_id"] for a in agents}
    assert {"moderator", "schedule_agent", "itinerary_agent", "concierge_agent"}.issubset(agent_ids)


def test_compose_run_two_agent_fixture(tmp_path: Path):
    agent_a = tmp_path / "agents" / "alpha.yaml"
    agent_b = tmp_path / "agents" / "beta.yaml"
    agent_a.parent.mkdir(parents=True)
    for path, name in ((agent_a, "alpha"), (agent_b, "beta")):
        path.write_text(
            f"""apiVersion: mas/v1
kind: Agent
metadata:
  name: {name}
spec:
  description: test agent
  models:
    - model: gpt-4o-mini
""",
            encoding="utf-8",
        )

    mas_path = tmp_path / "mas.yaml"
    mas_path.write_text(
        """apiVersion: mas/v1
kind: MAS
metadata:
  name: two-agent-fixture
spec:
  agency:
    agents:
      - id: alpha
        ref: agents/alpha.yaml
      - id: beta
        ref: agents/beta.yaml
  workflow:
    type: sequential
    entry: alpha
    nodes:
      - id: alpha
        role: specialist
        delegates_to:
          - beta
      - id: beta
        role: specialist
""",
        encoding="utf-8",
    )

    result = compose_run(
        ComposeRequest(
            manifest=mas_path,
            validate=False,
            infra_refs=["standard:openai"],
        )
    )

    agents = result.effective_bind.get("agents") or []
    assert len(agents) == 2
    assert {a["agent_id"] for a in agents} == {"alpha", "beta"}


def test_compose_run_library_app_keeps_experiment_workspace_infra(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A library-backed app must not replace the experiment's workspace config."""
    workspace_root = tmp_path / "experiment"
    workspace_root.mkdir()
    (workspace_root / "config.yaml").write_text(
        "infra_refs: [standard:openai]\n",
        encoding="utf-8",
    )

    user_config_root = tmp_path / "user-config"
    (user_config_root / "mas").mkdir(parents=True)
    (user_config_root / "mas" / "config.yaml").write_text(
        "default_infra: standard:production\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(user_config_root))

    library_app = tmp_path / "example-library" / "apps" / "example-app" / "v2"
    agent = library_app / "agents" / "example-app.yaml"
    agent.parent.mkdir(parents=True)
    agent.write_text(
        """apiVersion: mas/v1
kind: Agent
metadata:
  name: example-app
spec:
  description: test agent
  models:
    - model: gpt-4o-mini
""",
        encoding="utf-8",
    )
    mas_path = library_app / "mas.yaml"
    mas_path.write_text(
        """apiVersion: mas/v1
kind: MAS
metadata:
  name: example-app
spec:
  agency:
    agents:
      - id: example-app
        ref: agents/example-app.yaml
  workflow:
    type: sequential
    entry: example-app
    nodes:
      - id: example-app
        role: specialist
""",
        encoding="utf-8",
    )

    result = compose_run(
        ComposeRequest(
            manifest=mas_path,
            workspace_root=workspace_root,
            validate=False,
        )
    )

    assert result.infra_refs == ["standard:openai"]


def test_inproc_bus_transport_handoff():
    from mas.ctl.placement.bus.adapter import RuntimeCommEndpoint
    from mas.ctl.placement.bus.inproc import InProcessCommBus
    from mas.runtime.schema.egress import InvokeEngineIo

    delivered = []

    class _Inst:
        def feed(self, payload):
            delivered.append(payload.response_kind)

    bus = InProcessCommBus()
    bus.register("alpha", RuntimeCommEndpoint("alpha", _Inst()))
    bus.register("beta", RuntimeCommEndpoint("beta", _Inst()))
    result = bus.send(
        from_agent="alpha",
        to_agent="beta",
        intent=InvokeEngineIo(correlation_id=1, op="TRANSPORT_MSG"),
    )
    assert result is not None
    assert result.response_kind == "TRANSPORT_ACK"
    assert delivered == ["TRANSPORT_ACK"]
