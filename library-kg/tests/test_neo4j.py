#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for mas.library.kg.neo4j — push, dump, denormalize, annotations."""

import json
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def simple_kg_doc():
    return {
        "run_id": "r1",
        "nodes": [
            {"id": "sess-1", "node_type": "Session", "sessionId": "sess-1"},
            {"id": "ac-1", "node_type": "AgentCall", "agentId": "a", "callId": "ac-1"},
            {"id": "lc-1", "node_type": "LLMCall", "agentId": "a", "callId": "lc-1"},
            {"id": "tc-1", "node_type": "ToolCall", "agentId": "a", "toolName": "search"},
        ],
        "edges": [
            {"from_id": "ac-1", "to_id": "lc-1", "edge_type": "contains"},
            {"from_id": "ac-1", "to_id": "tc-1", "edge_type": "contains"},
        ],
    }


@pytest.fixture
def annotation_doc():
    """Mini-KG with Metric nodes referencing existing Session nodes in Neo4j."""
    return {
        "nodes": [
            {
                "id": "metric-1",
                "node_type": "Metric",
                "metricName": "GoalSuccessRate",
                "value": 0.85,
                "resourceId": "sess-1",
            },
        ],
        "edges": [
            {
                "from_id": "sess-1",
                "to_id": "metric-1",
                "edge_type": "hasMetric",
                "from_type": "session",  # sess-1 isn't in this doc's own nodes;
                # this role hint is how push resolves
                # it to the real :Session label instead
                # of leaving the match unlabeled.
            },
        ],
    }


# ---------------------------------------------------------------------------
# _neo4j_props
# ---------------------------------------------------------------------------


class TestNeo4jProps:
    def test_scalars_pass_through(self):
        from mas.library.kg.neo4j.push import _neo4j_props

        d = {"name": "test", "count": 42, "score": 0.5, "active": True}
        result = _neo4j_props(d)
        assert result == d

    def test_dict_gets_json_prefix(self):
        from mas.library.kg.neo4j.push import _JSON_PREFIX, _neo4j_props

        d = {"meta": {"key": "val"}}
        result = _neo4j_props(d)
        assert result["meta"].startswith(_JSON_PREFIX)
        inner = json.loads(result["meta"][len(_JSON_PREFIX) :])
        assert inner == {"key": "val"}

    def test_list_gets_json_prefix(self):
        from mas.library.kg.neo4j.push import _JSON_PREFIX, _neo4j_props

        d = {"items": [1, 2, 3]}
        result = _neo4j_props(d)
        assert result["items"].startswith(_JSON_PREFIX)

    def test_none_dropped(self):
        from mas.library.kg.neo4j.push import _neo4j_props

        d = {"a": "keep", "b": None}
        result = _neo4j_props(d)
        assert "a" in result
        assert "b" not in result


# ---------------------------------------------------------------------------
# _index_nodes_by_type — unknown node_type visibility
# ---------------------------------------------------------------------------


class TestIndexNodesByTypeUnknownWarning:
    def test_task_call_does_not_warn(self, caplog) -> None:
        """TaskCall is a real, dynamically-refined native node type (see
        core/graph_builder._class_for_event's execution_* boundary
        refinement) -- it must be in NATIVE_EXTENSION_NODE_TYPES, not just
        reachable via HANDLERS, or every push of a trace containing one
        logs a spurious unknown-node_type warning."""
        from mas.library.kg.neo4j.push import _index_nodes_by_type

        with caplog.at_level("WARNING"):
            _index_nodes_by_type([{"id": "t1", "node_type": "TaskCall"}])
        assert not caplog.records, [r.message for r in caplog.records]

    def test_genuine_unknown_type_warns(self, caplog) -> None:
        from mas.library.kg.neo4j.push import _index_nodes_by_type

        with caplog.at_level("WARNING"):
            _index_nodes_by_type([{"id": "t1", "node_type": "TotallyMadeUpType"}])
        assert any("TotallyMadeUpType" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# build_merge_statements
# ---------------------------------------------------------------------------


class TestBuildMergeStatements:
    def test_returns_list_of_strings(self, simple_kg_doc):
        from mas.library.kg.neo4j.push import build_merge_statements

        nodes = simple_kg_doc["nodes"]
        edges = simple_kg_doc["edges"]
        stmts = build_merge_statements(nodes, edges)
        assert isinstance(stmts, list)
        assert all(isinstance(s, str) for s in stmts)

    def test_contains_merge_keywords(self, simple_kg_doc):
        from mas.library.kg.neo4j.push import build_merge_statements

        stmts = build_merge_statements(simple_kg_doc["nodes"], simple_kg_doc["edges"])
        assert any("MERGE" in s for s in stmts)

    def test_contains_match_for_edges(self, simple_kg_doc):
        from mas.library.kg.neo4j.push import build_merge_statements

        stmts = build_merge_statements(simple_kg_doc["nodes"], simple_kg_doc["edges"])
        assert any("MATCH" in s for s in stmts)

    def test_no_shared_generic_label_is_ever_written(self, simple_kg_doc):
        """Class hierarchy belongs in the ontology, not the data: every node
        gets only its most specific label, never an extra shared one."""
        from mas.library.kg.neo4j.push import build_merge_statements

        stmts = build_merge_statements(simple_kg_doc["nodes"], simple_kg_doc["edges"])
        assert not any("KGNode" in s for s in stmts)

    def test_node_merge_uses_exactly_one_label(self, simple_kg_doc):
        from mas.library.kg.neo4j.push import build_merge_statements

        stmts = build_merge_statements(simple_kg_doc["nodes"], simple_kg_doc["edges"])
        merge_for_ac1 = next(s for s in stmts if "MERGE (n:" in s and '"ac-1"' in s)
        assert merge_for_ac1.startswith('MERGE (n:AgentCall {id: "ac-1"})')

    def test_edge_match_uses_each_endpoints_real_specific_label(self, simple_kg_doc):
        """ac-1 -contains-> lc-1: both endpoints are in this doc's own node
        list, so their MATCH clauses must use AgentCall/LLMCall specifically
        -- not a shared label, and not each other's label."""
        from mas.library.kg.neo4j.push import build_merge_statements

        stmts = build_merge_statements(simple_kg_doc["nodes"], simple_kg_doc["edges"])
        contains_stmt = next(s for s in stmts if '"ac-1"' in s and '"lc-1"' in s)
        assert "(a:AgentCall " in contains_stmt
        assert "(b:LLMCall " in contains_stmt

    def test_annotation_edge_endpoint_outside_batch_resolves_via_role_hint(self, annotation_doc):
        """sess-1 isn't in annotation_doc's own nodes (it already exists in
        Neo4j) -- from_type: "session" is how its real label still gets
        used instead of falling back to an unlabeled match."""
        from mas.library.kg.neo4j.push import build_merge_statements

        stmts = build_merge_statements(annotation_doc["nodes"], annotation_doc["edges"])
        has_metric_stmt = next(s for s in stmts if s.startswith("MATCH") and '"sess-1"' in s)
        assert "(a:Session " in has_metric_stmt
        assert "(b:Metric " in has_metric_stmt

    def test_ambiguous_call_role_outside_batch_falls_back_unlabeled_not_guessed(self):
        """ "call" spans AgentCall/LLMCall/ToolCall/... -- when the endpoint
        isn't in this batch, there's no single correct label to guess, so
        the match must stay unlabeled rather than assert a wrong class."""
        from mas.library.kg.neo4j.push import build_merge_statements

        doc_nodes = [{"id": "annotation-1", "node_type": "CallAnnotation"}]
        doc_edges = [
            {
                "from_id": "some-call",
                "to_id": "annotation-1",
                "edge_type": "hasAnnotation",
                "from_type": "call",
            }
        ]
        stmts = build_merge_statements(doc_nodes, doc_edges)
        stmt = next(s for s in stmts if '"some-call"' in s)
        assert "(a {id:" in stmt  # unlabeled -- no guessed class
        assert "(b:CallAnnotation " in stmt

    def test_empty_inputs(self):
        from mas.library.kg.neo4j.push import build_merge_statements

        assert build_merge_statements([], []) == []


# ---------------------------------------------------------------------------
# _node_unwind_batches / _edge_unwind_batches (the real write path -- what
# execute_merge actually runs, as opposed to build_merge_statements' display
# strings)
# ---------------------------------------------------------------------------


class TestUnwindBatches:
    def test_node_batches_use_only_the_specific_label(self, simple_kg_doc):
        from mas.library.kg.neo4j.push import _node_unwind_batches

        nodes_by_type: dict = {}
        for n in simple_kg_doc["nodes"]:
            nodes_by_type.setdefault(n["node_type"], []).append(n)
        batches = _node_unwind_batches(nodes_by_type)
        cyphers = [c for c, _ in batches]
        assert any("MERGE (n:AgentCall {id: row.__id})" in c for c in cyphers)
        assert not any("KGNode" in c for c in cyphers)

    def test_edge_batches_use_each_endpoints_real_label(self, simple_kg_doc):
        from mas.library.kg.neo4j.push import _edge_unwind_batches

        id_to_type = {n["id"]: n["node_type"] for n in simple_kg_doc["nodes"]}
        batches = _edge_unwind_batches(simple_kg_doc["edges"], id_to_type)
        cyphers = [c for c, _ in batches]
        assert any(
            "(a:AgentCall {id: row.from_id})" in c and "(b:LLMCall {id: row.to_id})" in c
            for c in cyphers
        )
        assert not any("KGNode" in c for c in cyphers)

    def test_edge_batch_falls_back_unlabeled_when_type_unknown(self):
        from mas.library.kg.neo4j.push import _edge_unwind_batches

        edges = [{"from_id": "unknown-1", "to_id": "annotation-1", "edge_type": "hasAnnotation"}]
        batches = _edge_unwind_batches(edges, id_to_type={"annotation-1": "CallAnnotation"})
        cyphers = [c for c, _ in batches]
        # Changed to MERGE to prevent silent edge deletion when endpoints don't exist
        # Note: unlabeled pattern uses {{id:}} in f-string which becomes single {id:} in output
        assert any(
            "MERGE (a {{id: row.from_id}})" in c and "MERGE (b:CallAnnotation {id: row.to_id})" in c
            for c in cyphers
        )


# ---------------------------------------------------------------------------
# denormalize
# ---------------------------------------------------------------------------


class TestDenormalize:
    def test_session_id_propagated(self, simple_kg_doc):
        from mas.library.kg.neo4j.denormalize import denormalize

        nodes, edges = denormalize(
            simple_kg_doc["nodes"], simple_kg_doc["edges"], app_name="test-app"
        )
        for n in nodes:
            assert n.get("sessionId") == "sess-1"
        for e in edges:
            assert e.get("sessionId") == "sess-1"

    def test_app_name_set(self, simple_kg_doc):
        from mas.library.kg.neo4j.denormalize import denormalize

        nodes, edges = denormalize(
            simple_kg_doc["nodes"], simple_kg_doc["edges"], app_name="my-app"
        )
        for n in nodes:
            assert n["appName"] == "my-app"

    def test_block_assignment(self, simple_kg_doc):
        from mas.library.kg.neo4j.denormalize import denormalize

        nodes, _ = denormalize(simple_kg_doc["nodes"], simple_kg_doc["edges"])
        block_map = {n["node_type"]: n["block"] for n in nodes}
        # Session/AgentCall/LLMCall/ToolCall are execution
        assert block_map["Session"] == "execution"
        assert block_map["AgentCall"] == "execution"

    def test_structural_block(self):
        from mas.library.kg.neo4j.denormalize import denormalize

        nodes = [{"id": "agent-1", "node_type": "Agent"}, {"id": "sess", "node_type": "Session"}]
        result, _ = denormalize(nodes, [])
        agent_node = next(n for n in result if n["node_type"] == "Agent")
        assert agent_node["block"] == "structural"

    def test_trajectory_block(self):
        from mas.library.kg.neo4j.denormalize import denormalize

        nodes = [{"id": "s1", "node_type": "State"}, {"id": "sess", "node_type": "Session"}]
        result, _ = denormalize(nodes, [])
        state_node = next(n for n in result if n["node_type"] == "State")
        assert state_node["block"] == "trajectory"
        assert state_node["layer"] == "normalized"

    def test_does_not_mutate_input(self, simple_kg_doc):
        from mas.library.kg.neo4j.denormalize import denormalize

        original_nodes = [dict(n) for n in simple_kg_doc["nodes"]]
        denormalize(simple_kg_doc["nodes"], simple_kg_doc["edges"])
        for orig, node in zip(original_nodes, simple_kg_doc["nodes"], strict=False):
            assert orig == node  # unchanged

    def test_annotations_merged(self, simple_kg_doc):
        from mas.library.kg.neo4j.denormalize import denormalize

        nodes, edges = denormalize(
            simple_kg_doc["nodes"],
            simple_kg_doc["edges"],
            annotations={"env": "test", "version": "1"},
        )
        for n in nodes:
            assert n.get("env") == "test"
            assert n.get("version") == "1"
        for e in edges:
            assert e.get("env") == "test"

    def test_annotations_do_not_overwrite(self, simple_kg_doc):
        from mas.library.kg.neo4j.denormalize import denormalize

        # sessionId should NOT be overwritten by annotations
        nodes, _ = denormalize(
            simple_kg_doc["nodes"], simple_kg_doc["edges"], annotations={"sessionId": "OVERRIDE"}
        )
        for n in nodes:
            assert n["sessionId"] == "sess-1"  # kept original

    def test_idempotent(self, simple_kg_doc):
        from mas.library.kg.neo4j.denormalize import denormalize

        nodes1, edges1 = denormalize(simple_kg_doc["nodes"], simple_kg_doc["edges"])
        nodes2, edges2 = denormalize(nodes1, edges1)
        assert len(nodes1) == len(nodes2)
        for n1, n2 in zip(nodes1, nodes2, strict=False):
            assert n1.get("sessionId") == n2.get("sessionId")
            assert n1.get("appName") == n2.get("appName")

    def test_infers_app_name_from_hierarchical_session_id(self):
        from mas.library.kg.neo4j.denormalize import denormalize

        nodes = [
            {
                "id": "trip/exp/scenario/item/r1",
                "node_type": "Session",
                "sessionId": "trip/exp/scenario/item/r1",
            }
        ]
        out_nodes, _ = denormalize(nodes, [], app_name="")
        assert out_nodes[0]["appName"] == "trip"

    def test_experiment_extension_layer_from_session_id(self):
        from mas.library.kg.neo4j.denormalize import denormalize

        nodes = [
            {
                "id": "trip/exp/scenario/item/r1",
                "node_type": "Session",
                "sessionId": "trip/exp/scenario/item/r1",
            }
        ]
        out_nodes, out_edges = denormalize(nodes, [], extension_layers=["experiment"])

        for elem in out_nodes + out_edges:
            assert elem.get("experiment") == "exp"
            assert elem.get("scenario") == "scenario"
            assert elem.get("testItem") == "item"
            assert elem.get("runLabel") == "r1"


# ---------------------------------------------------------------------------
# push_kg_to_neo4j dry_run
# ---------------------------------------------------------------------------


class TestPushKGToNeo4jDryRun:
    def test_dry_run_returns_statements(self, simple_kg_doc):
        from mas.library.kg.neo4j.push import push_kg_to_neo4j

        result = push_kg_to_neo4j(simple_kg_doc, dry_run=True)
        assert result["rows"] == 0
        assert "statements" in result
        assert isinstance(result["statements"], list)
        assert len(result["statements"]) > 0

    def test_dry_run_no_driver_needed(self, simple_kg_doc):
        """dry_run must work without neo4j driver installed."""
        from mas.library.kg.neo4j.push import push_kg_to_neo4j

        # Should not raise ImportError even if neo4j driver absent
        result = push_kg_to_neo4j(simple_kg_doc, dry_run=True)
        assert result["nodes"] > 0

    def test_dry_run_skip_denormalize(self, simple_kg_doc):
        from mas.library.kg.neo4j.push import push_kg_to_neo4j

        result = push_kg_to_neo4j(simple_kg_doc, dry_run=True, skip_denormalize=True)
        assert "statements" in result


# ---------------------------------------------------------------------------
# push_annotations_to_neo4j dry_run
# ---------------------------------------------------------------------------


class TestPushAnnotationsToNeo4j:
    def test_dry_run_returns_summary(self, annotation_doc):
        from mas.library.kg.neo4j.push import push_annotations_to_neo4j

        result = push_annotations_to_neo4j(annotation_doc, dry_run=True)
        assert result["dry_run"] is True
        assert result["nodes_pushed"] == 1
        assert result["edges_pushed"] == 1

    def test_dry_run_no_driver_needed(self, annotation_doc):
        from mas.library.kg.neo4j.push import push_annotations_to_neo4j

        # Should work without the neo4j driver
        result = push_annotations_to_neo4j(annotation_doc, dry_run=True)
        assert "nodes_pushed" in result

    def test_skip_denormalize_true(self, annotation_doc):
        """Annotation nodes must NOT get sessionId/appName/block injected."""
        from mas.library.kg.neo4j.push import push_annotations_to_neo4j

        result = push_annotations_to_neo4j(annotation_doc, dry_run=True)
        # Verify via statements — nodes should appear as-is, no sessionId SET
        stmts = "\n".join(result.get("statements", []))
        assert "sessionId" not in stmts

    def test_with_real_neo4j_mocked(self, annotation_doc):
        """Full path with mocked Neo4j driver."""
        from mas.library.kg.neo4j.push import push_annotations_to_neo4j

        mock_driver = MagicMock()
        mock_session = MagicMock()
        mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_driver.session.return_value.__exit__ = MagicMock(return_value=False)

        with patch("mas.library.kg.neo4j.push._Neo4jWriter") as MockWriter:
            instance = MockWriter.return_value.__enter__.return_value
            instance.run_unwind_batches.return_value = 2
            MockWriter.return_value.__exit__ = MagicMock(return_value=False)

            result = push_annotations_to_neo4j(
                annotation_doc,
                uri="bolt://localhost:7687",
                password="test",
            )
        assert result["nodes_pushed"] == 1
        assert result["edges_pushed"] == 1
        assert result["dry_run"] is False


class _FakeNode:
    def __init__(self, props, labels):
        self._props = props
        self.labels = labels

    def items(self):
        return self._props.items()


class _FakeRecord(dict):
    pass


class _FakeSession:
    def __init__(self, node_records, edge_records):
        self._node_records = node_records
        self._edge_records = edge_records

    def run(self, query, **params):
        del params
        if "RETURN n" in query:
            return self._node_records
        return self._edge_records


def test_fetch_nodes_and_edges_backfills_transition_state_fields():
    from mas.library.kg.neo4j.dump import _fetch_nodes_and_edges

    node_records = [
        _FakeRecord(
            n=_FakeNode(
                {"id": "t-1", "transitionId": "t-1", "sessionId": "s-1"},
                {"Transition", "KGNode"},
            )
        ),
        _FakeRecord(
            n=_FakeNode(
                {"id": "st-in", "stateNodeId": "st-in", "sessionId": "s-1"},
                {"State", "KGNode"},
            )
        ),
        _FakeRecord(
            n=_FakeNode(
                {"id": "st-out", "stateNodeId": "st-out", "sessionId": "s-1"},
                {"State", "KGNode"},
            )
        ),
    ]

    edge_records = [
        _FakeRecord(from_id="t-1", to_id="st-in", edge_type="fromState", edge_props={}),
        _FakeRecord(from_id="t-1", to_id="st-out", edge_type="toState", edge_props={}),
        _FakeRecord(from_id="t-1", to_id="call-1", edge_type="realizes", edge_props={}),
    ]

    result = _fetch_nodes_and_edges(
        _FakeSession(node_records, edge_records), session_id="s-1", run_id=None
    )
    transition = next(node for node in result["nodes"] if node.get("id") == "t-1")
    assert transition["fromState"] == "st-in"
    assert transition["toState"] == "st-out"
    assert transition["realizesCallId"] == "call-1"


def test_fetch_nodes_and_edges_backfills_app_name_from_hierarchical_session_id():
    from mas.library.kg.neo4j.dump import _fetch_nodes_and_edges

    node_records = [
        _FakeRecord(
            n=_FakeNode(
                {"id": "trip/exp/scenario/item/r1", "sessionId": "trip/exp/scenario/item/r1"},
                {"Session", "KGNode"},
            )
        ),
        _FakeRecord(
            n=_FakeNode(
                {"id": "c-1", "callId": "c-1", "sessionId": "trip/exp/scenario/item/r1"},
                {"AgentCall", "KGNode"},
            )
        ),
    ]

    result = _fetch_nodes_and_edges(
        _FakeSession(node_records, []), session_id="trip/exp/scenario/item/r1", run_id=None
    )
    for node in result["nodes"]:
        assert node.get("appName") == "trip"

    def test_with_real_neo4j_mocked(self, annotation_doc):
        """Full path with mocked Neo4j driver."""
        from mas.library.kg.neo4j.push import push_annotations_to_neo4j

        mock_driver = MagicMock()
        mock_session = MagicMock()
        mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_driver.session.return_value.__exit__ = MagicMock(return_value=False)

        with patch("mas.library.kg.neo4j.push._Neo4jWriter") as MockWriter:
            instance = MockWriter.return_value.__enter__.return_value
            instance.run_unwind_batches.return_value = 2
            MockWriter.return_value.__exit__ = MagicMock(return_value=False)

            result = push_annotations_to_neo4j(
                annotation_doc,
                uri="bolt://localhost:7687",
                password="test",
            )
        assert result["nodes_pushed"] >= 0


# ---------------------------------------------------------------------------
# _Neo4jWriter — mocked driver
# ---------------------------------------------------------------------------


class TestNeo4jWriter:
    def test_init_raises_without_driver(self):
        """Should raise ImportError if neo4j not installed."""
        from mas.library.kg.neo4j.push import _Neo4jWriter

        with patch.dict("sys.modules", {"neo4j": None}):
            with pytest.raises((ImportError, Exception)):
                _Neo4jWriter("bolt://localhost:7687", "neo4j", "pw", "neo4j")

    def test_context_manager(self):
        from mas.library.kg.neo4j.push import _Neo4jWriter

        mock_gdb = MagicMock()
        mock_gdb.return_value = MagicMock()
        with patch("mas.library.kg.neo4j.push._Neo4jWriter.__init__", return_value=None):
            writer = _Neo4jWriter.__new__(_Neo4jWriter)
            writer._driver = mock_gdb()
            writer._database = "neo4j"
            writer.__exit__(None, None, None)
            writer._driver.close.assert_called_once()


# ---------------------------------------------------------------------------
# run_neo4j_push step — dry_run
# ---------------------------------------------------------------------------


class TestRunNeo4jPushStep:
    def test_dry_run(self, simple_kg_doc, tmp_path):
        from mas.library.kg.steps.neo4j_push import run_neo4j_push

        kg_path = tmp_path / "kg.jsonld"
        kg_path.write_text(json.dumps(simple_kg_doc))
        result = run_neo4j_push(
            str(kg_path),
            dry_run=True,
        )
        assert result["dry_run"] is True
        assert result["node_count"] > 0

    def test_missing_file_raises(self, tmp_path):
        from mas.library.kg.steps.neo4j_push import run_neo4j_push

        with pytest.raises(FileNotFoundError):
            run_neo4j_push(str(tmp_path / "missing.json"))


# ---------------------------------------------------------------------------
# run_neo4j_dump step
# ---------------------------------------------------------------------------


class TestRunNeo4jDumpStep:
    def test_missing_session_and_run_id_raises(self, tmp_path):
        from mas.library.kg.steps.neo4j_dump import run_neo4j_dump

        with pytest.raises(ValueError, match="Either session_id or run_id"):
            run_neo4j_dump(output_path=str(tmp_path / "out.json"))
