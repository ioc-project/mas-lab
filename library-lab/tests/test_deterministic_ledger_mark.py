#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Lab deterministic patterns read this turn's tool results from the checkpointed mark."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from mas.library.lab.plugins.design_patterns import staged_debate as debate_mod
from mas.library.lab.plugins.design_patterns import supervised as supervised_mod
from mas.library.lab.plugins.design_patterns import voting as voting_mod
from mas.library.lab.plugins.design_patterns.staged_debate import DeterministicStagedDebatePlugin
from mas.library.lab.plugins.design_patterns.supervised import DeterministicSupervisedPlugin
from mas.library.lab.plugins.design_patterns.voting import DeterministicVotingPlugin
from mas.runtime.kernel.state import QProduct, RunEvent, RunLedger


def _q_and_run() -> tuple[QProduct, RunLedger]:
    q = QProduct()
    run = RunLedger()
    run.append(RunEvent(correlation_id=1, response_kind="TOOL_RESULT", next_step="LLM_CALL", text="old"))
    return q, run


def test_supervised_and_voting_read_only_results_after_the_restored_mark() -> None:
    for cls, mod in (
        (DeterministicSupervisedPlugin, supervised_mod),
        (DeterministicVotingPlugin, voting_mod),
    ):
        q, run = _q_and_run()
        plugin = cls()
        plugin._reset_state(["w0", "w1", "supervisor"], "task", q=q)
        q.dp_data["ledger_mark"] = run.max_seq()
        config = MagicMock()
        with patch.object(mod, "dispatch_parallel", return_value=["ok"]) as mocked:
            out = plugin.evaluate_next(q, run, config=config)
        assert out == ["ok"]
        mocked.assert_called_once()
        assert plugin._extract_tool_results(run, q) == []


def test_staged_debate_round_two_prompt_uses_this_turns_results_only() -> None:
    q, run = _q_and_run()
    plugin = DeterministicStagedDebatePlugin()
    plugin._reset_state(["a", "b"], "task", q=q)
    q.dp_data["ledger_mark"] = run.max_seq()
    run.append(RunEvent(correlation_id=2, response_kind="TOOL_RESULT", next_step="LLM_CALL", text="peer-a"))
    st = plugin._debate_state()
    st.next_idx = 1
    config = MagicMock()
    with patch.object(debate_mod, "dispatch_parallel", return_value=["ok"]) as mocked:
        out = plugin.evaluate_next(q, run, config=config)
    assert out == ["ok"]
    assert st.round_num == 2
    prompt = mocked.call_args.args[4]
    assert "peer-a" in prompt
    assert "old" not in prompt
