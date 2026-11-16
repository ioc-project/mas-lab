#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Native ``events.jsonl`` -> KG: a plain, stateless mapping of explicit signals.

Each event maps onto nodes and edges by itself, with the ontology's own names:

* an element record (``execution_*``, ``llm_call_*``, ``tool_call_*``,
  ``processing_call_*``, ``memory_call_*``, ``mas_call_*``) gives one call node whose id
  comes from the call's own id (``<class>:<run>:<call_id>``); its start and end records
  carry the same id and are merged by id;
* the State fields of a record give State and Transition nodes and the trajectory edges
  (``core/state_transition.py``);
* containment is what the record says: ``hasAgentCall`` / ``belongsToMASCall`` for every
  AgentCall, and ``hasLLMCall`` / ``hasToolCall`` / ``hasMemoryCall`` from the AgentCall
  named by ``parent_call_id``; a ProcessingCall is contained by the call it names
  (``hasProcessingCall`` from an AgentCall or MASCall, ``containsCall`` from another call);
* the catalog nodes (Agent, LLM, Tool, MAS, Processing) and their ``executes*`` /
  ``usesLLM`` / ``usesTool`` / ``belongsToMAS`` edges come from ``agent_id``, ``model``,
  ``tool_name``, ``processing_name`` and ``mas_id``.

Nothing is inferred: no timestamp enclosure, no sibling order, no content comparison, no
fabricated node. An edge may name a node that arrives later. Node ids are deterministic
from what the record carries and records are merged commutatively by id, so permuting
the events does not change the graph, and the batch fold is the same mapping looped over
the events.

:meth:`NativeGraphBuilder.finalize` only merges by id, drops edges whose endpoint never
arrived, names the Session and applies the SCOPE attribute projection
(:func:`~mas.library.kg.core.scope_models.restrict_graph`).

Entry points:

* :func:`build_graph_batch` / :func:`build_graph_from_normalized`: loop
  ``process_event`` over a list of events, then ``finalize()``.
* :func:`build_graph_realtime`: one event into a long-lived builder; returns the nodes
  and edges that event contributes.
"""

from __future__ import annotations

import copy
import json
import logging
import re
import warnings
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from mas.library.kg.core.event_mappings import skipped_classes
from mas.library.kg.core.graph_builder import (
    _annotate_one_event,
    _edge_canonical_id,
    _execution_id,
    _GapDedup,
    _infer_app_name_from_ids,
    _node_canonical_id,
    _split_composite_run_id,
    normalize_events,
)
from mas.library.kg.core.state_transition import content_hash, map_trajectory

logger = logging.getLogger(__name__)


def event_seconds(value: Any) -> Optional[float]:
    """An event timestamp as epoch seconds, or None when it is none.

    Accepts a number, a numeric string, or an ISO 8601 string (``Z`` or an offset;
    no zone means UTC).
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


SUCCESS_STATUS = frozenset({"ok", "success", "true", "completed"})
# What the runtime and the OTel converter write for a call that did not succeed.
FAILURE_STATUS = frozenset(
    {
        "error",
        "failed",
        "failure",
        "false",
        "denied",
        "blocked",
        "timeout",
        "cancelled",
        "canceled",
        "aborted",
        "terminated",
        "unfinished",
    }
)
_UNKNOWN_STATUS_SEEN: set[str] = set()


def status_is_success(status: Any) -> bool:
    """Whether a call status means success. Native and OTel graphs share this rule.

    A status in neither vocabulary reads as not successful (a call is not assumed to
    have worked) and is reported once, so a new runtime status is noticed and added to
    one of the two sets instead of silently failing every call that carries it.
    """
    text = str(status or "success").strip().lower()
    if text in SUCCESS_STATUS:
        return True
    if text not in FAILURE_STATUS and text not in _UNKNOWN_STATUS_SEEN:
        _UNKNOWN_STATUS_SEEN.add(text)
        logger.warning("unknown call status %r read as not successful", text)
    return False


# -- Ids -----------------------------------------------------------------------------


def call_node_id(local_name: str, run_id: str, call_id: str) -> str:
    """Node id of a call: its class, the run and the call's own id."""
    return f"{local_name.lower()}:{run_id}:{call_id}"


def mas_call_node_id(run_id: str, call_id: str = "", parent_call_id: str = "") -> str:
    """Node id of a MASCall.

    The MASCall of a session is the one no call started (no ``parent_call_id``): its id
    comes from the run alone, so every AgentCall can name it without having seen it. A
    MAS call that a tool call started (a nested MAS) is keyed by its own call id.
    """
    if parent_call_id and call_id:
        return f"mascall:{run_id}:{call_id}"
    return f"mascall:{run_id}"


def session_placeholder_id(run_id: str) -> str:
    """Node id a Session carries until ``finalize`` names it."""
    return f"session-{run_id}"


def call_ref(run_id: str, call_id: str) -> str:
    """An edge target named by a call id alone; ``finalize`` resolves it to the call's node."""
    return f"callref:{run_id}\n{call_id}"


_IRI_UNSAFE = re.compile(r'[\s<>"{}|\\^`]')


def catalog_node_id(ntype: str, name: str) -> str:
    """Node id of a catalog entry (Tool, LLM, Processing, Skill, MAS).

    The id ends up in an IRI, so characters an IRI cannot hold are written ``_``.
    """
    prefix = {"LLM": "model"}.get(ntype, ntype.lower())
    return f"catalog:{prefix}:{_IRI_UNSAFE.sub('_', name)}"


# -- Commutative merge by id ------------------------------------------------------------

_MIN_KEYS = frozenset({"startTime", "transitionTimestamp"})
_MAX_KEYS = frozenset({"endTime"})
_UNION_KEYS = frozenset({"sourceRecordIds"})


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str, ensure_ascii=False)


def merge_attrs(existing: Dict[str, Any], new: Dict[str, Any]) -> None:
    """Merge ``new`` into ``existing`` so the result does not depend on the order.

    A non-empty value beats an empty one. Two different non-empty values keep the
    smaller one (earliest ``startTime``, latest ``endTime``, the lexicographically
    smaller text otherwise); a different State ``content`` also logs a warning.
    """
    for key, value in new.items():
        if _empty(value):
            continue
        current = existing.get(key)
        if _empty(current):
            existing[key] = value
        elif current == value:
            continue
        elif key in _UNION_KEYS:
            existing[key] = sorted(set(current) | set(value))
        elif (
            key in _MIN_KEYS
            and isinstance(current, (int, float))
            and isinstance(value, (int, float))
        ):
            existing[key] = min(current, value)
        elif (
            key in _MAX_KEYS
            and isinstance(current, (int, float))
            and isinstance(value, (int, float))
        ):
            existing[key] = max(current, value)
        else:
            keep = min(current, value, key=_canon)
            existing[key] = keep
            if key == "content" and existing.get("node_type") == "State":
                logger.warning(
                    "State %s has two different contents; keeping the lexicographically "
                    "smaller one",
                    existing.get("id"),
                )


_CONTAINMENT_FROM_AGENT_CALL = {
    "LLMCall": "hasLLMCall",
    "ToolCall": "hasToolCall",
    "MemoryCall": "hasMemoryCall",
    "SkillCall": "hasSkillCall",
    "RAGQuery": "hasMemoryCall",
}
_TRAJECTORY_CLASSES = frozenset(
    {
        "AgentCall",
        "LLMCall",
        "ToolCall",
        "MemoryCall",
        "RAGQuery",
        "SkillCall",
        "ProcessingCall",
        "ThinkingCall",
    }
)
_DEFAULT_NAME_KEYS = (
    "name",
    "agentName",
    "masName",
    "llmName",
    "toolName",
    "processingName",
    "appName",
    "agentId",
)
_CALL_CLASSES = frozenset(
    {
        "Session",
        "MASCall",
        "AgentCall",
        "LLMCall",
        "ToolCall",
        "ProcessingCall",
        "SkillCall",
        "MemoryCall",
        "RAGQuery",
        "ThinkingCall",
    }
)


class NativeGraphBuilder:
    """Accumulator of the nodes and edges the events of a run map to.

    :meth:`process_event` maps one event; :meth:`finalize` returns the graph. The
    accumulation (``nodes``, ``edges``) is a merge by id and holds no ordering state.
    """

    def __init__(
        self,
        ontology: Any = None,
        run_id: str = "",
        *,
        categories: Any = None,
        all_attributes: bool = False,
        strict: bool = False,
        session_id_override: Optional[str] = None,
        split_session_id: bool = True,
        app_name_override: Optional[str] = None,
    ) -> None:
        self.ontology = ontology if ontology is not None else {}
        self.run_id = run_id
        self.strict = strict
        self.session_id_override = session_id_override
        self.split_session_id = split_session_id
        self.app_name_override = app_name_override
        self.all_attributes = bool(all_attributes)
        self._skip_classes = skipped_classes(categories)
        self._gaps = _GapDedup(run_id)

        # node id -> node, edge key -> edge: merged by id as events arrive.
        self.nodes: Dict[str, Dict[str, Any]] = {}
        self.edges: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        self.run_ids: set[str] = set()
        # run id -> the session ids / app names its events carry (all of them).
        self._event_session_ids: Dict[str, set[str]] = {}
        self._event_app_names: Dict[str, set[str]] = {}
        # What the current event contributed.
        self._delta_nodes: List[Dict[str, Any]] = []
        self._delta_edges: List[Dict[str, Any]] = []

    def class_enabled(self, local_name: str) -> bool:
        """Whether the categories in force keep the class ``local_name``."""
        return local_name not in self._skip_classes

    # -- Classification (realtime only; batch pre-classifies via normalize_events) ------

    def _classify_one(self, raw_event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return _annotate_one_event(
            raw_event,
            self.ontology,
            self.run_id,
            self.strict,
            self._gaps.report,
            skip_classes=self._skip_classes,
        )

    # -- Accumulation primitives, used by the handlers ----------------------------------

    def add_node(self, node: Dict[str, Any]) -> None:
        """Merge one node (a partial one is fine) into the node with the same id."""
        nid = str(node.get("id") or "")
        if not nid:
            return
        self._delta_nodes.append(node)
        existing = self.nodes.get(nid)
        if existing is None:
            # Empty values are not stored, so a record that says nothing for a field leaves
            # no trace whichever record arrives first.
            self.nodes[nid] = {k: copy.deepcopy(v) for k, v in node.items() if not _empty(v)}
        else:
            merge_attrs(existing, node)

    def add_edge(self, edge_type: str, from_id: str, to_id: str, **extra: Any) -> None:
        """Add one edge; the edge may name a node that arrives later."""
        if not from_id or not to_id:
            return
        key = (edge_type, from_id, to_id)
        edge = {"edge_type": edge_type, "from_id": from_id, "to_id": to_id, **extra}
        existing = self.edges.get(key)
        if existing is None:
            self.edges[key] = {k: v for k, v in edge.items() if not _empty(v)}
            self._delta_edges.append(edge)
        else:
            merge_attrs(existing, extra)

    def call_partial(
        self, event: Dict[str, Any], local_name: str, node_id: str = ""
    ) -> Optional[Dict[str, Any]]:
        """The node one start or end record says about its call (not yet added)."""
        call_id = str(event.get("call_id") or "")
        if not call_id:
            return None
        run_id = str(event.get("run_id") or "")
        kind = str(event.get("kind") or "")
        node: Dict[str, Any] = {
            "node_type": local_name,
            "id": node_id or call_node_id(local_name, run_id, call_id),
            "callId": call_id,
            "executionId": _execution_id(local_name, call_id),
            "agentId": event.get("agent_id") or "",
            "kindBase": kind.replace("_start", "").replace("_end", ""),
            "masUri": event.get("mas_uri", ""),
            "spanLevel": event.get("span_level", ""),
            "runId": run_id,
            "sessionId": session_placeholder_id(run_id),
        }
        record_id = str(event.get("record_id") or "").strip()
        if record_id:
            node["sourceRecordIds"] = [record_id]
        stamp = event.get("timestamp")
        if kind.endswith("_start"):
            node["startTime"] = stamp
            # The parent is the one the start names (an end may name another record's).
            node["parentCallId"] = event.get("parent_call_id") or ""
        elif kind.endswith("_end"):
            node["endTime"] = stamp
            status = event.get("status", "success")
            node["status"] = status
            node["success"] = status_is_success(status)
        return node

    def emit_call(self, event: Dict[str, Any], node: Dict[str, Any], role: str = "span") -> None:
        """Add a call node and what its record says around it: containment, trajectory."""
        self.add_node(node)
        ntype = str(node["node_type"])
        run_id = str(node.get("runId") or "")
        parent = str(event.get("parent_call_id") or "")
        if ntype == "AgentCall":
            mc = mas_call_node_id(run_id)
            self.add_edge("hasAgentCall", mc, node["id"])
            self.add_edge("belongsToMASCall", node["id"], mc)
        elif ntype == "ProcessingCall" and parent:
            # The parent is whatever call the record names (an agent visit, or the LLM or
            # tool call a check brackets); ``finalize`` types the edge once it knows which.
            self.add_edge("hasProcessingCall", call_ref(run_id, parent), node["id"])
        elif ntype in _CONTAINMENT_FROM_AGENT_CALL and parent:
            # The named parent is an agent visit when an AgentCall node has that id; a
            # call whose parent is another call, or never arrives, gets no edge.
            self.add_edge(
                _CONTAINMENT_FROM_AGENT_CALL[ntype],
                call_node_id("AgentCall", run_id, parent),
                node["id"],
            )
        if ntype in _TRAJECTORY_CLASSES:
            self.emit_trajectory(
                event,
                run_id=run_id,
                element_id=node["id"],
                role=role,
                call_id=node["callId"],
            )

    def emit_trajectory(
        self,
        event: Dict[str, Any],
        *,
        run_id: str,
        element_id: str,
        role: str,
        call_id: str = "",
    ) -> None:
        nodes, edges = map_trajectory(
            event,
            run_id=run_id,
            element_id=element_id,
            role=role,
            call_id=call_id,
            timestamp=event.get("timestamp"),
        )
        for node in nodes:
            self.add_node(node)
        for edge in edges:
            self.add_edge(edge["edge_type"], edge["from_id"], edge["to_id"])

    def catalog(self, ntype: str, name: str, node_id: str = "", **extra: Any) -> str:
        """Add a catalog node (Agent, LLM, Tool, MAS, Processing, Skill); returns its id."""
        nid = node_id or catalog_node_id(ntype, name)
        node: Dict[str, Any] = {
            "node_type": ntype,
            "id": nid,
            "name": name,
            "block": "structural",
            "masUri": _mas_ns() + ntype,
            **extra,
        }
        if ntype == "Agent":
            node["agentId"] = name
        self.add_node(node)
        return nid

    def executes(self, call_node: Dict[str, Any], edge_type: str, catalog_id: str) -> None:
        self.add_edge(edge_type, call_node["id"], catalog_id)

    # -- Per-event dispatch ---------------------------------------------------------------

    def process_event(
        self, event: Dict[str, Any]
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Classify (when needed) and map one event.

        Works the same called once per incoming event or in a loop over a whole trace.
        Returns the nodes and edges this event contributes (empty for a dropped or
        suppressed event); a node may be partial, the other record of its call says
        the rest.
        """
        self._delta_nodes = []
        self._delta_edges = []
        stamp = event_seconds(event.get("timestamp"))
        if stamp is not None and event.get("timestamp") != stamp:
            event = {**event, "timestamp": stamp}  # the rest of the fold reads seconds
        if "mas_class" not in event:
            classified = self._classify_one(dict(event))
            if classified is None:
                return [], []
            event = classified

        from mas.library.kg.observability.native.handlers import HANDLERS

        run_id = event.get("run_id", "")
        local_name = event.get("mas_class", "ExecutionElement")

        self.run_ids.add(run_id)
        event_session = str(event.get("session_id") or "").strip()
        if event_session:
            self._event_session_ids.setdefault(run_id, set()).add(event_session)
        event_app = str(event.get("app_name") or "").strip()
        if event_app:
            self._event_app_names.setdefault(run_id, set()).add(event_app)

        # Skip suppressed events (None-mapped kinds and abstract fallback).
        if local_name in ("ExecutionElement", None):
            return [], []

        handler = HANDLERS.get(local_name)
        if handler is not None:
            handler(event, self)
        return self._delta_nodes, self._delta_edges

    # -- Whole-graph step: merge by id, drop dangling edges, name the Session -------------

    def _session_id_for(self, run_id: str) -> str:
        """The session id of a run: the one its events carry, else what the run id says."""
        derived_app_name, derived_session_id = _split_composite_run_id(run_id)
        sessions = self._event_session_ids.get(run_id) or set()
        event_session = min(sessions) if sessions else None
        if self.split_session_id and event_session:
            # The events name the session, whatever shape the run id has (a plain id, a
            # hierarchical ``a/b/c/d/e`` or ``<app>_<uuid>``). Two runs naming the same
            # session keep their own ids.
            others = [
                r
                for r in self.run_ids
                if r != run_id and event_session in (self._event_session_ids.get(r) or set())
            ]
            if not others:
                return event_session
        if self.split_session_id and derived_session_id != run_id:
            return derived_session_id
        if self.split_session_id and "/" in str(run_id):
            # Hierarchical run IDs are already session-like (e.g. app/exp/scenario/item/r1).
            return str(run_id)
        return session_placeholder_id(run_id)

    def _app_name_for(self, run_id: str) -> str:
        derived_app_name, _ = _split_composite_run_id(run_id)
        inferred = _infer_app_name_from_ids(run_id, self.session_id_override)
        apps = self._event_app_names.get(run_id) or set()
        return (
            (self.app_name_override or "").strip()
            or inferred
            or derived_app_name
            or (min(apps) if apps else "")
        )

    def finalize(self) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Return the final ``(nodes, edges)``. Safe to call more than once."""
        self._gaps.log_rollup()
        from mas.library.kg.core.scope_models import map_graph, restrict_graph

        nodes: Dict[str, Dict[str, Any]] = copy.deepcopy(self.nodes)
        edges: Dict[Tuple[str, str, str], Dict[str, Any]] = copy.deepcopy(self.edges)
        run_ids = sorted(self.run_ids)

        # -- Sessions: one per run, named from the run and the ids the events carry ----
        rename: Dict[str, str] = {}
        session_ids: Dict[str, str] = {}
        for run_id in run_ids:
            placeholder = session_placeholder_id(run_id)
            session_id = self._session_id_for(run_id)
            app_name = self._app_name_for(run_id)
            if not self.split_session_id and not app_name:
                warnings.warn(
                    "split_session_id=False: appName cannot be inferred from run_id/session_id; "
                    "set app_name_override (CLI: --app-name) to retain application mapping.",
                    stacklevel=2,
                )
            base: Dict[str, Any] = {
                "node_type": "Session",
                "id": placeholder,
                "sessionId": placeholder,
                "executionId": f"exec-Session-{run_id}",
                "runId": run_id,
                "appName": app_name,
            }
            existing = nodes.get(placeholder)
            if existing is None:
                nodes[placeholder] = base
            else:
                merge_attrs(existing, base)
            session_ids[run_id] = session_id
        if self.session_id_override and len(run_ids) == 1:
            session_ids[run_ids[0]] = self.session_id_override
        elif self.session_id_override and len(run_ids) > 1:
            warnings.warn(
                f"session_id_override={self.session_id_override!r} ignored: "
                f"trace has {len(run_ids)} run_ids, override only applies to single-run traces.",
                stacklevel=2,
            )
        for run_id in run_ids:
            if session_ids[run_id] != session_placeholder_id(run_id):
                rename[session_placeholder_id(run_id)] = session_ids[run_id]

        # -- Rename the placeholders of the Sessions --------------------------------------
        if rename:
            renamed_nodes: Dict[str, Dict[str, Any]] = {}
            for nid, node in nodes.items():
                if node.get("id") in rename:
                    node["id"] = rename[node["id"]]
                if node.get("sessionId") in rename:
                    node["sessionId"] = rename[node["sessionId"]]
                renamed_nodes[node["id"]] = node
            nodes = renamed_nodes
            renamed_edges: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
            for edge in edges.values():
                edge["from_id"] = rename.get(edge["from_id"], edge["from_id"])
                edge["to_id"] = rename.get(edge["to_id"], edge["to_id"])
                renamed_edges[(edge["edge_type"], edge["from_id"], edge["to_id"])] = edge
            edges = renamed_edges

        # -- Resolve the targets an annotation names by call id ----------------------------
        by_call: Dict[Tuple[str, str], str] = {}
        for nid, node in sorted(nodes.items()):
            if node.get("node_type") in _CALL_CLASSES and node.get("callId"):
                by_call.setdefault((str(node.get("runId") or ""), str(node["callId"])), nid)
        resolved: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        for edge in edges.values():
            for end in ("from_id", "to_id"):
                target = edge[end]
                if isinstance(target, str) and target.startswith("callref:"):
                    run_id, _, call_id = target[len("callref:") :].partition("\n")
                    edge[end] = by_call.get((run_id, call_id), target)
            if str(edge["from_id"]).startswith("callref:") or str(edge["to_id"]).startswith(
                "callref:"
            ):
                continue
            resolved[(edge["edge_type"], edge["from_id"], edge["to_id"])] = edge
        edges = resolved

        # -- A ProcessingCall is contained through ``hasProcessingCall`` by an AgentCall or a
        # MASCall; any other call (an LLM or tool call a check brackets) contains it through
        # the general ``containsCall``.
        for edge in edges.values():
            if edge["edge_type"] == "hasProcessingCall":
                source = nodes.get(edge["from_id"])
                if source is not None and source.get("node_type") not in ("AgentCall", "MASCall"):
                    edge["edge_type"] = "containsCall"
        edges = {(e["edge_type"], e["from_id"], e["to_id"]): e for e in edges.values()}

        # -- Drop the edges whose endpoint never arrived -----------------------------------
        kept = {k: e for k, e in edges.items() if e["from_id"] in nodes and e["to_id"] in nodes}
        if len(kept) != len(edges):
            logger.debug("dropped %d edge(s) whose endpoint never arrived", len(edges) - len(kept))
        edges = kept

        for node in nodes.values():
            _finish_node(node)

        # -- Identity ----------------------------------------------------------------------------
        node_list = sorted(nodes.values(), key=lambda n: str(n["id"]))
        node_by_local_id: Dict[str, Dict[str, Any]] = {}
        for n in node_list:
            n["canonicalId"] = _node_canonical_id(n)
            for key in (
                "callId",
                "stateNodeId",
                "transitionId",
                "sessionId",
                "runId",
                "annotationId",
                "id",
            ):
                val = n.get(key)
                if val:
                    node_by_local_id[str(val)] = n
        edge_list = []
        for e in edges.values():
            etype = str(e.get("edge_type") or "edge")
            e["id"] = f"{etype}:{e['from_id']}->{e['to_id']}"
            e["canonicalId"] = _edge_canonical_id(e, node_by_local_id)
            edge_list.append(e)
        edge_list.sort(key=lambda e: e["id"])

        mapped_nodes, mapped_edges = map_graph(node_list, edge_list)
        return restrict_graph(mapped_nodes, mapped_edges, all_attributes=self.all_attributes)


def _mas_ns() -> str:
    from mas.library.kg.core.scope_models import mas_namespace

    return mas_namespace()


def _duration_ms(node: Dict[str, Any]) -> Optional[float]:
    start, end = node.get("startTime"), node.get("endTime")
    if start in (None, "") or end in (None, ""):
        return None
    try:
        return max(float(end) - float(start), 0.0) * 1000.0
    except (TypeError, ValueError):
        return None


def _finish_node(node: Dict[str, Any]) -> None:
    """Fill what a node derives from its own fields once its records are merged."""
    ntype = str(node.get("node_type") or "")
    if ntype == "State":
        node["contentHash"] = content_hash(str(node.get("content") or ""))
        node.setdefault("content", "")
    if ntype in {"State", "Transition"}:
        node.setdefault("name", "")
    elif ntype == "MASCall":
        session = str(node.get("sessionId") or "").removeprefix("session-")
        node.setdefault("name", f"MASCall {session or node.get('callId') or node['id']}")
    elif ntype == "Session":
        node.setdefault("name", f"Session {node.get('sessionId') or node['id']}")
    else:
        for key in _DEFAULT_NAME_KEYS:
            if node.get(key) not in (None, ""):
                node.setdefault("name", str(node[key]))
                break
        else:
            node.setdefault("name", f"{ntype or 'Element'} {node['id']}".strip())
    if ntype in _CALL_CLASSES:
        duration = _duration_ms(node)
        if duration is not None:
            node.setdefault("duration", duration)
    if ntype == "AgentCall":
        node.setdefault("agentName", node.get("agentId", ""))
    if ntype == "LLMCall":
        node.setdefault("provider", "unknown")
        node.setdefault("modelName", "")
        for key in (
            "promptTokenCount",
            "completionTokenCount",
            "totalTokenCount",
            "cacheReadTokenCount",
        ):
            node.setdefault(key, 0)
        node.setdefault("finishReason", "")
        node.setdefault("responseId", "")
        node.setdefault("temperature", 0.0)
    if ntype in {"Agent", "MAS", "LLM", "Tool", "Processing", "Skill", "Memory", "Worker"}:
        # An agent or MAS that ran is part of the declared topology; catalog entries seen
        # only as calls are not.
        node.setdefault("declared", ntype in {"Agent", "MAS"})
    if ntype == "LLM":
        node.setdefault("provider", "unknown")


# -- Entry points -------------------------------------------------------------------------


def build_graph_from_normalized(
    normalized: List[Dict[str, Any]],
    *,
    session_id_override: Optional[str] = None,
    split_session_id: bool = True,
    app_name_override: Optional[str] = None,
    all_attributes: bool = False,
    categories: Any = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Build (nodes, edges) from an already-``normalize_events``-annotated list.

    The same mapping as the realtime path, looped over the events in the order given
    (the order does not matter). ``categories`` are the ones ``normalize_events`` was given.
    """
    builder = NativeGraphBuilder(
        categories=categories,
        session_id_override=session_id_override,
        split_session_id=split_session_id,
        app_name_override=app_name_override,
        all_attributes=all_attributes,
    )
    for ev in normalized:
        builder.process_event(ev)
    return builder.finalize()


def build_graph_batch(
    events: List[Dict[str, Any]],
    ontology: Any,
    run_id: str,
    *,
    categories: Any = None,
    all_attributes: bool = False,
    strict: bool = False,
    session_id_override: Optional[str] = None,
    split_session_id: bool = True,
    app_name_override: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Batch entry point: classify the full event list, then map every event.

    Equivalent to ``extract_graph(normalize_events(events, ontology, run_id,
    ...), ...)``, expressed as the same "process_event per event, then finalize()" shape
    as :func:`build_graph_realtime`.
    """
    normalized = normalize_events(
        events,
        ontology,
        run_id,
        categories=categories,
        strict=strict,
    )
    return build_graph_from_normalized(
        normalized,
        session_id_override=session_id_override,
        split_session_id=split_session_id,
        app_name_override=app_name_override,
        all_attributes=all_attributes,
        categories=categories,
    )


def build_graph_realtime(
    builder: NativeGraphBuilder,
    event: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Realtime entry point: feed one event to a long-lived builder.

    Returns the nodes and edges that event contributes. ``builder.finalize()`` returns
    the complete graph at any point (merge by id, edges to missing nodes dropped).
    """
    return builder.process_event(event)
