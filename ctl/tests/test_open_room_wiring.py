#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from types import SimpleNamespace

import pytest
from mas.ctl.executor.open_room import wire_open_room


def _engine():
    return SimpleNamespace(engine_tool_contracts=())


_ROOM_ENTRY = {
    "kind": "system",
    "name": "open_room",
    "params": {"channel": "room", "members": ["a", "b"], "timeout": 9, "round_cap": 3, "description": "x"},
}


def test_open_room_gets_its_contract_with_the_rooms_rules() -> None:
    engine, comm = _engine(), object()
    tool = wire_open_room(engine, {"spec": {"tools": [_ROOM_ENTRY]}}, comm=comm, agent_id="starter")
    assert engine.engine_tool_contracts == (tool,)
    assert (tool.comm, tool.channel, tool.members, tool.timeout, tool.agent_id) == (comm, "room", ["a", "b"], 9.0, "starter")
    assert tool.rules == {"round_cap": 3} and tool.claims("open_room")
    assert wire_open_room(engine, {"spec": {"tools": [_ROOM_ENTRY]}}, comm=comm, agent_id="starter") is tool
    assert len(engine.engine_tool_contracts) == 1


def test_no_declaration_attaches_nothing() -> None:
    engine = _engine()
    assert wire_open_room(engine, {"spec": {"tools": []}}, comm=object(), agent_id="m") is None
    assert wire_open_room(None, {"spec": {"tools": [_ROOM_ENTRY]}}, comm=object(), agent_id="m") is None
    assert engine.engine_tool_contracts == ()


@pytest.mark.parametrize(
    "params, match",
    [({"members": ["a"]}, "channel"), ({"channel": "room"}, "members"), ({"channel": "r", "members": ["a"], "timeout": 0}, "timeout")],
)
def test_bad_room_declaration_is_a_load_error(params, match) -> None:
    with pytest.raises(ValueError, match=match):
        wire_open_room(_engine(), {"spec": {"tools": [{**_ROOM_ENTRY, "params": params}]}}, comm=object(), agent_id="m")
