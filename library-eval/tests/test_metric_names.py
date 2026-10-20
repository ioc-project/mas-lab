#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""CLI and YAML metric ids are snake_case only."""

from __future__ import annotations

import pytest
from click.testing import CliRunner
from mas.library.eval.cli import eval_cmd
from mas.library.eval.mce.catalog import lookup, resolve_metric


def test_lookup_keeps_class_name_for_internal_api() -> None:
    assert lookup("answer_relevancy").camel == "AnswerRelevancy"
    assert lookup("AnswerRelevancy").snake == "answer_relevancy"


def test_resolve_metric_requires_snake_case() -> None:
    assert resolve_metric("goal_success_rate").camel == "GoalSuccessRate"
    with pytest.raises(ValueError, match="Did you mean 'goal_success_rate'"):
        resolve_metric("GoalSuccessRate")
    with pytest.raises(ValueError, match="Unknown metric"):
        resolve_metric("goal-success-rate")
    with pytest.raises(ValueError, match="Unknown metric"):
        resolve_metric("GOAL_SUCCESS_RATE")


def test_resolve_unknown_lists_valid_and_closest() -> None:
    with pytest.raises(ValueError) as exc:
        resolve_metric("groundedess")
    msg = str(exc.value)
    assert "Did you mean 'groundedness'" in msg
    assert "goal_success_rate" in msg


def test_session_only_rejects_core_metric() -> None:
    with pytest.raises(ValueError):
        resolve_metric("tool_error", session_only=True)
    assert resolve_metric("tool_error").camel == "ToolError"


def test_eval_unknown_metric_fails_with_suggestion(tmp_path) -> None:
    trace = tmp_path / "events.jsonl"
    trace.write_text("")
    result = CliRunner().invoke(eval_cmd, [str(trace), "--metric", "goal_sucess_rate"])
    assert result.exit_code != 0
    assert "Did you mean 'goal_success_rate'" in result.output


def test_list_metrics_needs_no_source() -> None:
    result = CliRunner().invoke(eval_cmd, ["--list-metrics"])
    assert result.exit_code == 0, result.output
    assert "goal_success_rate" in result.output
    assert "groundedness" in result.output


def test_help_has_list_metrics_and_no_hardcoded_gemini() -> None:
    result = CliRunner().invoke(eval_cmd, ["--help"])
    assert "--list-metrics" in result.output
    assert "vertex_ai/gemini" not in result.output


def test_eval_rejects_camel_case_metric(tmp_path) -> None:
    trace = tmp_path / "events.jsonl"
    trace.write_text("")
    result = CliRunner().invoke(eval_cmd, [str(trace), "--metric", "GoalSuccessRate"])
    assert result.exit_code != 0
    assert "Did you mean 'goal_success_rate'" in result.output
