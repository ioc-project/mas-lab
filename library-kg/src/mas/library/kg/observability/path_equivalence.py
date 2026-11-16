#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Native events -> KG versus native events -> OTel (converter) -> ``norm``.

Both graphs are built from the same events and nothing runs on either result: the
native fold of the events, and the graph ``norm`` returns for the converter's output.
:func:`compare_trajectories` pairs the two node by node and lists every difference.

* Batch: the converter's spans, folded by ``norm.normalize``.
* Realtime (``realtime=True``): the native fold fed one event at a time, and the
  converter in realtime mode: its spans (exported when they end) and its runtime
  events (``*.started`` / ``*.completed`` LogRecords), folded by ``norm``'s
  ``RealtimeKG``. A call that never ended is never exported as a span; its
  ``*.started`` record is what ``norm`` gets of it.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from mas.library.kg.core.event_mappings import CHECK_INTERVALS, parse_categories
from mas.library.kg.observability.otel_via_norm import to_clickhouse_spans
from mas.library.kg.pipeline import build_kg_document


def node_type(node: dict[str, Any]) -> str:
    raw = node.get("node_type") or node.get("@type") or ""
    text = str(raw)
    if "/" in text:
        text = text.rsplit("/", 1)[-1]
    if "#" in text:
        text = text.rsplit("#", 1)[-1]
    return text


def _lab_graphs(
    events: list[dict[str, Any]],
    categories: Any = None,
    *,
    realtime: bool = False,
) -> tuple[
    str,
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    """Both folds of one event list: the native fold and converter -> ``norm``.

    Nothing runs on either result: the native graph is the fold of the events with the given
    opt-in ``categories`` (default: none, the core classes) and the OTel graph is what
    ``norm`` returns for the converter's output (the default export layers, or every layer,
    extension span and annotation when ``categories`` enables any: ``norm`` has no category
    gating, it maps every span it is given). With ``realtime``, the native fold takes the
    events one at a time and ``norm``'s ``RealtimeKG`` folds the converter's spans and its
    runtime events. Returns the run id the native ids carry, the native nodes and edges, the
    OTel nodes and edges, and the ClickHouse-shaped records ``norm`` was given.
    """
    import json
    import tempfile
    from pathlib import Path

    from mas.library.telemetry.conversion.replay import replay_events_file
    from mas.library.telemetry.conversion.session import runtime_events_path
    from mas.library.telemetry.conversion.topology import derive_app_name
    from norm import RealtimeKG, normalize, realtime_record_to_span

    run_id = next((str(e["run_id"]) for e in events if e.get("run_id")), "run")
    doc = build_kg_document(events, run_id=run_id, categories=categories, realtime=realtime)
    everything = bool(parse_categories(categories))
    layers = {"governance": everything, "annotation": everything}
    with tempfile.TemporaryDirectory() as tmp:
        source, spans = Path(tmp) / "events.jsonl", Path(tmp) / "spans.jsonl"
        source.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
        replay_events_file(
            source,
            spans,
            converter_profile="observe_sdk",
            export_layers=layers,
            extensions=everything,
            realtime=realtime,
            app_name=derive_app_name(events, fallback="mas"),
        )

        def read(path: Path) -> list[dict[str, Any]]:
            if not path.exists():
                return []
            return [
                json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line
            ]

        rows = to_clickhouse_spans(read(spans))
        records = [realtime_record_to_span(r) for r in read(runtime_events_path(spans))]
    if realtime:
        otel_nodes, otel_edges = RealtimeKG().add_all([*rows, *records]).snapshot()
    else:
        otel_nodes, otel_edges = normalize(rows)
    return (
        run_id,
        list(doc.get("nodes") or []),
        list(doc.get("edges") or []),
        list(otel_nodes),
        list(otel_edges),
        [*rows, *records],
    )


_CALL_TYPES = (
    "AgentCall",
    "LLMCall",
    "ToolCall",
    "ProcessingCall",
    "SkillCall",
    "MemoryCall",
    "RAGQuery",
    "ThinkingCall",
)
_ANNOTATION_TYPES = ("CallAnnotation", "GovernanceEvent", "ContextContribution")
_CATALOG_TYPES = ("Agent", "LLM", "Tool", "Processing", "MAS", "Skill", "Memory", "Worker")
_NOT_COMPARED = frozenset({"id", "@type", "node_type", "spanId", "parentSpanId"})
_TIMING_FIELDS = frozenset({"startTime", "endTime", "duration", "eventTime"})


def compare_trajectories(
    events: list[dict[str, Any]],
    *,
    timing_tolerance_ms: float = 1.0,
    categories: Any = None,
    realtime: bool = False,
) -> list[str]:
    """Differences between the native fold and converter -> ``norm``; empty means they agree.

    Both graphs are built from the same events with no post-processing. The explicit identity
    anchors them: a native call node id ends in the call id, the converter writes that call id
    on its span (``mas.call.id``), so the call is the ``norm`` node of that span; a native State
    id ends in the producer's State id, which ``norm`` hashes into its own State id; a
    Transition is the one its call corresponds to. Catalog nodes pair by class and name. An
    annotation pairs by its class, kind, time and the paired node it annotates.

    Every paired node is compared field by field (timing to within ``timing_tolerance_ms``;
    native stamps seconds, spans nanoseconds), and every edge must exist on both sides between
    the same paired nodes. A node of either side with no counterpart is a difference. Each line
    is ``<key>: <detail>`` with a stable key (``LLMCall.name``, ``edge only OTel usesTool``);
    the same difference on several nodes is counted on one line. ``realtime`` compares the
    realtime folds (see :func:`_lab_graphs`).
    """
    from norm.ioa_observe.trajectory import state_node_id

    run_id, native_nodes, native_edges, otel_nodes, otel_edges, rows = _lab_graphs(
        events, categories, realtime=realtime
    )
    native = {str(n["id"]): n for n in native_nodes}
    otel = {str(n["id"]): n for n in otel_nodes}
    problems: list[str] = []
    for side, nodes in (("native", native_nodes), ("OTel", otel_nodes)):
        repeated = [i for i, n in Counter(str(n["id"]) for n in nodes).items() if n > 1]
        problems += [f"{side} duplicate id: {i}" for i in sorted(repeated)]

    pair: dict[str, str] = {}  # native node id -> OTel node id

    # Calls: the span that carries the call id.
    spans_of_call: dict[str, list[str]] = {}
    for row in rows:
        call_id = (row.get("SpanAttributes") or {}).get("mas.call.id")
        if call_id:
            known = spans_of_call.setdefault(str(call_id), [])
            if str(row["SpanId"]) not in known:  # a span and its realtime records
                known.append(str(row["SpanId"]))
    otel_of_span = {str(n["spanId"]): i for i, n in otel.items() if n.get("spanId")}
    for nid, node in native.items():
        ntype = node_type(node)
        if ntype not in _CALL_TYPES:
            continue
        call_id = nid.removeprefix(f"{ntype.lower()}:{run_id}:")
        if ntype == "ProcessingCall" and str(node.get("name")) in CHECK_INTERVALS:
            # The converter keys a check interval ``{call_id}:{kind}_start``.
            call_id = f"{call_id}:{node['name']}_start"
        spans = spans_of_call.get(call_id, [])
        found = otel_of_span.get(spans[0]) if len(spans) == 1 else None
        if found is not None and node_type(otel[found]) == ntype:
            pair[nid] = found

    # Session and MASCall are unique; catalog nodes pair by class and name.
    for ntype in ("Session", "MASCall", *_CATALOG_TYPES):
        left = [i for i, n in native.items() if node_type(n) == ntype]
        right = [i for i, n in otel.items() if node_type(n) == ntype]
        by_name = {str(otel[i].get("name")): i for i in right}
        unique = ntype in ("Session", "MASCall")
        for i in left:
            if unique and len(left) == len(right) == 1:
                pair[i] = right[0]
            elif not unique and str(native[i].get("name")) in by_name:
                pair[i] = by_name[str(native[i].get("name"))]

    # Annotations carry no call id: they pair by class, kind, time (to the millisecond) and
    # the node they annotate (already paired); a group of several pairs only when both sides
    # hold the same number, in time and payload order.
    def annotation_groups(
        nodes: dict[str, dict[str, Any]], edges: list[dict[str, Any]], mapping: dict[str, str]
    ) -> dict[tuple, list[str]]:
        target = {
            str(e["from_id"]): mapping.get(str(e["to_id"]), str(e["to_id"]))
            for e in edges
            if e.get("edge_type") == "annotates"
        }
        groups: dict[tuple, list[str]] = {}
        for i, n in nodes.items():
            if node_type(n) in _ANNOTATION_TYPES:
                stamp = round(float(n.get("eventTime") or 0.0) * 1000.0)
                key = (node_type(n), n.get("eventKind"), stamp, target.get(i, ""))
                groups.setdefault(key, []).append(i)
        return groups

    otel_groups = annotation_groups(otel, otel_edges, {})
    for key, left in annotation_groups(native, native_edges, pair).items():
        right = otel_groups.get(key, [])
        if len(left) == len(right):

            def order(graph: dict[str, dict[str, Any]]) -> Any:
                return lambda i: (
                    float(graph[i].get("eventTime") or 0.0),
                    str(graph[i].get("payload")),
                )

            for i, o in zip(sorted(left, key=order(native)), sorted(right, key=order(otel))):
                pair[i] = o

    # States: the producer's State id, hashed by norm with the session id.
    sessions = [i for i, n in otel.items() if node_type(n) == "Session"]
    session_id = sessions[0] if len(sessions) == 1 else ""
    for nid, node in native.items():
        if node_type(node) == "State":
            pair[nid] = state_node_id(session_id, nid.removeprefix(f"state:{run_id}:"))

    # Transitions: the one the paired call corresponds to.
    def transition_of(edges: list[dict[str, Any]]) -> dict[str, str]:
        return {
            str(e["from_id"]): str(e["to_id"])
            for e in edges
            if e.get("edge_type") == "correspondsToTransition"
        }

    native_transition, otel_transition = transition_of(native_edges), transition_of(otel_edges)
    for call, transition in native_transition.items():
        if call in pair and pair[call] in otel_transition:
            pair[transition] = otel_transition[pair[call]]

    taken = set(pair.values())
    for side, graph, mapped in (("native", native, set(pair)), ("OTel", otel, taken)):
        lost = Counter(node_type(n) for i, n in graph.items() if i not in mapped)
        problems += [
            f"{t} without counterpart ({side}): {c} node(s)" for t, c in sorted(lost.items())
        ]
    for nid in sorted(pair):
        if pair[nid] not in otel:
            problems.append(f"{node_type(native[nid])} pairs to no OTel node: {nid}")

    # Fields of paired nodes.
    differing: dict[str, list[tuple[Any, Any]]] = {}
    for nid, oid in pair.items():
        if nid not in native or oid not in otel:
            continue
        left, right = native[nid], otel[oid]
        for key in sorted((set(left) | set(right)) - _NOT_COMPARED):
            a, b = left.get(key), right.get(key)
            if key in _TIMING_FIELDS and a is not None and b is not None:
                scale = 1.0 if key == "duration" else 1000.0
                same = abs(float(a) - float(b)) * scale <= timing_tolerance_ms
            else:
                same = a == b
            if not same:
                differing.setdefault(f"{node_type(left)}.{key}", []).append((a, b))
    for key, cases in sorted(differing.items()):
        a, b = cases[0]
        problems.append(f"{key}: {len(cases)} differ, e.g. native {a!r:.60} vs OTel {b!r:.60}")

    # Edges between paired nodes.
    def bag(edges: list[dict[str, Any]], mapping: dict[str, str]) -> Counter:
        return Counter(
            (
                str(e.get("edge_type")),
                mapping.get(str(e.get("from_id")), str(e.get("from_id"))),
                mapping.get(str(e.get("to_id")), str(e.get("to_id"))),
            )
            for e in edges
        )

    left_edges, right_edges = bag(native_edges, pair), bag(otel_edges, {})
    for side, extra in (("native", left_edges - right_edges), ("OTel", right_edges - left_edges)):
        by_type: dict[str, list[tuple[str, str, str]]] = {}
        for edge in extra.elements():
            by_type.setdefault(edge[0], []).append(edge)
        for etype, cases in sorted(by_type.items()):
            problems.append(
                f"edge only {side} {etype}: {len(cases)}, e.g. {cases[0][1]} -> {cases[0][2]}"
            )
    return problems
