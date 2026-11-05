#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for run-mas HITL wiring (--no-auto-hitl → OperatorConsole)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from mas.ctl.executor.run_mas import execute_run_mas
from mas.ctl.session.engine_factory import EngineSelection
from mas.runtime.engine.simulated import SimulatedEngine


def test_run_mas_no_auto_hitl_uses_operator_console(tmp_path):
    """With auto_hitl=False, run-mas uses OperatorConsole for interactive HITL."""
    agent = tmp_path / "agent.yaml"
    agent.write_text(
        """apiVersion: mas/v1
kind: Agent
metadata:
  name: solo
spec:
  description: test
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
  name: hitl-fixture
spec:
  agency:
    agents:
      - id: solo
        ref: agent.yaml
""",
        encoding="utf-8",
    )

    mock_console = MagicMock()
    sel = EngineSelection(
        engine=SimulatedEngine(llm_next_step=lambda _cid: "STOP", stop_text="hello"),
        mode="injected",
    )
    with patch("mas.ctl.session.operator_console.OperatorConsole", return_value=mock_console):
        with patch("mas.ctl.session.bootstrap.build_engine", return_value=sel):
            rc = execute_run_mas(
                mas_path,
                prompt="hello",
                validate=False,
                infra_refs=["standard:openai"],
                auto_hitl=False,
            )

    assert rc == 0
    mock_console.run.assert_called_once()


def test_run_mas_auto_hitl_batch_skips_operator_console(tmp_path):
    """With auto_hitl=True (batch mode), run-mas bypasses OperatorConsole."""
    agent = tmp_path / "agent.yaml"
    agent.write_text(
        """apiVersion: mas/v1
kind: Agent
metadata:
  name: solo
spec:
  description: test
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
  name: batch-fixture
spec:
  agency:
    agents:
      - id: solo
        ref: agent.yaml
""",
        encoding="utf-8",
    )

    mock_console = MagicMock()
    mock_resp = MagicMock(content="hello", finish_reason="stop")
    mock_trace = MagicMock(client_responses=[mock_resp], boundary_errors=[])
    mock_turn = MagicMock(text="hello", awaiting_hitl=False, trace=mock_trace, responses=[mock_resp])
    sel = EngineSelection(
        engine=SimulatedEngine(llm_next_step=lambda _cid: "STOP", stop_text="hello"),
        mode="injected",
    )
    with patch("mas.ctl.session.operator_console.OperatorConsole", return_value=mock_console):
        with patch("mas.ctl.session.bootstrap.build_engine", return_value=sel):
            with patch("mas.ctl.session.controller.SessionController.run_turn", return_value=mock_turn) as run_turn:
                rc = execute_run_mas(
                    mas_path,
                    prompt="hello",
                    validate=False,
                    infra_refs=["standard:openai"],
                    auto_hitl=True,
                )

    assert rc == 0
    mock_console.run.assert_not_called()
    run_turn.assert_called()
