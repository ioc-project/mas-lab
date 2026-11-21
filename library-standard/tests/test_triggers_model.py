#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E402
from __future__ import annotations

import pytest
from mas.library.standard.lib.triggers import compile_trigger, compile_when, parse_break_line


def test_structured_and_gdb_compile_equal() -> None:
    structured = compile_when({"on": "tool_result", "tool": "pay_fare", "occurrence": 2})
    gdb = parse_break_line("break tool_result pay_fare if 2")
    assert structured.on == gdb.on
    assert structured.tool == gdb.tool
    assert structured.occurrence == gdb.occurrence == 2


def test_if_first_every_agent() -> None:
    first = parse_break_line("break tool_result pay_fare if first")
    assert first.occurrence == "first"
    every = parse_break_line("break tool_call search if every")
    assert every.occurrence == "every"
    agent = parse_break_line("break tool_result pay_fare agent payer if 2")
    assert agent.agent == "payer"
    assert agent.occurrence == 2


def test_if_first_only_on_tool_result() -> None:
    with pytest.raises(ValueError, match="if first"):
        parse_break_line("break tool_call search if first")


def test_invalid_break_line() -> None:
    with pytest.raises(ValueError, match="break line"):
        parse_break_line("break whenever")


def test_compile_trigger_id_and_do() -> None:
    trigger = compile_trigger(
        {
            "id": "revoke-after-2",
            "when": {"on": "tool_result", "tool": "pay_fare", "occurrence": 2},
            "do": [{"type": "http_call", "config": {"fixture": "issuer", "path": "/admin/revoke"}}],
        }
    )
    assert trigger.id == "revoke-after-2"
    assert trigger.do[0].type == "http_call"
    assert trigger.max_fires == 1
