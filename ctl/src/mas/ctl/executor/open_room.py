#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Give an agent that declares ``open_room`` the contract that runs it."""

from __future__ import annotations

from typing import Any

from mas.ctl.executor.subagent_spawner import _resolve_engine_tool_class
from mas.runtime.engine.leaf import leaf_engine
from mas.runtime.engine.tools import open_room_params


def wire_open_room(engine: Any, manifest: dict[str, Any], *, comm: Any, agent_id: str) -> Any | None:
    """Attach ``room_open`` to the engine when the manifest declares the ``open_room`` system tool.

    Declaring the tool without a ``channel`` and ``members`` is a load error; a link without rooms only
    fails the call, as ``RoomOpen`` says. Every other param of the entry that ``RoomOpen`` knows is a rule
    of the room and travels to the members.
    """
    params = open_room_params(manifest.get("spec"))
    if params is None or engine is None:
        return None
    channel = str(params.get("channel") or "").strip()
    members = [str(m).strip() for m in params.get("members") or [] if str(m).strip()]
    if not channel or not members:
        raise ValueError(f"agent {agent_id!r}: open_room needs params.channel and params.members")
    timeout = params.get("timeout")
    if timeout is not None and not (isinstance(timeout, (int, float)) and timeout > 0):
        raise ValueError(f"agent {agent_id!r}: open_room params.timeout must be a positive number")
    leaf = leaf_engine(engine)
    for existing in getattr(leaf, "engine_tool_contracts", ()) or ():
        if callable(getattr(existing, "is_room_open_tool", None)) and getattr(existing, "agent_id", None) == agent_id:
            return existing
    tool = _resolve_engine_tool_class("room_open")(
        comm=comm,
        channel=channel,
        members=members,
        rules=params,
        timeout=float(timeout) if timeout is not None else None,
        agent_id=agent_id,
    )
    leaf.engine_tool_contracts = (*(getattr(leaf, "engine_tool_contracts", ()) or ()), tool)
    return tool
