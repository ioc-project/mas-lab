#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E402
from __future__ import annotations

from io import StringIO
from types import SimpleNamespace

from mas.library.standard.plugins.governance.debug_script import DebugScriptPlugin, parse_debug_script


def test_if_n_and_agent_parse() -> None:
    breaks = parse_debug_script("break tool_result pay_fare agent payer if 2\n")
    assert breaks[0].tool_name == "pay_fare"
    assert breaks[0].agent == "payer"
    assert breaks[0].occurrence == 2


def test_occurrence_two_fires_on_second_only() -> None:
    plugin = DebugScriptPlugin(
        script="break tool_result pay_fare if 2\ncommands\ncontinue\nend\n",
        out=StringIO(),
    )
    transition = SimpleNamespace(
        hook="ingress",
        op="",
        response_kind="TOOL_RESULT",
        session_id="s1",
        agent_id="a",
        attributes={"tool_name": "pay_fare"},
        q_state={},
    )
    plugin.on_transition(transition)
    assert not any("Breakpoint" in line for line in plugin.log)
    plugin.on_transition(transition)
    assert any("Breakpoint 0:" in line for line in plugin.log)


def test_gdb_llm_turn_and_env_with_port() -> None:
    from mas.library.standard.lib.environment_port import reset_environment_port, set_environment_port

    actions: list[str] = []
    emits: list[str] = []
    port = SimpleNamespace(
        run_named_action=lambda name: actions.append(name),
        emit_named=lambda name, payload=None, source=None: emits.append(name) or {},
    )
    token = set_environment_port(port)
    try:
        llm = DebugScriptPlugin(script="break llm_call\ncommands\ncontinue\nend\n", out=StringIO())
        llm.on_transition(
            SimpleNamespace(
                hook="egress",
                op="LLM_CALL",
                response_kind="",
                session_id="s1",
                agent_id="a",
                attributes={},
                q_state={},
            )
        )
        assert any("Breakpoint 0:" in line for line in llm.log)
        env = DebugScriptPlugin(
            script="break tool_result pay_fare\ncommands\nenv revoke\nemit ping\nprint session\ncontinue\nend\n",
            out=StringIO(),
        )
        env.on_transition(
            SimpleNamespace(
                hook="ingress",
                op="",
                response_kind="TOOL_RESULT",
                session_id="s1",
                agent_id="a",
                attributes={"tool_name": "pay_fare"},
                q_state={},
            )
        )
        assert actions == ["revoke"]
        assert emits == ["user.ping"]
    finally:
        reset_environment_port(token)


def test_env_and_emit_without_port() -> None:
    script = "break tool_result pay_fare\ncommands\nenv revoke\nemit ping\ncontinue\nend\n"
    plugin = DebugScriptPlugin(script=script, out=StringIO())
    plugin.on_transition(
        SimpleNamespace(
            hook="ingress",
            op="",
            response_kind="TOOL_RESULT",
            session_id="s1",
            agent_id="a",
            attributes={"tool_name": "pay_fare"},
            q_state={},
        )
    )
    assert any("env skipped" in line for line in plugin.log)
    assert any("emit skipped" in line for line in plugin.log)
