"""Events -> Knowledge Graph engine, shared by every event source.

Whatever produced the input events -- native SDK instrumentation, or an OTel
span source already reduced to events.jsonl -- they all converge on the same
intermediate "event" shape before reaching this module. OTel spans themselves
are normalized by ``norm.normalize()`` (see ``observability.otel_via_norm``),
not by this module.

Two stages:
1. :func:`normalize_events` -- annotates each event with its ontology class
   (``mas_class``, ``mas_uri``, ``span_level``) from ``mas-ontology.ttl``
   (parsed lazily via rdflib/``scope_ontology``; degrades gracefully to
   :data:`~mas.library.kg.core.event_mappings.KIND_TO_CLASS` alone when
   those aren't installed), and pairs ``*_start``/``*_end`` events into call
   records with a stable ``call_id``.
2. :func:`extract_graph` -- maps those call records onto KG nodes and
   edges, one event at a time: Session/Agent/catalog nodes, one node per
   call, the State/Transition nodes the events name, and the ontology edges
   connecting them. Nothing is inferred.

Ontology class mapping (from mas-ontology.ttl §1b)::

    Kind prefix              mas: class
    tool_call_*              ToolCall
    llm_call_*               LLMCall
    execution_*              AgentCall
    mas_call_*               MASCall
    rag_query_*              RAGQuery
    memory_call_*            MemoryCall
    processing_call_*        ProcessingCall
    context_assembled        — (structural, no execution class)
    audit                    — (meta)
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, TypedDict

from mas.library.kg._dist import extra_hint
from mas.library.kg.core.event_mappings import KIND_TO_CLASS, skipped_classes
from mas.library.kg.core.scope_models import mas_namespace
from mas.library.kg.exceptions import (
    MissingCallIdError,
    UnknownSpanBoundaryError,
)

# ---------------------------------------------------------------------------
# Typed structures for KG elements
# ---------------------------------------------------------------------------


class NodeDict(TypedDict, total=False):
    """Minimal typed structure for a KG node dict."""

    node_type: str
    id: str
    callId: str
    agentId: str
    startTime: Any
    endTime: Any
    runId: str


class EdgeDict(TypedDict, total=False):
    """Minimal typed structure for a KG edge dict."""

    edge_type: str
    from_id: str
    from_type: str
    to_id: str
    to_type: str


# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)


def extract_plot_events(raw_events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return a replay copy of all raw ObsEvents for multilevel plot parity.

    Stored in ``kg.meta.plot_events`` so the KG plot path can call the same
    ``_build_call_records`` / ``_build_dag`` pipeline as ``events.jsonl`` without
    reading a companion trace file at plot time.

    Event order is preserved (JSONL file order) so annotation hover text matches
    the native plotter, which also keeps ``load_trace`` ordering.
    """
    return [dict(ev) for ev in raw_events if ev.get("kind")]


_MAS_NS = mas_namespace()

# ---------------------------------------------------------------------------
# Ontology loader (rdflib-based, lazy/cached)
# ---------------------------------------------------------------------------


class _OntologyIndex:
    """Parsed index of mas-ontology.ttl execution classes.

    Create via ``_OntologyIndex(ttl_path)`` or use the module-level
    ``_load_ontology(ttl_path)`` convenience function which maintains a
    lightweight path-keyed cache (avoids re-parsing the same TTL within a
    process without coupling cache lifecycle to the class itself).
    """

    def __init__(self, ttl_path: Path) -> None:
        self.ttl_path = ttl_path
        # class local-name → {uri, label, span_level, icon, comment}
        self.classes: Dict[str, Dict[str, Any]] = {}
        self._load()

    @classmethod
    def load(cls, ttl_path: str | Path) -> "_OntologyIndex":
        """Alias for ``_load_ontology`` — returns cached ontology index."""
        return _load_ontology(Path(ttl_path))

    # -- Loading -----------------------------------------------------------

    def _load(self) -> None:
        try:
            import rdflib
        except ImportError:
            raise ImportError(
                "rdflib is required for OTel→KG normalization. "
                f"Install with: uv sync (or {extra_hint('graph')})"
            ) from None

        g = rdflib.Graph()
        g.parse(str(self.ttl_path), format="turtle")

        MAS = rdflib.Namespace(_MAS_NS)
        RDFS = rdflib.RDFS
        OWL = rdflib.OWL

        for cls in g.subjects(rdflib.RDF.type, OWL.Class):
            local = str(cls).replace(_MAS_NS, "")
            if not local or "/" in local or "#" not in str(cls) and _MAS_NS not in str(cls):
                continue
            label = str(g.value(cls, RDFS.label) or local)
            comment = str(g.value(cls, RDFS.comment) or "")
            span_level = str(g.value(cls, MAS.spanLevel) or "")
            icon = str(g.value(cls, MAS.icon) or "")
            self.classes[local] = {
                "uri": str(cls),
                "local_name": local,
                "label": label,
                "span_level": span_level,
                "icon": icon,
                "comment": comment[:120] if comment else "",
            }
        logger.debug("Loaded %d ontology classes from %s", len(self.classes), self.ttl_path)

    def get(self, local_name: str) -> Optional[Dict[str, Any]]:
        return self.classes.get(local_name)

    def __contains__(self, local_name: object) -> bool:
        if not isinstance(local_name, str):
            return False
        return local_name in self.classes


# Module-level ontology cache — keyed by resolved path to avoid re-parsing.
_ontology_cache: Dict[Path, _OntologyIndex] = {}


def _load_ontology(ttl_path: Path) -> _OntologyIndex:
    """Load (or return cached) ontology index for the given TTL path."""
    resolved = ttl_path.resolve()
    if resolved not in _ontology_cache:
        _ontology_cache[resolved] = _OntologyIndex(ttl_path)
    return _ontology_cache[resolved]


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------


def _resolve_ontology_path(config_path: Optional[str]) -> Path:
    """Resolve mas-ontology.ttl from config or the SCOPE package."""
    from mas.library.kg.ontology import resolve_mas_ontology_path

    return resolve_mas_ontology_path(config_path)


def _class_for_event(event: Dict[str, Any]) -> str:
    """Return the ontology local class name for an event dict.

    Suppressed kinds (mapped to ``None`` in ``KIND_TO_CLASS``) return
    ``"ExecutionElement"`` so ``extract_graph`` skips them.
    Kinds absent from the map raise ``UnknownSpanBoundaryError`` — add them
    to core/event_mappings.py:KIND_TO_CLASS or map them to None to suppress.
    This is caught by the caller, ``normalize_events``, which by default
    logs a warning and drops the event instead of letting the exception
    propagate; pass ``strict=True`` to ``normalize_events`` to raise instead.
    """
    kind = event.get("kind", "")
    # OTel round-trip represents duplicate *_start markers as CallAnnotation
    # point spans; the OTel normalizer restores them with a ``content`` field.
    if kind in ("llm_call_start", "tool_call_start") and "content" in event:
        return "CallAnnotation"

    if kind not in KIND_TO_CLASS:
        span_id = event.get("span_id", "unknown")
        raise UnknownSpanBoundaryError(boundary=kind, span_id=span_id)

    local = KIND_TO_CLASS[kind]  # None if explicitly suppressed
    if local is None:
        return "ExecutionElement"
    return local


def _annotation_id(event: Dict[str, Any], run_id: str) -> str:
    """Stable, deterministic ID for annotation / point-in-time events.

    Annotation events (CallAnnotation, ContextContribution, Worker, governance)
    are never paired as start/end and carry no ``call_id``.  Their logical
    identity is derived from the semantic payload (run_id + agent + kind +
    timestamp), not from OTel-only span IDs; different normalizers for the same
    native event should produce the same annotation node identity.
    """
    timestamp = str(event.get("timestamp", ""))
    kind = event.get("kind", "")
    agent_id = event.get("agent_id", "")
    return uuid.uuid5(uuid.NAMESPACE_URL, f"{run_id}|{agent_id}|{kind}|{timestamp}").hex[:16]


class _GapDedup:
    """First-occurrence-logs-in-full, repeats-counted dedup for
    observability-gap warnings.

    Shared by :func:`normalize_events` (batch) and
    ``core.native_graph_builder.NativeGraphBuilder`` (event-by-event): a
    mapping table falling behind an SDK bump can mean every event in a
    trace carries the same unmapped kind, and logging one WARNING per event
    would drown the log in near-identical lines. The first occurrence of
    each distinct gap is logged in full (with run_id + span_id, a specific
    starting point to debug from); repeats are counted silently and can be
    rolled up into one summary line via :meth:`log_rollup`.
    """

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.counts: Dict[Tuple[str, str], int] = {}
        self.last_exc: Dict[Tuple[str, str], Exception] = {}

    def report(self, gap_key: Tuple[str, str], exc: Exception) -> None:
        count = self.counts.get(gap_key, 0) + 1
        self.counts[gap_key] = count
        self.last_exc[gap_key] = exc
        if count == 1:
            logger.warning(
                "normalize_events: observability gap run_id=%s — %s. Skipping "
                "this event and continuing (pass strict=True to raise instead).",
                self.run_id,
                exc,
            )

    def log_rollup(self) -> None:
        for gap_key, count in self.counts.items():
            if count > 1:
                gap_type, key_value = gap_key
                logger.warning(
                    "normalize_events: observability gap run_id=%s — %r (kind=%r) "
                    "recurred %d times in this trace; only the first occurrence "
                    "was logged in full (last: %s).",
                    self.run_id,
                    gap_type,
                    key_value,
                    count,
                    self.last_exc[gap_key],
                )


def _annotate_one_event(
    out: Dict[str, Any],
    ontology: Any,
    run_id: str,
    strict: bool,
    report_gap: Any,
    *,
    skip_classes: frozenset = frozenset(),
) -> Optional[Dict[str, Any]]:
    """Classify + annotate one event dict, in place.

    Shared by :func:`normalize_events` (batch, one call per trace) and
    ``NativeGraphBuilder._classify_one`` (realtime, one call per incoming
    event) — this is the single per-event classification step both paths
    reuse, per the module's "one dispatch core, two thin input adapters"
    design.

    Returns the same dict (mutated) on success, or ``None`` when the event
    should be dropped (unmapped kind, a class of a disabled category, or a structural event missing
    call_id) — ``report_gap`` has already been called in that case unless
    ``strict`` caused the underlying exception to propagate instead.
    """
    try:
        local_name = _class_for_event(out)
    except UnknownSpanBoundaryError as exc:
        if strict:
            raise
        report_gap(("unmapped_kind", out.get("kind", "")), exc)
        return None
    if local_name in skip_classes:
        return None  # a class of a category that is not enabled
    meta = ontology.get(local_name) or {}
    out["mas_class"] = local_name
    out["mas_uri"] = meta.get("uri", _MAS_NS + local_name)
    out["span_level"] = meta.get("span_level", "")
    out["mas_icon"] = meta.get("icon", "")
    # Keep the runtime UUID as call_id — it is already shared between
    # *_start and *_end of the same logical call.  Fall back to a
    # deterministic hash only for old traces without a runtime call_id.
    call_id = str(out.get("call_id") or "").strip()
    if not call_id:
        # Annotation/point-in-time events have no call_id — that is correct.
        # Structural events (anything that forms a call tree node) must carry
        # one. Live trip-planner processing spans leave it blank; synthesize
        # the same start/end key the observe-sdk converter uses.
        local = out.get("mas_class", "")
        if local == "ProcessingCall":
            parent = str(out.get("parent_call_id") or "")
            agent = str(out.get("agent_id") or "")
            extra = str(
                out.get("processing_type")
                or out.get("processing_name")
                or out.get("part_id")
                or out.get("kind")
                or "anon"
            )
            call_id = f"{agent}:{parent}:{extra}"
        elif local not in (
            "CallAnnotation",
            "GovernanceEvent",
            "ContextContribution",
            "Worker",
            "ExecutionElement",
            None,
        ):
            try:
                raise MissingCallIdError(
                    kind=out.get("kind", ""),
                    agent_id=out.get("agent_id", ""),
                    span_id=out.get("span_id", "unknown"),
                )
            except MissingCallIdError as exc:
                if strict:
                    raise
                report_gap(("missing_call_id", out.get("kind", "")), exc)
                return None
    out["call_id"] = call_id or ""
    out["run_id"] = run_id
    rid = str(out.get("event_id") or "").strip()
    if rid:
        out["record_id"] = rid
    return out


def normalize_events(
    events: List[Dict[str, Any]],
    ontology: _OntologyIndex,
    run_id: str,
    *,
    categories: Any = None,
    strict: bool = False,
) -> List[Dict[str, Any]]:
    """Annotate a list of ObsEvents with ontology metadata.

    Each event receives:
      mas_class    str   local class name (ToolCall, LLMCall, …)
      mas_uri      str   full IRI
      span_level   str   call | agent | task | mas | session
      mas_icon     str   icon hint
      call_id      str   runtime UUID preserved as-is; fallback to deterministic
                         hash only for old traces that predate call_id emission.
      parent_call_id str runtime UUID (no translation — it already matches the
                         parent call_id directly).
      run_id       str   benchmark run identifier

    Parameters
    ----------
    categories : None | str | iterable of str, default None
        Opt-in categories to add to the core output: ``capabilities`` (Skill, SkillCall,
        Memory, MemoryCall, RAGQuery), ``annotations`` (CallAnnotation, GovernanceEvent,
        ContextContribution), ``worker`` (Worker) and ``thinking`` (ThinkingCall); ``all``
        enables every one. Events whose class is in a disabled category are dropped.
    strict : bool, default False
        An observability gap (an event kind with no ``KIND_TO_CLASS`` entry,
        or a structural event missing ``call_id``) should never crash a
        benchmark run. By default such events are logged at ``WARNING`` and
        dropped from the KG, and normalization continues with everything
        else in the trace. Set ``strict=True`` (e.g. in CI conformance
        tests) to instead raise :class:`KGNormalizationError` immediately —
        useful when validating that a mapping table is complete.
    """
    skip_classes = skipped_classes(categories)
    gaps = _GapDedup(run_id)

    # Shallow copy + ontology annotation + category filter, in arrival order.
    normalized: List[Dict[str, Any]] = []
    for out in (dict(raw) for raw in events):
        annotated = _annotate_one_event(
            out, ontology, run_id, strict, gaps.report, skip_classes=skip_classes
        )
        if annotated is not None:
            normalized.append(annotated)

    gaps.log_rollup()
    return normalized


# ---------------------------------------------------------------------------
# Graph extraction (nodes + edges) for dry-run + Neo4j step
# ---------------------------------------------------------------------------

# -- Per-class attribute helpers -------------------------------------------


def _execution_id(local_name: str, call_id: str) -> str:
    """Synthesise a stable executionId aligned with the ontology spec."""
    return f"exec-{local_name}-{call_id}"


def _split_composite_run_id(run_id: str) -> Tuple[str, str]:
    """Split ``<appName>_<sessionUuid>`` into ``(appName, sessionId)``.

    Returns ``("", run_id)`` when *run_id* does not end with a UUID suffix.
    """
    if not run_id or "_" not in run_id:
        return "", run_id
    app_name, candidate_sid = run_id.rsplit("_", 1)
    try:
        uuid.UUID(candidate_sid)
    except (ValueError, TypeError, AttributeError):
        return "", run_id
    return app_name, candidate_sid


def _infer_app_name_from_ids(
    run_id: str,
    session_id_override: Optional[str],
) -> str:
    """Infer appName from known identifier conventions.

    Priority:
    1. ``<appName>_<sessionUuid>`` run_id pattern
    2. Hierarchical run_id/session_id: ``<appName>/...``
    """
    app_name, _ = _split_composite_run_id(run_id)
    if app_name:
        return app_name

    rid = str(run_id or "").strip()
    if "/" in rid:
        return rid.split("/", 1)[0].strip()

    sid = str(session_id_override or "").strip()
    if "/" in sid:
        return sid.split("/", 1)[0].strip()

    return ""


def _slug(value: Any) -> str:
    """Return a conservative slug for readable IDs."""
    text = str(value or "").strip().lower()
    out: List[str] = []
    dash = False
    for ch in text:
        if ch.isalnum():
            out.append(ch)
            dash = False
        elif not dash:
            out.append("-")
            dash = True
    return "".join(out).strip("-") or "na"


def _node_canonical_id(node: Dict[str, Any]) -> str:
    """Build a globally unique canonicalId for a KG node."""
    ntype = str(node.get("node_type") or "Node")
    run_id = str(node.get("runId") or "")
    session_id = str(
        node.get("sessionId") or (f"session-{run_id}" if run_id else "session-unknown")
    )

    if ntype == "Session":
        return f"urn:mas:session:{node.get('sessionId') or node.get('id')}"
    if node.get("stateNodeId"):
        return f"urn:mas:session:{session_id}:state:{node['stateNodeId']}"
    if node.get("transitionId"):
        return f"urn:mas:session:{session_id}:transition:{node['transitionId']}"
    if node.get("annotationId"):
        return f"urn:mas:session:{session_id}:annotation:{node['annotationId']}"
    if node.get("callId"):
        return f"urn:mas:session:{session_id}:call:{node['callId']}"

    if ntype in {
        "Agent",
        "MAS",
        "Task",
        "LLM",
        "Tool",
        "Processing",
        "Skill",
        "Workflow",
        "DesignPattern",
        "Memory",
    }:
        stable = node.get("agentId") or node.get("name") or node.get("id")
        return f"urn:mas:global:{ntype.lower()}:{_slug(stable)}"

    fallback = (
        node.get("id")
        or f"{ntype}-{uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(node, sort_keys=True)).hex[:16]}"
    )
    return f"urn:mas:node:{ntype.lower()}:{_slug(fallback)}"


def _edge_canonical_id(edge: Dict[str, Any], node_by_local_id: Dict[str, Dict[str, Any]]) -> str:
    """Build a globally unique canonicalId for an edge."""
    src = str(edge.get("from_id") or "")
    dst = str(edge.get("to_id") or "")
    etype = str(edge.get("edge_type") or "edge")

    session_id = ""
    src_node = node_by_local_id.get(src)
    dst_node = node_by_local_id.get(dst)
    if src_node and src_node.get("sessionId"):
        session_id = str(src_node["sessionId"])
    elif dst_node and dst_node.get("sessionId"):
        session_id = str(dst_node["sessionId"])
    else:
        run_id = (src_node or {}).get("runId") or (dst_node or {}).get("runId")
        session_id = f"session-{run_id}" if run_id else "session-unknown"

    digest = hashlib.sha1(f"{etype}|{src}|{dst}".encode("utf-8", errors="replace")).hexdigest()[:16]
    return f"urn:mas:session:{session_id}:edge:{etype}:{digest}"


# ---------------------------------------------------------------------------
# URN helpers — identity scope conventions
# ---------------------------------------------------------------------------
#
# Identity tiers (from broad to narrow):
#
#   global   urn:mas:global:agent:{name}:{version}
#            Agent types publicly advertised; stable across orgs.
#            Use when the agent definition is published/versioned.
#
#   org      urn:mas:org:{org_id}:agent:{name}
#            Agent deployed in an organisation but not publicly registered.
#
#   mas      urn:mas:mas:{mas_id}:{kind}:{local_id}
#            Structural elements specific to a MAS deployment (tool definitions,
#            internal routing agents, etc.).  Stable across sessions.
#
#   session  urn:mas:session:{session_id}:{kind}:{local_id}
#            Ephemeral nodes scoped to a single trajectory: calls, states,
#            transitions.  Two identical state *contents* in two sessions
#            get DIFFERENT session-scoped URNs but the SAME content_hash.
#
# Neo4j currently stores the local_id part (call_id / state_node_id);
# full URNs are used for RDF/SHACL serialisation in validate_kg.


def _urn_session(session_id: str, kind: str, local_id: str) -> str:
    """Build a session-scoped URN: ``urn:mas:session:{sid}:{kind}:{lid}``."""
    return f"urn:mas:session:{session_id}:{kind}:{local_id}"


def _urn_agent(agent_id: str, org: str = "local") -> str:
    """Build an agent-scoped URN used for RDF serialisation.

    ``org='local'`` until a governance registry exists; replace with the
    actual org identifier when agents are publicly advertised.
    """
    return f"urn:mas:agent:{org}:{agent_id}"


# Per-class attribute formatting/enrichment (_format_messages,
# _extract_completion, _enrich_call_node, ...) now lives event-by-event in
# observability/native/handlers/*.py, one handler module per call family —
# see core/native_graph_builder.py for the dispatch core that calls them.


def extract_graph(
    normalized: List[Dict[str, Any]],
    session_id_override: Optional[str] = None,
    *,
    split_session_id: bool = True,
    app_name_override: Optional[str] = None,
    all_attributes: bool = False,
    categories: Any = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Build nodes and edges from a normalized event list.

    Drives ``core.native_graph_builder``: a ``process_event`` dispatch over
    per-event-kind handlers in ``observability/native/handlers/*.py``.

    Returns (nodes, edges). The graph is a plain mapping of each event onto
    ontology nodes and edges; see ``core/native_graph_builder.py`` for the
    node and edge families.
    """
    # Deferred import: native_graph_builder imports several names straight
    # from this module, so importing it at module level here would be a
    # circular import at load time; by the time this function actually
    # runs, both modules are already fully loaded.
    from mas.library.kg.core.native_graph_builder import build_graph_from_normalized

    return build_graph_from_normalized(
        normalized,
        session_id_override=session_id_override,
        split_session_id=split_session_id,
        app_name_override=app_name_override,
        all_attributes=all_attributes,
        categories=categories,
    )


def _dry_run_dump(nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]]) -> None:
    """Dump nodes and edges to stdout in a human-readable format."""
    print(f"\n── NODES ({len(nodes)}) ─────────────────────────────────────")
    for node in nodes:
        node_type = node.get("node_type", "?")
        attrs = {k: v for k, v in node.items() if k != "node_type" and v is not None}
        attrs_str = "  ".join(f"{k}={json.dumps(v)[:60]}" for k, v in attrs.items())
        print(f"  (:{node_type})  {attrs_str}")

    print(f"\n── EDGES ({len(edges)}) ─────────────────────────────────────")
    for edge in edges:
        et = edge.get("edge_type", "?")
        from_id = edge.get("from_id", "?")
        to_id = edge.get("to_id", "?")
        extra = {
            k: v
            for k, v in edge.items()
            if k not in ("edge_type", "from_id", "from_type", "to_id", "to_type")
        }
        extra_str = "  ".join(f"{k}={v}" for k, v in extra.items())
        print(f"  ({from_id}) -[:{et}]-> ({to_id})  {extra_str}")
    print()


# ---------------------------------------------------------------------------
# Pipeline step
# ---------------------------------------------------------------------------
