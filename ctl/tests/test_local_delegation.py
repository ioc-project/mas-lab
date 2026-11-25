#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""An agent whose design pattern sends reaches its peers in one process."""

from __future__ import annotations

from unittest.mock import patch

from mas.ctl.compose.runner import ComposeRequest, compose_run
from mas.ctl.executor.mas_session import make_workflow_send, materialize_mas_compose
from mas.ctl.executor.run_mas import _QuietDisplay
from mas.ctl.session.engine_factory import EngineSelection
from mas.runtime.engine.simulated import SimulatedEngine

AGENT = """apiVersion: mas/v1
kind: Agent
metadata:
  name: {name}
spec:
  description: test
  models:
    - model: gpt-4o-mini
{pattern}"""

MODERATOR = "  design_pattern:\n    type: deterministic_parallel\n"

MAS = """apiVersion: mas/v1
kind: MAS
metadata:
  name: fan-out
spec:
  agency:
    agents:
      - id: moderator
        ref: moderator.yaml
      - id: worker
        ref: worker.yaml
  workflow:
    entry: moderator
    nodes:
      - id: moderator
        delegates_to: [worker]
      - id: worker
"""


def _selection(*_args, **_kwargs) -> EngineSelection:
    return EngineSelection(engine=SimulatedEngine(llm_next_step=lambda _cid: "STOP", stop_text="408"), mode="injected")


def _compose(tmp_path):
    (tmp_path / "moderator.yaml").write_text(AGENT.format(name="moderator", pattern=MODERATOR), encoding="utf-8")
    (tmp_path / "worker.yaml").write_text(AGENT.format(name="worker", pattern=""), encoding="utf-8")
    (tmp_path / "mas.yaml").write_text(MAS, encoding="utf-8")
    with patch("mas.ctl.session.bootstrap.build_engine", side_effect=_selection):
        result = compose_run(
            ComposeRequest(manifest=tmp_path / "mas.yaml", validate=False, infra_refs=["standard:openai"])
        )
        return materialize_mas_compose(result, mas_base_dir=tmp_path)


def test_a_sending_pattern_finds_its_peers(tmp_path):
    """The agent's own spec lists its next hops: the pattern reads them, not the MAS workflow."""
    materialized = _compose(tmp_path)
    spec = materialized.materialized.instances["moderator"].kernel.config.agent_spec
    assert spec["communication"]["peers"] == [{"id": "worker"}]
    assert materialized.materialized.instances["worker"].kernel.config.agent_spec["communication"]["peers"] == []


async def test_a_send_to_a_local_peer_runs_inside_the_event_loop(tmp_path):
    """The bus hands the peer its turn through a sync bridge, which refuses to run on a loop that is running."""
    materialized = _compose(tmp_path)
    send = make_workflow_send(materialized.materialized, display=_QuietDisplay(), verbose=0, from_agent="moderator")
    assert await send("worker", "What is 17 times 24?") == "408"
