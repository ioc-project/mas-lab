#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Span fixtures from the transport inventory recorder load as normalised JSON."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"

EXPECTED = {
    "a2a/1.1.5": (
        "agent_card",
        "blocking_message",
        "failure",
        "streaming_message",
        "thread_pool_bridge",
    ),
    "mcp/2.2.0": (
        "initialize",
        "tools_call_failure",
        "tools_call_success",
        "tools_list",
    ),
    "slim/2.2.0": (
        "channel_session",
        "connect",
        "send_receive",
    ),
}


@pytest.mark.parametrize("folder, scenarios", sorted(EXPECTED.items()))
def test_inventory_fixtures_cover_each_recorded_scenario(folder: str, scenarios: tuple[str, ...]) -> None:
    root = FIXTURES / folder
    found = tuple(sorted(path.stem for path in root.glob("*.json")))
    assert found == tuple(sorted(scenarios))
    for name in scenarios:
        payload = json.loads((root / f"{name}.json").read_text(encoding="utf-8"))
        assert payload["scenario"] == name
        assert payload["span_count"] == len(payload["spans"])
        assert "token_context_error" in payload
        for span in payload["spans"]:
            assert "name" in span
            assert "scope_name" in span
            assert "attributes" in span
            assert "parent_span_id" in span
            for key in ("trace_id", "span_id", "parent_span_id"):
                value = span[key]
                if value is not None:
                    assert str(value).startswith("$")


def test_a2a_thread_bridge_recorded_no_token_error() -> None:
    payload = json.loads((FIXTURES / "a2a" / "1.1.5" / "thread_pool_bridge.json").read_text(encoding="utf-8"))
    assert payload["token_context_error"] is False
    assert payload["span_count"] > 0


def test_a2a_agent_card_and_slim_recorded_zero_sdk_spans() -> None:
    card = json.loads((FIXTURES / "a2a" / "1.1.5" / "agent_card.json").read_text(encoding="utf-8"))
    slim = json.loads((FIXTURES / "slim" / "2.2.0" / "send_receive.json").read_text(encoding="utf-8"))
    assert card["span_count"] == 0
    assert slim["span_count"] == 0
    assert slim["reply"] == "ack:hello"


def test_mcp_tools_call_spans_name_the_method() -> None:
    payload = json.loads((FIXTURES / "mcp" / "2.2.0" / "tools_call_success.json").read_text(encoding="utf-8"))
    names = {span["name"] for span in payload["spans"]}
    assert "tools/call echo" in names
    assert "MCP send tools/call echo" in names
    server = next(span for span in payload["spans"] if span["name"] == "tools/call echo")
    assert server["attributes"]["mcp.method.name"] == "tools/call"
    assert server["attributes"]["gen_ai.tool.name"] == "echo"
