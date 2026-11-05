#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""chat propagates non-zero exit on failed scripted turns."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from click.testing import CliRunner
from mas.ctl.cli.commands.chat import chat_cmd


def test_chat_scripted_turn_failure_exits_nonzero(tmp_path) -> None:
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

    mock_trace = MagicMock(client_responses=[], boundary_errors=[MagicMock(code="E_TEST", message="turn failed")])
    mock_turn = MagicMock(
        text="",
        awaiting_hitl=False,
        trace=mock_trace,
        responses=[],
    )

    with patch("mas.ctl.cli.commands.chat.instantiate_runtime") as inst:
        mock_instance = MagicMock()
        inst.return_value = (mock_instance, None)
        with patch("mas.ctl.cli.commands.chat.SessionController") as ctrl_cls:
            ctrl_cls.return_value.run_turn.return_value = mock_turn
            with patch("mas.ctl.cli.commands.chat.resolve_hitl_from_manifest", return_value=(None, None)):
                with patch("mas.ctl.cli.commands.chat.setup_observability", return_value=None):
                    runner = CliRunner()
                    result = runner.invoke(
                        chat_cmd,
                        [str(agent), "--prompt", "hello", "--no-validate", "-I", "--no-control"],
                    )

    assert result.exit_code == 1, result.output
