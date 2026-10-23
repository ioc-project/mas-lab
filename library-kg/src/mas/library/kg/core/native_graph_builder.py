#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Event-by-event native events.jsonl -> KG dispatch core.

Mirrors the reference OTel normalizer's shape (``norm.ioa_observe.build``):
a small ``process_event`` dispatch loop, handlers in
``observability/native/handlers/`` (one per event-kind family, each reading
attributes directly off one event and mutating this builder's shared
state), and a handful of named post-processing passes that run exactly
once, in :meth:`NativeGraphBuilder.finalize`, for things that genuinely
need the complete event set (contains-edge inference, annotation
resolution, State/Transition sibling-chaining, identity normalization, ...).

Two thin entry points share this one implementation:

* :func:`build_graph_batch` — loop ``process_event`` over a full
  ``events.jsonl`` list, then ``finalize()``. Used by
  ``core.graph_builder.extract_graph`` (via :func:`build_graph_from_normalized`)
  and by ``pipeline.build_kg_document``.
* :func:`build_graph_realtime` — feed one incoming event to a long-lived
  :class:`NativeGraphBuilder` instance and get back just the
  nodes/edges it just added; a caller elsewhere decides when to also call
  ``finalize()`` (e.g. at session end) to run the cross-event passes.
"""

from __future__ import annotations

import logging
import warnings
from typing import Any, Dict, List, Optional, Tuple

from mas.library.kg.core.graph_builder import (
    _annotate_one_event,
    _annotation_id,
    _backfill_missing_parent_call_ids,
    _build_application_layer,
    _call_nodes_merge_key,
    _derived_from_edges,
    _edge_canonical_id,
    _execution_id,
    _extract_catalog_layer,
    _GapDedup,
    _infer_app_name_from_ids,
    _infer_contains_edges,
    _node_canonical_id,
    _node_human_id,
    _resolve_annotation_edges,
    _split_composite_run_id,
    layer_skip_kinds,
    normalize_events,
)
from mas.library.kg.core.output_rendering import (
    agent_output_text,
    derive_tool_use_completions,
    materialize_agent_outputs,
)
from mas.library.kg.core.state_transition import synthesize_states_and_transitions

logger = logging.getLogger(__name__)


def _timestamp_sorted(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Causal order for interleaved cached traces (stable on equal timestamps)."""
    return sorted(
        events,
        key=lambda event: (
            float(event["timestamp"])
            if isinstance(event.get("timestamp"), (int, float))
            else float("inf"),
        ),
    )


class NativeGraphBuilder:
    """Stateful event-by-event accumulator for the native events.jsonl -> KG path.

    Owns everything a single-arrival-order pass over native events needs:
    the ontology index, the call-node / edge / annotation accumulation
    (the native path's dict-based equivalent of ``norm``'s ``Registry``),
    open-call tracking state for routing-derived parent inference
    (``_active_exec`` / ``_pending_parent``), and the observability-gap
    dedup counters.
    """

    def __init__(
        self,
        ontology: Any = None,
        run_id: str = "",
        *,
        include_infrastructure: bool = False,
        include_trajectory: bool = True,
        include_provenance: bool = False,
        include_governance: bool = False,
        strict: bool = False,
        session_id_override: Optional[str] = None,
        split_session_id: bool = True,
        app_name_override: Optional[str] = None,
        application_node: bool = False,
        rewrite_tool_delegation: bool = True,
        # Deprecated, accepted-and-ignored for call-site compatibility:
        # the heuristics/fabrication they used to gate have been removed
        # outright (see core/graph_builder.py's relocation comment).
        fill_synthesized_processing_defaults: bool = True,
        allow_heuristics: bool = False,
    ) -> None:
        del fill_synthesized_processing_defaults, allow_heuristics
        self.ontology = ontology if ontology is not None else {}
        self.run_id = run_id
        self.strict = strict
        self.session_id_override = session_id_override
        self.split_session_id = split_session_id
        self.app_name_override = app_name_override
        self.application_node = application_node
        self.rewrite_tool_delegation = bool(rewrite_tool_delegation)
        self._rewritten_delegate_ids: set[str] = set()
        self._delegated_agent_ids: set[str] = set()
        self._resume_pending: Dict[str, str] = {}
        self._agent_visit_count: Dict[str, int] = {}

        self._skip_kinds = layer_skip_kinds(
            include_infrastructure=include_infrastructure,
            include_trajectory=include_trajectory,
            include_provenance=include_provenance,
            include_governance=include_governance,
        )
        self._gaps = _GapDedup(run_id)

        # call_id (or merge-key) -> node dict. The native path's equivalent
        # of norm's Registry: a shared, mutable accumulator every handler
        # reads and writes directly.
        self.call_nodes: Dict[str, Dict[str, Any]] = {}
        self.edges: List[Dict[str, Any]] = []
        # Point-in-time annotation nodes — resolved to 'annotates' edges in
        # finalize() once the full call_nodes map is available.
        self.raw_annotations: List[Dict[str, Any]] = []
        # L4 ContextContribution nodes (one-shot, not start/end paired).
        self.cpr_nodes: List[Dict[str, Any]] = []
        self.run_ids: set[str] = set()
        self.agent_ids: set[str] = set()

        # Routing-derived parent_call_id inference state (single pass, in
        # arrival order) — see handlers/agent.py and handlers/annotation.py.
        self._active_exec: Dict[str, str] = {}
        self._pending_parent: Dict[str, str] = {}

    # -- Classification (realtime path only; batch pre-classifies via
    #    normalize_events before process_event ever sees the event) --------

    def _classify_one(self, raw_event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if self._skip_kinds and raw_event.get("kind") in self._skip_kinds:
            return None
        return _annotate_one_event(
            raw_event, self.ontology, self.run_id, self.strict, self._gaps.report
        )

    # -- Shared call-node upsert primitive, used by every handlers/*.py -----

    def upsert_call(self, event: Dict[str, Any], local_name: str) -> Optional[Dict[str, Any]]:
        """Create (on ``*_start``) or update (on ``*_end``) one call-tree node.

        Shared by every per-kind handler — this is the common part of
        "turn one structural event into/onto its KG node" that doesn't vary
        by call family; each handler does its own class-specific attribute
        enrichment on the node this returns.
        """
        call_id = event.get("call_id", "")
        if not call_id:
            return None
        agent_id = event.get("agent_id", "")
        kind = event.get("kind", "")
        run_id = event.get("run_id", "")

        merge_key = _call_nodes_merge_key(call_id, local_name, agent_id)
        existing = self.call_nodes.get(merge_key)
        if kind.endswith("_start") and existing is not None:
            if existing.get("startTime") is not None and existing.get("endTime") is None:
                # Duplicate start before first end — preserve for plot replay,
                # rather than silently overwriting the in-progress call.
                effective_agent_id = agent_id or event.get("source_agent_id", "")
                self.raw_annotations.append(
                    {
                        "node_type": "CallAnnotation",
                        "id": f"ann-{_annotation_id(event, run_id)}",
                        "annotationId": f"ann-{_annotation_id(event, run_id)}",
                        "agentId": effective_agent_id,
                        "kind": kind,
                        "callId": call_id,
                        "parentCallId": event.get("parent_call_id") or "",
                        "timestamp": event.get("timestamp"),
                        "runId": run_id,
                    }
                )
                return None

        node = self.call_nodes.setdefault(
            merge_key,
            {
                "node_type": local_name,
                "id": call_id,
                "callId": call_id,
                "executionId": _execution_id(local_name, call_id),
                "agentId": agent_id,
                "parentCallId": event.get("parent_call_id") or "",
                "kindBase": kind.replace("_start", "").replace("_end", ""),
                "masUri": event.get("mas_uri", ""),
                "spanLevel": event.get("span_level", ""),
                "runId": run_id,
            },
        )
        if kind.endswith("_start"):
            node["startTime"] = event.get("timestamp")
        elif kind.endswith("_end"):
            node["endTime"] = event.get("timestamp")

        # Provenance: keep ordered source telemetry record IDs used to
        # create/update this ExecutionElement.
        span_id = str(event.get("record_id") or "").strip()
        source_ids = node.setdefault("sourceRecordIds", [])
        if span_id and isinstance(source_ids, list) and span_id not in source_ids:
            source_ids.append(span_id)

        if kind.endswith("_end"):
            node["status"] = event.get("status", "success")

        return node

    def close_agent_visit_for_delegate(self, agent_id: str, event: Dict[str, Any]) -> None:
        """End the caller's AgentCall so a delegated agent is a sibling visit."""
        current = self._active_exec.get(agent_id)
        if not current:
            return
        for node in self.call_nodes.values():
            if str(node.get("callId") or "") == current:
                node["endTime"] = event.get("timestamp")
                node.setdefault("outputRaw", event.get("tool_name") or "delegate")
                break
        self._resume_pending[agent_id] = current
        self._active_exec.pop(agent_id, None)

    def ensure_resumed_agent_call(self, event: Dict[str, Any]) -> None:
        """Open a new AgentCall after a rewritten delegation (LangGraph visit)."""
        from mas.library.kg.observability.native.handlers.agent import handle_agent_call

        agent_id = str(event.get("agent_id") or "")
        if not agent_id or self._active_exec.get(agent_id):
            return
        original = self._resume_pending.pop(agent_id, None)
        if not original:
            return
        visit = int(self._agent_visit_count.get(agent_id) or 1) + 1
        self._agent_visit_count[agent_id] = visit
        resume = {
            "kind": "execution_start",
            "agent_id": agent_id,
            "call_id": f"{original}-resume-{visit}",
            "parent_call_id": None,
            "input": event.get("input") or event.get("output") or "",
            "timestamp": event.get("timestamp"),
            "run_id": event.get("run_id") or self.run_id,
            "session_id": event.get("session_id"),
            "mas_class": "AgentCall",
        }
        handle_agent_call(resume, self)

    # -- Per-event dispatch --------------------------------------------------

    def process_event(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Classify (if needed) and dispatch one event to its handler.

        Works identically whether called once per incoming event (realtime)
        or in a loop over a full ``events.jsonl`` list (batch) — the only
        difference is whether ``event`` already carries ``mas_class`` (set
        by a prior batch ``normalize_events`` call) or needs classifying
        here, on the fly.

        Returns whatever node dicts this event newly created or updated
        (empty for a dropped/suppressed event), so a realtime caller can
        report just the incremental delta.
        """
        if "mas_class" not in event:
            classified = self._classify_one(dict(event))
            if classified is None:
                return []
            event = classified

        from mas.library.kg.observability.native.handlers import HANDLERS
        from mas.library.kg.observability.native.handlers.structural import handle_generic_call

        run_id = event.get("run_id", "")
        agent_id = event.get("agent_id", "")
        local_name = event.get("mas_class", "ExecutionElement")

        self.run_ids.add(run_id)
        if (
            agent_id
            and str(agent_id).strip().lower() not in {"", "unknown", "agent", "mas"}
            and local_name in {"AgentCall", "MASCall"}
        ):
            self.agent_ids.add(agent_id)

        # Skip suppressed events (None-mapped kinds and abstract fallback).
        if local_name in ("ExecutionElement", None):
            return []

        handler = HANDLERS.get(local_name, handle_generic_call)
        return handler(event, self)

    def _flatten_rewritten_agent_siblings(
        self,
        call_nodes: Dict[str, Dict[str, Any]],
        edges: List[Dict[str, Any]],
    ) -> None:
        """LangGraph / norm: delegated AgentCalls are siblings, not nested.

        ``rewrite_tool_delegation`` intercepts ``delegate_to_*`` tools. The
        target AgentCall must not remain a child of the caller AgentCall
        (or of the dropped tool id) — parent is the MASCall/TaskCall, same
        as observe-sdk ``invoke_agent``.
        """
        if not self.rewrite_tool_delegation:
            return
        by_call = {
            str(node.get("callId") or ""): node
            for node in call_nodes.values()
            if node.get("callId")
        }
        container_id = next(
            (
                str(node.get("callId") or "")
                for node in call_nodes.values()
                if node.get("node_type") in {"MASCall", "TaskCall"} and node.get("callId")
            ),
            "",
        )
        rewritten = self._rewritten_delegate_ids
        delegated = self._delegated_agent_ids
        for node in call_nodes.values():
            if node.get("node_type") != "AgentCall":
                continue
            parent = str(node.get("parentCallId") or "")
            agent = str(node.get("agentId") or node.get("agent_id") or "")
            if parent and (parent in rewritten or agent in delegated):
                node["parentCallId"] = container_id
        kept: List[Dict[str, Any]] = []
        for edge in edges:
            src = by_call.get(str(edge.get("from_id") or ""))
            dst = by_call.get(str(edge.get("to_id") or ""))
            etype = str(edge.get("edge_type") or "")
            dst_agent = str((dst or {}).get("agentId") or (dst or {}).get("agent_id") or "")
            if (
                src
                and dst
                and src.get("node_type") == "AgentCall"
                and dst.get("node_type") == "AgentCall"
                and etype in {"contains", "hasCall", "hasAgentCall"}
                and (str(edge.get("to_id") or "") in rewritten or dst_agent in delegated)
            ):
                continue
            kept.append(edge)
        edges[:] = kept

    # -- Cross-event post-processing ----------------------------------------

    def finalize(self) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Run the post-processing passes that need the complete event set,
        and return the final ``(nodes, edges)``.

        Order (mirrors the previous monolithic ``extract_graph``):
        output rendering -> annotation resolution -> session/application
        layers -> contains-edge inference -> parent-id backfill (last
        resort) -> State/Transition synthesis -> derivedFrom edges ->
        catalog layer -> session trajectory anchors -> identity
        normalization -> scope-model mapping.
        """
        self._gaps.log_rollup()

        call_nodes = self.call_nodes
        edges = self.edges
        run_ids = self.run_ids
        agent_ids = self.agent_ids

        # AgentCall.agentName may be absent on orphan _end events (no matching _start).
        for node in call_nodes.values():
            if node["node_type"] == "AgentCall" and not node.get("agentName"):
                node["agentName"] = node.get("agentId", "")

        # Synthesize readable completions for LLM tool-use calls (empty content).
        derive_tool_use_completions(call_nodes)

        # Materialize AgentCall outputParts and render compatibility outputContent.
        materialize_agent_outputs(call_nodes)

        # -- Resolve annotation edges -----------------------------------
        annotation_nodes, ann_edges = _resolve_annotation_edges(self.raw_annotations, call_nodes)
        edges.extend(ann_edges)

        # -- Session nodes (one per run) ---------------------------------
        session_nodes: List[Dict[str, Any]] = []
        run_to_session: Dict[str, str] = {}
        for run_id in sorted(run_ids):
            run_calls = [n for n in call_nodes.values() if n.get("runId") == run_id]
            mas_calls = [n for n in run_calls if n["node_type"] == "MASCall"]
            if mas_calls:
                outer = mas_calls[0]
            else:
                timed = [
                    n
                    for n in run_calls
                    if n.get("startTime") is not None and n["node_type"] == "AgentCall"
                ]
                outer = min(timed, key=lambda n: float(n.get("startTime") or 0)) if timed else None

            derived_app_name, derived_session_id = _split_composite_run_id(run_id)
            inferred_app_name = _infer_app_name_from_ids(run_id, self.session_id_override)
            app_name = (
                (self.app_name_override or "").strip() or inferred_app_name or derived_app_name
            )
            if self.split_session_id and derived_session_id != run_id:
                session_id = derived_session_id
            elif self.split_session_id and "/" in str(run_id):
                # Hierarchical run IDs are already session-like (e.g. app/exp/scenario/item/r1).
                session_id = str(run_id)
            else:
                session_id = f"session-{run_id}"

            if not self.split_session_id and not app_name:
                warnings.warn(
                    "split_session_id=False: appName cannot be inferred from run_id/session_id; "
                    "set app_name_override (CLI: --app-name) to retain application mapping.",
                    stacklevel=2,
                )

            run_to_session[run_id] = session_id
            session_nodes.append(
                {
                    "node_type": "Session",
                    "id": session_id,
                    "sessionId": session_id,
                    "executionId": f"exec-Session-{run_id}",
                    "runId": run_id,
                    "appName": app_name,
                    "inputQuery": (
                        (outer.get("inputContent") or outer.get("masName") or "") if outer else ""
                    ),
                    "finalResponse": agent_output_text(outer) if outer else "",
                    "startTime": outer.get("startTime") if outer else None,
                    "endTime": outer.get("endTime") if outer else None,
                }
            )

        # -- Session -> Run edges (contains) ------------------------------
        for sn in session_nodes:
            rid = sn.get("runId", "")
            if rid:
                edges.append(
                    {
                        "edge_type": "contains",
                        "from_id": sn["id"],
                        "from_type": "session",
                        "to_id": rid,
                        "to_type": "run",
                    }
                )

        # -- Optional Application nodes -----------------------------------
        application_nodes: List[Dict[str, Any]] = []
        if self.application_node:
            application_nodes, application_edges = _build_application_layer(session_nodes)
            edges.extend(application_edges)

        # -- Structural nodes ----------------------------------------------
        run_nodes = [{"node_type": "Run", "id": rid, "runId": rid} for rid in sorted(run_ids)]
        agent_nodes = [
            {"node_type": "Agent", "id": aid, "agentId": aid} for aid in sorted(agent_ids)
        ]
        call_node_list = list(call_nodes.values())

        # -- contains edges (timestamp enclosure) ---------------------------
        contains_edges = _infer_contains_edges(call_nodes)
        edges.extend(contains_edges)

        # Parent-call-id precedence (each tier only fills what the previous
        # one left unset): (1) the runtime's own explicit parent_call_id,
        # (2) routing-derived, applied event-by-event in handlers/agent.py +
        # handlers/annotation.py via _active_exec/_pending_parent, (3) this
        # timestamp-containment-derived last resort.
        _backfill_missing_parent_call_ids(call_nodes, contains_edges)
        self._flatten_rewritten_agent_siblings(call_nodes, edges)

        # -- Run -> Call edges -----------------------------------------------
        for node in call_node_list:
            edges.append(
                {
                    "edge_type": "hasCall",
                    "from_id": node.get("runId", ""),
                    "from_type": "run",
                    "to_id": node["callId"],
                    "to_type": "call",
                }
            )

        # -- State / Transition nodes ----------------------------------------
        st_nodes, st_edges = synthesize_states_and_transitions(call_nodes, agent_output_text)
        edges.extend(st_edges)

        # -- Sigma derivedFrom edges (L4 parents[]) ---------------------------
        all_cc_nodes = self.cpr_nodes + [
            n for n in annotation_nodes if n.get("node_type") == "ContextContribution"
        ]
        edges.extend(_derived_from_edges(all_cc_nodes))

        # -- Structural type index nodes: Tool / LLM / Skill / Processing -----
        cat_nodes, cat_edges = _extract_catalog_layer(call_node_list)
        edges.extend(cat_edges)

        nodes = (
            application_nodes
            + session_nodes
            + run_nodes
            + agent_nodes
            + call_node_list
            + annotation_nodes
            + self.cpr_nodes
            + st_nodes
            + cat_nodes
        )

        # -- sessionId harmonization across State/Transition synthetic nodes --
        for run_id, session_id in run_to_session.items():
            old_session_id = f"session-{run_id}"
            if old_session_id == session_id:
                continue
            for n in nodes:
                if n.get("sessionId") == old_session_id:
                    n["sessionId"] = session_id

        # -- session_id override (e.g. --session-id from CLI) -----------------
        if self.session_id_override and len(run_ids) == 1:
            _run_id = next(iter(run_ids))
            _old_sid = run_to_session.get(_run_id, f"session-{_run_id}")
            logger.debug(
                "NativeGraphBuilder.finalize: renaming sessionId %r -> %r",
                _old_sid,
                self.session_id_override,
            )
            for n in nodes:
                if n.get("id") == _old_sid:
                    n["id"] = self.session_id_override
                if n.get("sessionId") == _old_sid:
                    n["sessionId"] = self.session_id_override
            for e in edges:
                if e.get("from_id") == _old_sid:
                    e["from_id"] = self.session_id_override
                if e.get("to_id") == _old_sid:
                    e["to_id"] = self.session_id_override
        elif self.session_id_override and len(run_ids) > 1:
            warnings.warn(
                f"session_id_override={self.session_id_override!r} ignored: "
                f"trace has {len(run_ids)} run_ids, override only applies to single-run traces.",
                stacklevel=2,
            )

        # -- Session trajectory anchors ----------------------------------------
        # `call_nodes` is keyed by the upsert merge key (plain call_id for
        # most node types, but "<call_id>::<agent_id>" for AgentCall -- see
        # _call_nodes_merge_key), not by the plain call_id a Transition's
        # realizesCallId actually carries. Look up by each node's own real
        # callId field instead (same pattern as
        # graph_builder._backfill_missing_parent_call_ids), or a nested
        # AgentCall's transitions are silently misread as top-level here.
        call_node_by_call_id: Dict[str, Dict[str, Any]] = {
            str(n.get("callId")): n for n in call_nodes.values() if n.get("callId")
        }
        transitions_by_session: Dict[str, List[Dict[str, Any]]] = {}
        for node in nodes:
            if node.get("node_type") != "Transition":
                continue
            sess = str(node.get("sessionId") or "")
            if not sess:
                continue
            transitions_by_session.setdefault(sess, []).append(node)

        for session_node in session_nodes:
            session_id = str(session_node.get("sessionId") or "")
            session_transitions = transitions_by_session.get(session_id, [])
            if not session_transitions:
                continue
            top_level_transitions = [
                t
                for t in session_transitions
                if not call_node_by_call_id.get(t.get("realizesCallId", ""), {}).get(
                    "parentCallId"
                )
            ]
            ordered = sorted(
                top_level_transitions or session_transitions,
                key=lambda node: float(node.get("transitionTimestamp") or 0),
            )
            first_state = ordered[0].get("fromState")
            last_state = ordered[-1].get("toState")
            if first_state:
                edges.append(
                    {
                        "edge_type": "hasInitialState",
                        "from_id": session_node["id"],
                        "from_type": "session",
                        "to_id": first_state,
                        "to_type": "state",
                    }
                )
            if last_state:
                edges.append(
                    {
                        "edge_type": "hasFinalState",
                        "from_id": session_node["id"],
                        "from_type": "session",
                        "to_id": last_state,
                        "to_type": "state",
                    }
                )

        # Remove deprecated scalar link fields from exported nodes.
        for n in nodes:
            n.pop("sourceCallId", None)

        # -- Identity normalization ---------------------------------------------
        node_by_local_id: Dict[str, Dict[str, Any]] = {}
        old_id_to_new_id: Dict[str, str] = {}
        for n in nodes:
            old_id = n.get("id")
            n["id"] = _node_human_id(n)
            new_id = n["id"]
            if old_id and old_id != new_id:
                old_id_to_new_id[str(old_id)] = new_id
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

        for e in edges:
            e["from_id"] = old_id_to_new_id.get(str(e.get("from_id") or ""), e.get("from_id"))
            e["to_id"] = old_id_to_new_id.get(str(e.get("to_id") or ""), e.get("to_id"))
            src = str(e.get("from_id") or "")
            dst = str(e.get("to_id") or "")
            etype = str(e.get("edge_type") or "edge")
            e["id"] = f"{etype}:{src}->{dst}"
            e["canonicalId"] = _edge_canonical_id(e, node_by_local_id)

        from mas.library.kg.core.ontology_align import align_graph
        from mas.library.kg.core.oxp_models import map_graph

        return map_graph(*align_graph(nodes, edges))


def build_graph_from_normalized(
    normalized: List[Dict[str, Any]],
    *,
    session_id_override: Optional[str] = None,
    split_session_id: bool = True,
    app_name_override: Optional[str] = None,
    application_node: bool = False,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Build (nodes, edges) from an already-``normalize_events``-annotated list.

    Used by ``core.graph_builder.extract_graph`` (back-compat wrapper) and
    by :func:`build_graph_batch` below.
    """
    builder = NativeGraphBuilder(
        session_id_override=session_id_override,
        split_session_id=split_session_id,
        app_name_override=app_name_override,
        application_node=application_node,
    )
    for ev in _timestamp_sorted(normalized):
        builder.process_event(ev)
    return builder.finalize()


def build_graph_batch(
    events: List[Dict[str, Any]],
    ontology: Any,
    run_id: str,
    *,
    include_infrastructure: bool = False,
    include_trajectory: bool = True,
    include_provenance: bool = False,
    include_governance: bool = False,
    strict: bool = False,
    session_id_override: Optional[str] = None,
    split_session_id: bool = True,
    app_name_override: Optional[str] = None,
    application_node: bool = False,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Batch entry point: classify the full event list, then build the graph.

    Equivalent to ``extract_graph(normalize_events(events, ontology, run_id,
    ...), ...)`` but expressed as the "loop process_event, then finalize()"
    shape shared with :func:`build_graph_realtime`.
    """
    normalized = normalize_events(
        events,
        ontology,
        run_id,
        include_infrastructure=include_infrastructure,
        include_trajectory=include_trajectory,
        include_provenance=include_provenance,
        include_governance=include_governance,
        strict=strict,
    )
    return build_graph_from_normalized(
        normalized,
        session_id_override=session_id_override,
        split_session_id=split_session_id,
        app_name_override=app_name_override,
        application_node=application_node,
    )


def build_graph_realtime(
    builder: NativeGraphBuilder,
    event: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Realtime entry point: feed one event to a long-lived builder.

    Returns just the nodes this one event newly created/updated and the
    edges added while processing it (the incremental delta) — the caller
    decides when to also call ``builder.finalize()`` (e.g. at session end)
    to run the cross-event passes and get the complete, final graph.
    """
    edges_before = len(builder.edges)
    new_nodes = builder.process_event(event)
    new_edges = builder.edges[edges_before:]
    return new_nodes, new_edges
