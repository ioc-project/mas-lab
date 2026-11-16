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


def test_chat_wires_the_subagent_spawner_for_the_session(tmp_path) -> None:
    """A granted create_subagent needs a spawner behind it in `mas-ctl chat`, not only in MAS sessions."""
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
    mock_turn = MagicMock(
        text="ok", awaiting_hitl=False, trace=MagicMock(client_responses=[], boundary_errors=[]), responses=[]
    )
    with patch("mas.ctl.cli.commands.chat.instantiate_runtime") as inst:
        mock_instance = MagicMock()
        inst.return_value = (mock_instance, None)
        with patch("mas.ctl.cli.commands.chat.SessionController") as ctrl_cls:
            ctrl_cls.return_value.run_turn.return_value = mock_turn
            with patch("mas.ctl.cli.commands.chat.SessionManager") as manager_cls:
                manager_cls.return_value.create.return_value.session_id = "sess-1"
                with patch("mas.ctl.cli.commands.chat.resolve_hitl_from_manifest", return_value=(None, None)):
                    with patch("mas.ctl.cli.commands.chat.setup_observability", return_value=None):
                        with patch("mas.ctl.executor.subagent_spawner.wire_subagent_spawning") as wire:
                            result = CliRunner().invoke(
                                chat_cmd,
                                [str(agent), "--prompt", "hello", "--no-validate", "-I", "--no-control"]
                                + ["--model", "other-model"],
                            )

    assert result.exception is None, result.output
    wire.assert_called_once()
    kwargs = wire.call_args.kwargs
    assert kwargs["parent_agent_id"] == "solo"
    assert kwargs["entry_id"] == "solo"
    assert kwargs["materialized"].instances == {"solo": mock_instance}
    assert kwargs["session_id"] == "sess-1"
    assert kwargs["model_override"] == "other-model"


def _chat_with_agent(tmp_path, agent_yaml: str, *, engine, extra=(), wire_error=None):
    from types import SimpleNamespace

    (tmp_path / "worker.yaml").write_text(
        "apiVersion: mas/v1\nkind: Agent\nmetadata: {name: worker}\nspec:\n  description: Worker agent\n",
        encoding="utf-8",
    )
    agent = tmp_path / "agent.yaml"
    agent.write_text(agent_yaml, encoding="utf-8")
    mock_turn = MagicMock(
        text="ok", awaiting_hitl=False, trace=MagicMock(client_responses=[], boundary_errors=[]), responses=[]
    )
    instance = MagicMock()
    instance.driver = SimpleNamespace(engine=engine)
    with patch("mas.ctl.cli.commands.chat.instantiate_runtime", return_value=(instance, None)):
        with patch("mas.ctl.cli.commands.chat.SessionController") as ctrl_cls:
            ctrl_cls.return_value.run_turn.return_value = mock_turn
            with patch("mas.ctl.cli.commands.chat.SessionManager") as manager_cls:
                manager_cls.return_value.create.return_value.session_id = "sess-1"
                with patch("mas.ctl.cli.commands.chat.resolve_hitl_from_manifest", return_value=(None, None)):
                    with patch("mas.ctl.cli.commands.chat.setup_observability", return_value=None):
                        return CliRunner().invoke(
                            chat_cmd,
                            [str(agent), "--prompt", "hello", "--no-validate", "-I", "--no-control", *extra],
                        )


_GRANTED_AGENT = """apiVersion: mas/v1
kind: Agent
metadata:
  name: solo
spec:
  description: test
  models:
    - model: gpt-4o-mini
  system_plugins:
    - subagents:
        templates:
          - id: worker
            ref: worker.yaml
"""


def test_chat_attaches_a_real_spawner_that_carries_the_model_override(tmp_path) -> None:
    from types import SimpleNamespace

    engine = SimpleNamespace(engine_tool_contracts=())
    result = _chat_with_agent(tmp_path, _GRANTED_AGENT, engine=engine, extra=["--model", "other-model"])

    assert result.exception is None, result.output
    (spawner,) = engine.engine_tool_contracts
    assert list(spawner.templates) == ["worker"]
    assert spawner.context.model_override == "other-model"


def test_chat_reports_a_spawner_grant_without_templates_instead_of_a_traceback(tmp_path) -> None:
    from types import SimpleNamespace

    no_templates = _GRANTED_AGENT.split("    - subagents:")[0] + "    - subagents: {}\n"
    result = _chat_with_agent(tmp_path, no_templates, engine=SimpleNamespace(engine_tool_contracts=()))

    assert result.exit_code == 1, result.output
    assert "error:" in result.output
    assert "Traceback" not in result.output
