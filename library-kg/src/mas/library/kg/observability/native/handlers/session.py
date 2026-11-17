#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Handler for the ``mas_call_start`` / ``mas_call_end`` event kinds (MASCall).

The MAS call of a session is the one no call started; its node id comes from the run, so
every AgentCall can name it without having seen it. A nested MAS (started by a tool call)
is a MASCall of the same session with an id of its own. The Session and its MASCall share
the boundary States the ``mas_call_end`` record names (the ontology requires a Session's
boundary States to be shared with another element); each has its own Transition.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List

from mas.library.kg.core.native_graph_builder import (
    mas_call_node_id,
    session_placeholder_id,
)
from mas.library.kg.core.state_transition import ROLE_SPAN
from mas.library.kg.observability.native.handlers.agent import mas_name

if TYPE_CHECKING:
    from mas.library.kg.core.native_graph_builder import NativeGraphBuilder


def handle_mas_call(event: Dict[str, Any], builder: "NativeGraphBuilder") -> List[Dict[str, Any]]:
    run_id = str(event.get("run_id") or "")
    parent = str(event.get("parent_call_id") or "")
    root = not parent
    mc_id = mas_call_node_id(run_id, str(event.get("call_id") or ""), parent)
    node = builder.call_partial(event, "MASCall", node_id=mc_id)
    if node is None:
        return []
    kind = event.get("kind", "")
    if kind.endswith("_start"):
        node["masName"] = event.get("mas_name") or ""
        node["masType"] = event.get("mas_type", "")
    builder.add_node(node)

    session_id = session_placeholder_id(run_id)
    session: Dict[str, Any] = {
        "node_type": "Session",
        "id": session_id,
        "sessionId": session_id,
        "runId": run_id,
    }
    stamp = event.get("timestamp")
    if root:
        # The session spans its own MAS call, not a nested one.
        if kind.endswith("_start"):
            session["startTime"] = stamp
        else:
            session["endTime"] = stamp
            session["success"] = node.get("success")
            session["inputQuery"] = event.get("state_in_content") or ""
            session["finalResponse"] = event.get("state_out_content") or ""
    builder.add_node(session)

    builder.add_edge("hasMASCall", session_id, mc_id)
    mas = mas_name(event, builder)
    if mas:
        mas_id = builder.catalog("MAS", mas)
        builder.executes(node, "executesMAS", mas_id)
        if root:
            builder.executes(session, "executesSession", mas_id)

    if kind.endswith("_end"):
        for element_id in (session_id, mc_id) if root else (mc_id,):
            builder.emit_trajectory(
                event,
                run_id=run_id,
                element_id=element_id,
                role=ROLE_SPAN,
                call_id=node["callId"] if element_id == mc_id else "",
            )
    return [node, session]
