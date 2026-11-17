#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""State and Transition nodes, mapped from the state fields one event carries.

The runtime says what flows through the system: every element record
(``execution_start`` / ``execution_end``, ``llm_call_*``, ``tool_call_*``,
``memory_call_*``, ``processing_call_*`` and ``mas_call_end``) names the States it reads
and writes in ``state_in`` / ``state_out`` and states their content in
``state_in_content`` / ``state_out_content``. The same id on two records is the same
State, and the emitter guarantees equal content for a same id.

This module turns one such record into nodes and edges, with no knowledge of any other
record:

* a State node per id, its node id derived from the run and the producer's state id;
* one Transition per element, its id derived from the element's node id;
* ``hasInitialState`` / ``hasFinalState`` / ``hasState`` (element -> State),
  ``inputTo`` (State -> Transition), ``leadsTo`` (Transition -> State),
  ``correspondsToTransition`` (element -> Transition) and ``representsExecution``
  (Transition -> element).

A record without state fields maps to nothing: no State, no Transition, no placeholder
text. Two records that name one State give the same node; the builder merges them by id
(see ``NativeGraphBuilder.add_node``), so the result does not depend on arrival order.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

# How a record's fields read, per record kind.
ROLE_SPAN = "span"  # state_in is the input State(s), state_out the output State
ROLE_VISIT_START = "visit_start"  # state_out is the visit's first State (the agent input)
ROLE_VISIT_END = "visit_end"  # state_in == state_out is the visit's final State


def content_hash(content: str) -> str:
    """SHA-256 hex of a State's content (64 chars; equal content gives an equal hash)."""
    return hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()


def canonical_json(value: Any) -> str:
    """The text of a structured value: sorted keys, no spaces (what the emitter writes)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)


def state_node_id(run_id: str, state_id: str) -> str:
    """Node id of the State the producer calls ``state_id`` in this run."""
    return f"state:{run_id}:{state_id}"


def transition_node_id(element_id: str) -> str:
    """Node id of the Transition of the element whose node id is ``element_id``."""
    return f"trans:{element_id}"


def _text(value: Any) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else canonical_json(value)


def _ids(value: Any) -> Tuple[List[str], bool]:
    """State ids of a field and whether the field is a list (a join)."""
    if value is None or value == "":
        return [], False
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v not in (None, "")], True
    return [str(value)], False


def _contents(ids: List[str], joined: bool, raw: Any) -> List[str]:
    """Content of each id, in order. A join states its contents as a JSON array."""
    if not ids:
        return []
    if joined:
        items: Any = raw
        if isinstance(raw, str):
            try:
                items = json.loads(raw)
            except ValueError:
                items = None
        if isinstance(items, list) and len(items) == len(ids):
            return [_text(item) for item in items]
        if items not in (None, ""):
            logger.debug("join content does not match its %d State ids; left empty", len(ids))
        return [""] * len(ids)
    return [_text(raw)]


def read_states(event: Dict[str, Any], role: str) -> Tuple[List[Tuple[str, str]], ...]:
    """``(initial, final)`` States of a record, each a list of ``(state id, content)``."""
    out_ids, _ = _ids(event.get("state_out"))
    in_ids, in_joined = _ids(event.get("state_in"))
    if role == ROLE_VISIT_START:
        content = _contents(out_ids[:1], False, event.get("state_out_content"))
        return list(zip(out_ids[:1], content)), []
    if role == ROLE_VISIT_END:
        final = out_ids[:1] or in_ids[:1]
        if not final:
            return [], []
        raw = event.get("state_out_content")
        if raw in (None, "") and in_ids[:1] == final:
            raw = event.get("state_in_content")
        return [], list(zip(final, _contents(final, False, raw)))
    initial = list(zip(in_ids, _contents(in_ids, in_joined, event.get("state_in_content"))))
    final = list(zip(out_ids[:1], _contents(out_ids[:1], False, event.get("state_out_content"))))
    return initial, final


def map_trajectory(
    event: Dict[str, Any],
    *,
    run_id: str,
    element_id: str,
    role: str = ROLE_SPAN,
    call_id: str = "",
    timestamp: Any = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Nodes and edges one record contributes to the trajectory of ``element_id``.

    Returns ``([], [])`` when the record names no State.
    """
    initial, final = read_states(event, role)
    if not initial and not final:
        return [], []
    placeholder_session = f"session-{run_id}"
    tid = transition_node_id(element_id)
    nodes: List[Dict[str, Any]] = [
        {
            "node_type": "Transition",
            "id": tid,
            "transitionId": tid,
            "sessionId": placeholder_session,
            "name": "",
            "layer": "normalized",
            "realizesCallId": call_id,
            "transitionTimestamp": timestamp,
        }
    ]
    edges: List[Dict[str, Any]] = [
        {"edge_type": "correspondsToTransition", "from_id": element_id, "to_id": tid},
        {"edge_type": "representsExecution", "from_id": tid, "to_id": element_id},
    ]
    for sid, content in initial + final:
        nid = state_node_id(run_id, sid)
        nodes.append(
            {
                "node_type": "State",
                "id": nid,
                "stateNodeId": nid,
                "sessionId": placeholder_session,
                "name": "",
                "layer": "normalized",
                "content": content,
            }
        )
    for sid, _ in initial:
        nid = state_node_id(run_id, sid)
        edges.append({"edge_type": "hasInitialState", "from_id": element_id, "to_id": nid})
        edges.append({"edge_type": "hasState", "from_id": element_id, "to_id": nid})
        edges.append({"edge_type": "inputTo", "from_id": nid, "to_id": tid})
    for sid, _ in final:
        nid = state_node_id(run_id, sid)
        edges.append({"edge_type": "hasFinalState", "from_id": element_id, "to_id": nid})
        edges.append({"edge_type": "hasState", "from_id": element_id, "to_id": nid})
        edges.append({"edge_type": "leadsTo", "from_id": tid, "to_id": nid})
    return nodes, edges
