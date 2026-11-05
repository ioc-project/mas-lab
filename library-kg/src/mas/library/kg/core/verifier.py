"""Structural and SHACL checks for a Knowledge Graph, used by
``steps/validate_kg.py``'s ``run_validate_kg``.

Graph-shape invariants — containment chain, state nesting, the
ProcessingCall gate, temporal enclosure, Session↔Run connectivity, and
Session.appName — live in SHACL (``mas-shapes.ttl``, run via
:func:`run_shacl_validation`) rather than as hand-rolled Python, so there is
a single source of truth for what a valid KG looks like.

Structural checks in this module
---------------------------------
1. :func:`check_unknown_node_types` — every node_type must be declared in
   :data:`KNOWN_NODE_TYPES` (derived from the MAS ontology class hierarchy).
2. :func:`check_unknown_edge_types` — every edge_type must be declared in
   :data:`KNOWN_EDGE_TYPES`.
3. :func:`check_block_vocabulary` — every node carrying a ``block`` attribute
   must use ``structural | execution | trajectory``, matching the expected
   category for its node_type (e.g. State/Transition → trajectory).
4. :func:`check_layer_vocabulary` — ``layer`` may only be set on
   State/Transition nodes with value ``"normalized"``.
5. :func:`check_experiment_extension` — optional experiment/scenario/
   testItem/runLabel extension attributes on Session, when that layer is
   enabled.
6. :func:`run_shacl_validation` — runs ``mas-shapes.ttl`` against the KG via
   pyshacl; raises :class:`KGCheckSkipped` if pyshacl/rdflib aren't
   installed, so a missing optional dependency is never mistaken for a pass.
"""

from __future__ import annotations

import json
import logging
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from mas.library.kg._dist import extra_hint
from mas.library.kg.core.oxp_models import mas_namespace

# ---------------------------------------------------------------------------
# Ontology index — derives all validation constants from mas-ontology.ttl via
# the SCOPE scope_ontology package.  No intermediate JSON schema file required.
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)


class _OntologyIndex:
    """Loads all validation constants from a set of ontology TTL files.

    TTL sources loaded in order into one combined rdflib graph:

    1. ``mas-ontology.ttl`` from ``scope_ontology`` (SCOPE)
    2. MAS-Lab native-path extension TTLs (RAGQuery, MemoryCall, SkillCall,
       governance, …) from ``library-kg/ontology/extensions/``

    All resolved by ``mas.library.kg.ontology``. Missing extensions are
    skipped with a warning, not fatal.

    Every constant is derived from the combined graph. Nothing is hardcoded.

    Recognised namespaces (used to filter triples to the MAS vocabulary):

    * ``mas:``    SCOPE ``https://ioc-project.github.io/scope-ontology/mas#``
    * prior OXP URI kept so older graphs still index
    """

    # Recognised MAS namespaces — triples outside these are ignored.
    _NS: List[str] = [
        "https://ioc-project.github.io/scope-ontology/mas#",
        "https://ioc-project.github.io/scope-ontology/mas-lab#",
        "https://ioc-project.github.io/scope-ontology/mas-kg#",
        "https://outshift-open.github.io/oxp-ontology/mas#",
        "https://outshift-open.github.io/oxp-ontology/mas-lab#",
        "https://outshift-open.github.io/oxp-ontology/mas-kg#",
    ]

    def __init__(self) -> None:
        self.known_node_types: frozenset[str] = frozenset()
        self.known_edge_types: frozenset[str] = frozenset()
        self.known_attrs_lower: Dict[str, str] = {}
        self.trajectory_types: frozenset[str] = frozenset()
        self.structural_types: frozenset[str] = frozenset()
        self.parent_classes: Dict[str, List[str]] = {}
        self.valid_block_values: frozenset[str] = frozenset()
        self.loaded: bool = False  # True iff all TTLs parsed successfully
        self._load()

    def _load(self) -> None:
        try:
            import rdflib  # type: ignore

            from mas.library.kg.ontology import resolve_kg_ontology_paths

            ttl_files: List[Path] = resolve_kg_ontology_paths()
        except ImportError as exc:  # pragma: no cover
            logger.warning(
                (
                    "scope_ontology not available — _OntologyIndex empty "
                    "(validation will block ingest): %s"
                ),
                exc,
            )
            return

        OWL = rdflib.namespace.OWL
        RDFS = rdflib.namespace.RDFS
        RDF = rdflib.RDF
        MASLAB_NS = "https://ioc-project.github.io/scope-ontology/mas-lab#"
        MAS_NS = mas_namespace()

        if not ttl_files:
            logger.warning("_OntologyIndex: no ontology TTLs resolved — ingest will be blocked.")
            return

        # ── Load into one combined graph ───────────────────────────────────
        g = rdflib.Graph()
        loaded_count = 0
        for ttl in ttl_files:
            try:
                before = len(g)
                g.parse(str(ttl), format="turtle")
                logger.debug("Loaded TTL: %s (+%d triples)", ttl.name, len(g) - before)
                loaded_count += 1
            except Exception as exc:
                logger.warning("Could not parse %s: %s", ttl, exc)

        if loaded_count == 0:
            logger.warning("_OntologyIndex: no TTL could be loaded — ingest will be blocked.")
            return

        # ── Helpers ────────────────────────────────────────────────────────
        def _in_ns(iri: str) -> bool:
            return any(iri.startswith(ns) for ns in self._NS)

        def _local(iri: str) -> str:
            return iri.split("#")[-1].split("/")[-1]

        def _local_names(rdf_type: Any) -> frozenset[str]:
            return frozenset(
                _local(str(s))
                for s, _, _ in g.triples((None, RDF.type, rdf_type))
                if _in_ns(str(s))
            )

        def _subclasses_of(parent_uri: Any) -> frozenset[str]:
            """Transitive subclasses of parent_uri (BFS, excluding parent itself)."""
            visited: set = set()
            queue: list = [s for s, _, _ in g.triples((None, RDFS.subClassOf, parent_uri))]
            while queue:
                cls = queue.pop()
                if cls in visited:
                    continue
                visited.add(cls)
                queue.extend(
                    s for s, _, _ in g.triples((None, RDFS.subClassOf, cls)) if s not in visited
                )
            return frozenset(_local(str(c)) for c in visited if _in_ns(str(c)))

        # ── Populate index ─────────────────────────────────────────────────
        self.known_node_types = _local_names(OWL.Class)
        self.known_edge_types = _local_names(OWL.ObjectProperty)
        dt_names = list(_local_names(OWL.DatatypeProperty))
        self.known_attrs_lower = {n.lower(): n for n in dt_names}

        # Block vocabulary: instances of maslab:BlockValue when that TTL is
        # present. SCOPE may not ship it; keep the documented
        # instance vocabulary so catalog nodes with block=structural still pass.
        MASLAB = rdflib.Namespace(MASLAB_NS)
        self.valid_block_values = frozenset(
            _local(str(s))
            for s, _, _ in g.triples((None, RDF.type, MASLAB.BlockValue))
            if str(s).startswith(MASLAB_NS)
        ) or frozenset({"structural", "execution", "trajectory", "governance"})

        # Subclass hierarchies (transitive BFS on combined graph)
        MAS = rdflib.Namespace(MAS_NS)
        self.trajectory_types = _subclasses_of(MAS.TrajectoryElement)
        self.structural_types = _subclasses_of(MAS.StructuralElement)

        # Direct rdfs:subClassOf → parent_classes map
        parent_map: Dict[str, List[str]] = {}
        for cls, _, parent in g.triples((None, RDFS.subClassOf, None)):
            if not _in_ns(str(cls)) or not _in_ns(str(parent)):
                continue
            parent_map.setdefault(_local(str(cls)), []).append(_local(str(parent)))
        self.parent_classes = parent_map

        logger.debug(
            "_OntologyIndex: %d node types, %d edge types, %d attrs, "
            "%d block values, %d TTLs loaded",
            len(self.known_node_types),
            len(self.known_edge_types),
            len(self.known_attrs_lower),
            len(self.valid_block_values),
            loaded_count,
        )
        self.loaded = loaded_count > 0


_ONT = _OntologyIndex()

# ---------------------------------------------------------------------------
# Invariant checkers
# ---------------------------------------------------------------------------

CheckResult = Tuple[bool, List[Any]]  # (passed, list_of_violation_dicts)


@dataclass
class _ValidationMessage:
    severity: str
    message: str


# Node/edge type sets derived from the combined ontology graph via _ONT.
KNOWN_NODE_TYPES: frozenset[str] = _ONT.known_node_types
KNOWN_EDGE_TYPES: frozenset[str] = _ONT.known_edge_types

# ---------------------------------------------------------------------------
# Vocabulary constants — all derived from TTL, nothing hardcoded.
#
# _VALID_BLOCK_VALUES : maslab:BlockValue instances in experiment-orchestration.ttl §4
# _STRUCTURAL_NODE_TYPES : rdfs:subClassOf* mas:StructuralElement (combined graph)
# _TRAJECTORY_NODE_TYPES : rdfs:subClassOf* mas:TrajectoryElement (combined graph)
# ---------------------------------------------------------------------------

# Valid maslab:block values (experiment-orchestration.ttl §4 — maslab:BlockValue enum).
_VALID_BLOCK_VALUES: frozenset[str] = _ONT.valid_block_values

# Node types whose expected block value is 'structural' (StructuralElement subclasses).
_STRUCTURAL_NODE_TYPES: frozenset[str] = _ONT.structural_types

# Node types whose expected block value is 'trajectory' (TrajectoryElement subclasses).
_TRAJECTORY_NODE_TYPES: frozenset[str] = _ONT.trajectory_types

# Only these node types may carry a 'layer' attribute (must be 'normalized').
_LAYER_ALLOWED_TYPES: frozenset[str] = _ONT.trajectory_types


def check_unknown_node_types(nodes: List[Dict], edges: List[Dict]) -> CheckResult:
    """Every node must have a node_type that is declared in the MAS ontology.

    Unknown types indicate a normalisation bug or schema drift — surfaced as
    violations so they are caught before Neo4j ingestion.
    """
    violations: List[Any] = []
    seen_unknown: set[str] = set()
    for n in nodes:
        ntype = n.get("node_type", "")
        if not ntype:
            nid = n.get("callId") or n.get("sessionId") or n.get("stateNodeId") or "?"
            violations.append(
                {
                    "sub": "node",
                    "status": "missing",
                    "attr": "node_type",
                    "node_id": nid,
                }
            )
        elif ntype not in KNOWN_NODE_TYPES and ntype not in seen_unknown:
            seen_unknown.add(ntype)
            violations.append(
                {
                    "sub": "node",
                    "status": "spurious",
                    "attr": "node_type",
                    "cur": ntype,
                }
            )
    return len(violations) == 0, violations


def check_unknown_edge_types(nodes: List[Dict], edges: List[Dict]) -> CheckResult:
    """Every edge must have an edge_type that is declared in the MAS ontology.

    Unknown edge types indicate a normalisation bug or schema drift.
    """
    violations: List[Any] = []
    seen_unknown: set[str] = set()
    for e in edges:
        etype = e.get("edge_type", "")
        if not etype:
            violations.append(
                {
                    "sub": "edge",
                    "status": "missing",
                    "attr": "edge_type",
                    "from_id": e.get("from_id", "?"),
                    "to_id": e.get("to_id", "?"),
                }
            )
        elif etype not in KNOWN_EDGE_TYPES and etype not in seen_unknown:
            seen_unknown.add(etype)
            violations.append(
                {
                    "sub": "edge",
                    "status": "spurious",
                    "attr": "edge_type",
                    "cur": etype,
                }
            )
    return len(violations) == 0, violations


def check_block_vocabulary(nodes: List[Dict], edges: List[Dict]) -> CheckResult:
    """Every node that carries a ``block`` attribute must use a value from the
    ontology vocabulary (structural | execution | trajectory) and the value
    must match the expected category for its node_type.

    Expected mapping (from experiment-orchestration.ttl)
    -----------------------------------------------------
    structural  — Agent, CatalogTool, CatalogModel, CatalogSkill
    trajectory  — State, Transition  (TrajectoryElement subclasses)
    execution   — everything else (Session, *Call, CallAnnotation …)
    """
    violations: List[Any] = []
    for n in nodes:
        block = n.get("block")
        if block is None:
            continue

        ntype = n.get("node_type", "Unknown")
        nid = (
            n.get("callId")
            or n.get("stateNodeId")
            or n.get("transitionId")
            or n.get("sessionId")
            or "?"
        )

        # (a) value must be in the allowed vocabulary
        if block not in _VALID_BLOCK_VALUES:
            violations.append(
                {
                    "sub": "node_attr",
                    "node_type": ntype,
                    "node_id": nid,
                    "attr": "block",
                    "status": "invalid",
                    "cur": block,
                    "exp": " | ".join(sorted(_VALID_BLOCK_VALUES)),
                }
            )
            continue  # skip consistency check if vocabulary is already wrong

        # (b) value must match the expected category for this node_type
        if ntype in _STRUCTURAL_NODE_TYPES:
            expected = "structural"
        elif ntype in _TRAJECTORY_NODE_TYPES:
            expected = "trajectory"
        else:
            expected = "execution"

        # Governance CallAnnotations use block=governance (see experiment-orchestration.ttl §4).
        if ntype == "CallAnnotation" and block == "governance":
            kind = str(n.get("annotationKind") or "")
            if kind.startswith("governance") or kind in {
                "skills_check",
                "governance_denied",
                "governance_checked",
            }:
                continue

        if block != expected:
            violations.append(
                {
                    "sub": "node_attr",
                    "node_type": ntype,
                    "node_id": nid,
                    "attr": "block",
                    "status": "invalid",
                    "cur": block,
                    "exp": expected,
                }
            )

    return len(violations) == 0, violations


def check_layer_vocabulary(nodes: List[Dict], edges: List[Dict]) -> CheckResult:
    """``layer`` may only appear on State and Transition nodes (TrajectoryElement
    subclasses) with value ``"normalized"``.

    Any other node type carrying ``layer`` (e.g. CallAnnotation, AgentCall) is
    a normalisation bug — CallAnnotation is an ExecutionElement and must never
    receive ``layer``.
    """
    violations: List[str] = []
    for n in nodes:
        layer = n.get("layer")
        if layer is None:
            continue  # absence is expected for non-trajectory nodes

        ntype = n.get("node_type", "Unknown")
        nid = (
            n.get("callId")
            or n.get("stateNodeId")
            or n.get("transitionId")
            or n.get("sessionId")
            or "?"
        )

        if ntype not in _LAYER_ALLOWED_TYPES:
            violations.append(
                {
                    "sub": "node_attr",
                    "node_type": ntype,
                    "node_id": nid,
                    "attr": "layer",
                    "status": "spurious",
                    "cur": layer,
                    "exp": "absent (only State/Transition)",
                }
            )
        elif layer != "normalized":
            violations.append(
                {
                    "sub": "node_attr",
                    "node_type": ntype,
                    "node_id": nid,
                    "attr": "layer",
                    "status": "invalid",
                    "cur": layer,
                    "exp": "normalized",
                }
            )

    return len(violations) == 0, violations


def check_orphaned_edges(nodes: List[Dict], edges: List[Dict]) -> CheckResult:
    """Check for edges whose endpoints are not present in the node list.

    This is a critical structural invariant: in a normal KG push, every edge
    must reference nodes that exist in the same batch. Orphaned edges indicate
    one of two upstream bugs:

    1. **Node creation failure**: A node should exist but was filtered out
       (e.g., missing 'id' field, validation failure, denormalization bug).
    
    2. **Edge creation bug**: An edge was created with invalid from_id/to_id
       that doesn't match any real node.

    Annotation cross-references are legitimate (e.g., Metric nodes referencing
    existing Session nodes not in the current batch) but should be pushed via
    ``push_annotations_to_neo4j`` which documents that behavior.  This check
    helps catch unintentional orphaned edges in normal KG building.

    NOTE: This check does NOT run for annotation pushes where dangling
    references are expected.
    """
    node_ids = {str(n.get("id", "")) for n in nodes if n.get("id")}
    violations: List[Any] = []

    for e in edges:
        from_id = str(e.get("from_id") or e.get("source") or "")
        to_id = str(e.get("to_id") or e.get("target") or "")
        edge_type = e.get("edge_type", "?")

        if from_id and from_id not in node_ids:
            violations.append(
                {
                    "sub": "edge",
                    "status": "orphaned_from",
                    "edge_type": edge_type,
                    "from_id": from_id,
                    "to_id": to_id,
                    "msg": f"from_id '{from_id}' not in node list",
                }
            )

        if to_id and to_id not in node_ids:
            violations.append(
                {
                    "sub": "edge",
                    "status": "orphaned_to",
                    "edge_type": edge_type,
                    "from_id": from_id,
                    "to_id": to_id,
                    "msg": f"to_id '{to_id}' not in node list",
                }
            )

    return len(violations) == 0, violations


# ---------------------------------------------------------------------------
# Optional SHACL validation
# ---------------------------------------------------------------------------


class KGCheckSkipped(Exception):
    """Raised when a check cannot run at all in this environment.

    This is distinct from both "pass" (ran, found nothing) and "error" (ran,
    found a problem, or crashed unexpectedly): the check never executed, so
    it must never be reported the same way a real pass would be. Previously
    ``run_shacl_validation`` returned ``(True, [], [warning])`` when pyshacl/
    rdflib were missing -- ``True`` is indistinguishable from an actual clean
    validation run in any caller that only checks ``error_count``/``passed``,
    which is exactly how this whole SHACL path went unnoticed for months
    (see the "shacl" ``KGCheckSkipped`` handling in ``run_validate_kg``).
    """


def run_shacl_validation(
    nodes: List[Dict],
    edges: List[Dict],
    ontology_path: Path,
    run_id: str,
    *,
    strict: bool = False,
) -> Tuple[bool, List[Any], List[Any]]:
    """Run pyshacl against a JSON-LD representation of the KG.

    Uses ``mas-ontology.ttl`` + ``mas-shapes.ttl`` + ``mas-shapes-custom.ttl``
    from SCOPE as the SHACL shapes graph. ``mas-shapes.ttl`` only emits
    bare ``sh:property`` references to inline PropertyShapes on the ontology
    TTLs, so those files must be in the same graph.

    Returns ``(passed, violations, warnings)`` where SHACL warnings remain
    warnings unless ``strict=True``.

    Raises:
        KGCheckSkipped: if pyshacl or rdflib are not installed. The caller
            must surface this as a distinct "skipped" outcome, not a pass.
    """
    try:
        import pyshacl  # type: ignore
        import rdflib
    except ImportError as exc:
        raise KGCheckSkipped(
            f"shacl check requires pyshacl and rdflib ({extra_hint('validation')})"
        ) from exc

    MAS = mas_namespace()
    MASKG = "https://ioc-project.github.io/scope-ontology/mas-kg#"
    MASLAB = "https://ioc-project.github.io/scope-ontology/mas-lab#"

    # Resolve SHACL shapes from SCOPE. mas-shapes.ttl only emits
    # bare sh:property references to PropertyShapes declared on the
    # ontology TTLs themselves, so the full ontology set must be in the
    # same graph (see scope_ontology.verification._shapes_graph).
    from mas.library.kg.ontology import (
        resolve_all_ontology_paths,
        resolve_mas_shapes_custom_path,
        resolve_mas_shapes_path,
    )

    try:
        shapes_files = [
            path
            for path in (
                *resolve_all_ontology_paths(),
                resolve_mas_shapes_path(),
                resolve_mas_shapes_custom_path(),
            )
            if path is not None and path.exists()
        ]
    except ImportError:
        shapes_files = []
    if not shapes_files:
        if ontology_path is None:
            raise KGCheckSkipped(
                "shacl check requires ioc-scope-ontology to resolve mas-shapes.ttl "
                f"({extra_hint('validation')})"
            )
        sibling_shapes = ontology_path.parent / "mas-shapes.ttl"
        shapes_files = [sibling_shapes if sibling_shapes.exists() else ontology_path]

    # Build JSON-LD graph from KG nodes
    # Maps camelCase node dict keys → MAS camelCase property IRIs
    _PROP_MAP = [
        ("callId", "callId"),
        ("parentCallId", "parentCallId"),
        ("executionId", "executionId"),
        ("sourceRecordIds", "sourceRecordIds"),
        ("status", "status"),
        ("sessionId", "sessionId"),
        ("runId", "runId"),
        ("agentId", "agentId"),
        ("appName", "appName"),
        ("agentName", "agentName"),
        ("masName", "masName"),
        ("llmName", "llmName"),
        ("inputContent", "inputContent"),
        ("outputContent", "outputContent"),
        ("prompt", "prompt"),
        ("modelName", "modelName"),
        ("provider", "provider"),
        ("promptTokenCount", "promptTokenCount"),
        ("completionTokenCount", "completionTokenCount"),
        ("totalTokenCount", "totalTokenCount"),
        ("cacheReadTokenCount", "cacheReadTokenCount"),
        ("finishReason", "finishReason"),
        ("responseId", "responseId"),
        ("temperature", "temperature"),
        ("canonicalId", "canonicalId"),
        ("completion", "completion"),
        ("toolName", "toolName"),
        ("toolArguments", "toolArguments"),
        ("processingName", "processingName"),
        ("processingType", "processingType"),
        ("id", "id"),
        ("name", "name"),
        ("duration", "duration"),
        ("declared", "declared"),
        ("description", "description"),
        # State identity/content
        ("stateNodeId", "stateNodeId"),
        ("contentHash", "contentHash"),
        ("content", "content"),
        ("semanticType", "semanticType"),
        # Transition
        ("transitionId", "transitionId"),
        ("edgeType", "edgeType"),
        ("transitionTimestamp", "transitionTimestamp"),
        # Timing (decimal)
        ("startTime", "startTime"),
        ("endTime", "endTime"),
    ]
    _DECIMAL_PROPS = {"startTime", "endTime", "transitionTimestamp", "temperature"}
    _DOUBLE_PROPS = {"duration"}
    _INTEGER_PROPS = {
        "promptTokenCount",
        "completionTokenCount",
        "totalTokenCount",
        "cacheReadTokenCount",
    }
    _BOOLEAN_PROPS = {"declared"}

    ld_nodes = []
    node_iri_by_local_id: Dict[str, str] = {}
    entry_by_local_id: Dict[str, Dict[str, Any]] = {}
    for n in nodes:
        ntype = n.get("node_type")
        if not ntype:
            continue

        type_ns = MASKG if ntype == "Run" else MAS
        entry: Dict[str, Any] = {
            "@type": f"{type_ns}{ntype}",
        }
        local_id: Optional[str] = None
        sess = n.get("sessionId") or f"session-{run_id}"

        # Dispatch on node_type first. A call/state with sessionId must not
        # reuse urn:mas:session:{sessionId} (that IRI is the Session node).
        if ntype == "State" and n.get("stateNodeId"):
            local_id = str(n["stateNodeId"])
            entry["@id"] = f"urn:mas:session:{sess}:state:{local_id}"
        elif ntype == "Transition" and n.get("transitionId"):
            local_id = str(n["transitionId"])
            entry["@id"] = f"urn:mas:session:{sess}:transition:{local_id}"
        elif ntype == "Run" and n.get("runId"):
            local_id = str(n["runId"])
            entry["@id"] = f"urn:mas:run:{local_id}"
        elif ntype == "Session" and n.get("sessionId"):
            local_id = str(n["sessionId"])
            entry["@id"] = f"urn:mas:session:{local_id}"
        elif ntype == "Agent" and (n.get("agentId") or n.get("id") or n.get("name")):
            local_id = str(n.get("agentId") or n.get("id") or n.get("name"))
            entry["@id"] = f"urn:mas:agent:{local_id}"
        elif ntype in {"Tool", "LLM", "Skill", "Processing", "MAS", "Capability"}:
            name = n.get("name") or n.get("id") or ntype
            slug = "".join(ch if ch.isalnum() or ch in "-._" else "_" for ch in str(name).strip()) or ntype.lower()  # noqa: E501
            local_id = str(n.get("id") or slug)
            entry["@id"] = f"urn:mas:catalog:{ntype.lower()}:{slug}"
        elif n.get("callId"):
            local_id = str(n["callId"])
            entry["@id"] = f"urn:mas:session:{sess}:call:{local_id}"
        elif n.get("id"):
            local_id = str(n["id"])
            entry["@id"] = f"urn:mas:session:{sess}:{ntype.lower()}:{local_id}"
        else:
            continue

        # Call nodes often reuse agentId/name ("moderator"). Those keys
        # must not steal the Agent/MAS/Tool catalog entry SHACL focuses on.
        existing_primary = entry_by_local_id.get(local_id)
        if existing_primary is None or ntype in {
            "Agent",
            "MAS",
            "LLM",
            "Tool",
            "Processing",
            "Skill",
            "Capability",
            "Session",
            "Run",
        }:
            node_iri_by_local_id[local_id] = entry["@id"]
            entry_by_local_id[local_id] = entry
        # Call nodes often reuse agentId/name ("moderator"). Those aliases
        # must not steal the Agent/MAS/Tool catalog entry SHACL focuses on.
        catalog_types = {
            "Agent",
            "MAS",
            "LLM",
            "Tool",
            "Processing",
            "Skill",
            "Capability",
            "Session",
            "Run",
        }
        aliases = [n.get("id"), n.get("callId"), n.get("canonicalId")]
        if ntype in catalog_types:
            aliases.extend(
                (
                    n.get("agentId"),
                    n.get("annotationId"),
                    n.get("name"),
                    n.get("runId") if ntype == "Run" else None,
                    n.get("sessionId") if ntype == "Session" else None,
                )
            )
        for alias in aliases:
            if not alias:
                continue
            key = str(alias)
            existing = entry_by_local_id.get(key)
            if existing is not None:
                if ntype not in catalog_types:
                    continue
                # An LLM/Tool named after an agent must not steal the Agent IRI.
                if ntype not in {"Agent", "MAS", "Session", "Run"}:
                    continue
            node_iri_by_local_id[key] = entry["@id"]
            entry_by_local_id[key] = entry

        for key, prop in _PROP_MAP:
            val = n.get(key)
            if val is None:
                continue
            iri_ns = MASLAB if prop in {"appName"} else MAS
            iri = f"{iri_ns}{prop}"
            if prop in _DECIMAL_PROPS:
                try:
                    entry[iri] = {"@value": str(float(val)), "@type": "xsd:decimal"}
                except (TypeError, ValueError):
                    pass
            elif prop in _DOUBLE_PROPS:
                try:
                    entry[iri] = {"@value": str(float(val)), "@type": "xsd:double"}
                except (TypeError, ValueError):
                    pass
            elif prop in _BOOLEAN_PROPS:
                entry[iri] = {"@value": "true" if bool(val) else "false", "@type": "xsd:boolean"}
            elif prop in _INTEGER_PROPS:
                try:
                    entry[iri] = {"@value": str(int(val)), "@type": "xsd:integer"}
                except (TypeError, ValueError):
                    pass
            else:
                entry[iri] = {"@value": str(val), "@type": "xsd:string"}

        ld_nodes.append(entry)

    # Materialise ontology object properties from graph edges so SHACL sees
    # Session state anchors, transition endpoints, and trajectory links in
    # the RDF view built for validation.
    # Project every known object property (plus native aliases) so SHACL
    # sees containment / executes / trajectory links on the focus node.
    edge_to_obj_prop = {name: f"{MAS}{name}" for name in KNOWN_EDGE_TYPES}
    edge_to_obj_prop.update(
        {
            "contains": f"{MASKG}contains",
            "hasCall": f"{MASKG}hasCall",
            "callsAgent": f"{MAS}hasAgentCall",
            "ofLLMType": f"{MAS}executesLLM",
            "ofToolType": f"{MAS}executesTool",
            "invokesProcessing": f"{MAS}executesProcessing",
            "invokesSkill": f"{MAS}executesProcessing",
            "fromState": f"{MAS}fromState",
            "toState": f"{MAS}toState",
            "realizes": f"{MAS}representsExecution",
            "annotates": f"{MAS}annotates",
            "contributesTo": f"{MAS}contributesTo",
        }
    )
    for e in edges:
        prop_iri = edge_to_obj_prop.get(str(e.get("edge_type", "")))
        if not prop_iri:
            continue
        src_local = str(e.get("from_id") or "")
        dst_local = str(e.get("to_id") or "")
        src_entry = entry_by_local_id.get(src_local)
        dst_iri = node_iri_by_local_id.get(dst_local)
        if not src_entry or not dst_iri:
            continue

        existing = src_entry.get(prop_iri)
        obj_ref = {"@id": dst_iri}
        if existing is None:
            src_entry[prop_iri] = obj_ref
        elif isinstance(existing, list):
            if obj_ref not in existing:
                existing.append(obj_ref)
        elif existing != obj_ref:
            src_entry[prop_iri] = [existing, obj_ref]

    doc = {
        "@context": {
            "mas": MAS,
            "maskg": MASKG,
            "xsd": "http://www.w3.org/2001/XMLSchema#",
            "owl": "http://www.w3.org/2002/07/owl#",
        },
        "@graph": ld_nodes,
    }

    data_graph = rdflib.Graph()
    # rdflib JSON-LD parser currently emits a ConjunctiveGraph deprecation warning
    # internally; suppress that third-party warning at this call site.
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=r"ConjunctiveGraph is deprecated, use Dataset instead.*",
            category=DeprecationWarning,
        )
        data_graph.parse(data=json.dumps(doc), format="json-ld")

    shacl_graph = rdflib.Graph()
    for shapes_file in shapes_files:
        shacl_graph.parse(str(shapes_file), format="turtle")

    SH = rdflib.Namespace("http://www.w3.org/ns/shacl#")

    conforms, report_graph, report_text = pyshacl.validate(
        data_graph,
        shacl_graph=shacl_graph,
        inference="rdfs",
        abort_on_first=False,
    )
    violations: List[Any] = []
    warning_items: List[Any] = []

    for result in report_graph.subjects(rdflib.RDF.type, SH.ValidationResult):
        severity_node = report_graph.value(result, SH.resultSeverity)
        focus_node = report_graph.value(result, SH.focusNode)
        result_path = report_graph.value(result, SH.resultPath)
        result_message = report_graph.value(result, SH.resultMessage)
        source_shape = report_graph.value(result, SH.sourceShape)

        severity_text = (
            str(severity_node).rsplit("#", 1)[-1].lower() if severity_node else "violation"
        )
        status = "warning" if severity_text == "warning" and not strict else "error"

        detail_parts = []
        if result_message:
            detail_parts.append(str(result_message))
        if focus_node:
            detail_parts.append(f"focus={focus_node}")
        if result_path:
            detail_parts.append(f"path={result_path}")
        if source_shape:
            detail_parts.append(f"shape={source_shape}")
        detail = " | ".join(detail_parts) or (
            report_text[:4000] if report_text else "SHACL validation failed"
        )

        item = _ValidationMessage(status, detail)
        if status == "warning":
            warning_items.append(item)
        else:
            violations.append(item)

    passed = not violations
    return passed, violations, warning_items


# ---------------------------------------------------------------------------
# Pipeline step
# ---------------------------------------------------------------------------


def check_experiment_extension(nodes: List[Dict], edges: List[Dict]) -> CheckResult:
    """Validate the optional experiment extension on Session nodes.

    For hierarchical session IDs with at least 5 segments
    ``{appName}/{experiment}/{scenario}/{testItem}/{runLabel}``, ensure the
    corresponding scalar fields are present and consistent on Session.
    """
    del edges
    violations: List[Any] = []
    required_fields = ("experiment", "scenario", "testItem", "runLabel")

    for n in nodes:
        if n.get("node_type") != "Session":
            continue
        sid = str(n.get("sessionId") or n.get("id") or "").strip()
        if not sid or "/" not in sid:
            continue
        parts = [p for p in sid.split("/") if p]
        if len(parts) < 5:
            continue

        expected = {
            "experiment": parts[1],
            "scenario": parts[2],
            "testItem": parts[3],
            "runLabel": parts[4],
        }
        node_id = n.get("sessionId") or n.get("id") or "unknown"
        for field in required_fields:
            actual = str(n.get(field) or "").strip()
            if not actual:
                violations.append(
                    {
                        "sub": "node_attr",
                        "node_type": "Session",
                        "node_id": node_id,
                        "attr": field,
                        "status": "missing",
                        "exp": "mandatory",
                    }
                )
            elif actual != expected[field]:
                violations.append(
                    {
                        "sub": "node_attr",
                        "node_type": "Session",
                        "node_id": node_id,
                        "attr": field,
                        "status": "mismatch",
                        "cur": actual,
                        "exp": expected[field],
                    }
                )

    return len(violations) == 0, violations
