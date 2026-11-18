#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The open_room system tool and the room_open engine tool that runs it."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from mas.library.standard.plugins.engine_tools.room_open import DEFAULT_TIMEOUT_S, RoomOpen
from mas.library.standard.plugins.system_tools.open_room import OpenRoomTool
from mas.runtime.boundary.agentcomm.protocol import AgentCommError
from mas.runtime.engine.manifest_tool_provider import attach_manifest_tools
from mas.runtime.engine.tools import open_room_params

_ENTRY = {
    "kind": "system",
    "name": "open_room",
    "params": {
        "channel": "room",
        "members": ["finance", "procurement"],
        "round_cap": 5,
        "post_timeout_s": 900,
        "turn": "Round {round}. {task}\n{board}",
        "agree_on": "plan",
        "accept": {"stance": "accept"},
        "description": "The two desks.",
        "timeout": 60,
    },
}


class _Comm:
    def __init__(self, result: str = "DECISION: p1", error: Exception | None = None) -> None:
        self.result, self.error, self.calls = result, error, []

    def open_room(self, channel, task, *, members, rules, timeout, headers=None):
        self.calls.append((channel, task, members, rules, timeout))
        if self.error:
            raise self.error
        return self.result


def test_params_are_read_from_an_enabled_entry_only() -> None:
    assert open_room_params({"tools": [_ENTRY]})["channel"] == "room"
    assert open_room_params({"tools": [{**_ENTRY, "enabled": False}]}) is None
    assert open_room_params({"tools": [{"kind": "system", "name": "inform_user"}]}) is None
    assert open_room_params({}) is None


def test_the_model_sees_one_tool_with_the_members_named() -> None:
    tool = OpenRoomTool()
    assert tool.on_collect_tools(ctx=SimpleNamespace()) == []
    assert tool.on_collect_tools(ctx=SimpleNamespace(current_spec={"tools": [{**_ENTRY, "params": {"channel": "room"}}]})) == []
    (spec,) = tool.on_collect_tools(ctx=SimpleNamespace(current_spec={"tools": [_ENTRY]}))
    assert spec["name"] == "open_room"
    assert "finance, procurement" in spec["description"] and "The two desks." in spec["description"]
    assert spec["parameters"]["required"] == ["task"] and "channel" not in spec["parameters"]["properties"]


def test_unclaimed_call_fails_closed() -> None:
    assert "unavailable" in OpenRoomTool().on_execute_tool("open_room", {"task": "x"})


def test_manifest_declaration_advertises_open_room(tmp_path: Path) -> None:
    ctx, engine = SimpleNamespace(), SimpleNamespace()
    attach_manifest_tools(engine, {"spec": {"tools": [_ENTRY]}}, tmp_path, ctx=ctx)
    assert [t["function"]["name"] for t in engine.tool_provider.list_openai_tools(ctx=ctx)] == ["open_room"]


def test_room_open_passes_the_rules_and_returns_the_result() -> None:
    comm = _Comm("DECISION: p1 in round 2")
    rules = {**_ENTRY["params"]}
    tool = RoomOpen(comm=comm, channel="room", members=["finance", "procurement"], rules=rules, timeout=30)
    assert tool.claims("open_room") and not tool.claims("delegate_to_finance")
    assert tool.call("open_room", {"task": "pick"}) == "DECISION: p1 in round 2"
    (channel, task, members, sent, timeout) = comm.calls[0]
    assert (channel, task, members, timeout) == ("room", "pick", ["finance", "procurement"], 30.0)
    # only the room's rules travel; the tool's own settings do not
    assert sent["round_cap"] == 5 and sent["agree_on"] == "plan" and "channel" not in sent and "description" not in sent


def test_room_open_waits_a_long_time_unless_told_otherwise() -> None:
    comm = _Comm()
    RoomOpen(comm=comm, channel="room", members=["f"], rules={}).call("open_room", {"task": "x"})
    assert comm.calls[0][4] == DEFAULT_TIMEOUT_S


def test_room_open_turns_failures_into_text_for_the_model() -> None:
    err = _Comm(error=AgentCommError("room 'room': no result within 5s"))
    tool = RoomOpen(comm=err, channel="room", members=["f"], rules={})
    assert tool.call("open_room", {"task": "x"}) == "[open_room] room 'room' failed: room 'room': no result within 5s"
    assert "empty" in tool.call("open_room", {"task": "  "})
    assert "no shared rooms" in RoomOpen(comm=object(), channel="room", members=["f"], rules={}).call("open_room", {"task": "x"})
    assert "unsupported" in tool.call("other", {"task": "x"})


def test_room_open_async_call_does_not_block_the_loop() -> None:
    tool = RoomOpen(comm=_Comm("ok"), channel="room", members=["f"], rules={})
    assert asyncio.run(tool.acall("open_room", {"task": "x"})) == "ok"
