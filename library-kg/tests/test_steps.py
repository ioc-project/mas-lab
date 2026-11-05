#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for mas.library.kg.steps — standalone step functions.

Uses tmp_path for file I/O. No PipelineStep dependency.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def minimal_events_jsonl(tmp_path) -> Path:
    """Minimal valid events.jsonl with two events."""
    events = [
        {
            "kind": "agent_call_start",
            "timestamp": 1704067200.0,
            "run_id": "test-run",
            "call_id": "urn:mas:call:agent-1",
            "agent_id": "agent-1",
            "trace_id": "trace-abc",
            "span_id": "span-001",
        },
        {
            "kind": "agent_call_end",
            "timestamp": 1704067210.0,
            "run_id": "test-run",
            "call_id": "urn:mas:call:agent-1",
            "agent_id": "agent-1",
            "trace_id": "trace-abc",
            "span_id": "span-001",
        },
    ]
    p = tmp_path / "events.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in events))
    return p


@pytest.fixture
def minimal_kg_json(tmp_path) -> Path:
    """Minimal kg.jsonld that actually satisfies mas-shapes.ttl.

    A single orphaned AgentCall (no containing Session/Run, no agentName)
    used to pass here for a long time -- not because it was valid, but
    because the "shacl" check was silently inert (see core/verifier.py's
    KGCheckSkipped / mas-shapes.ttl's PREFIX fixes). Once SHACL actually
    ran, this fixture surfaced 2 real violations
    (ContainmentChainShape + AgentCallShape.agentName) despite calling
    itself "minimal valid". This is the shape every other "valid KG"
    fixture in this suite should match.
    """
    doc = {
        "nodes": [
            {
                "id": "sess-1",
                "node_type": "Session",
                "sessionId": "sess-1",
                "appName": "test-app",
            },
            {"id": "run-1", "node_type": "Run", "runId": "test-run"},
            {
                "callId": "call-root",
                "id": "call-root",
                "node_type": "AgentCall",
                "agentName": "test-agent",
                "startTime": 1704067200.0,
                "endTime": 1704067210.0,
                "sessionId": "sess-1",
            },
            {
                "id": "st-in", "node_type": "State", "stateNodeId": "st-in",
                "contentHash": "h1", "semanticType": "initial", "content": "hello",
            },
            {
                "id": "st-out", "node_type": "State", "stateNodeId": "st-out",
                "contentHash": "h2", "semanticType": "final", "content": "world",
            },
            {
                "id": "t-1", "node_type": "Transition", "transitionId": "t-1",
                "edgeType": "sequential", "fromState": "st-in", "toState": "st-out",
            },
        ],
        "edges": [
            {"edge_type": "contains", "from_id": "run-1", "to_id": "sess-1"},
            {"edge_type": "hasCall", "from_id": "sess-1", "to_id": "call-root"},
            {"edge_type": "hasInitialState", "from_id": "sess-1", "to_id": "st-in"},
            {"edge_type": "hasFinalState", "from_id": "sess-1", "to_id": "st-out"},
            {"edge_type": "inputTo", "from_id": "st-in", "to_id": "t-1"},
            {"edge_type": "leadsTo", "from_id": "t-1", "to_id": "st-out"},
            {"edge_type": "fromState", "from_id": "t-1", "to_id": "st-in"},
            {"edge_type": "toState", "from_id": "t-1", "to_id": "st-out"},
        ],
        "metadata": {"run_id": "test-run"},
    }
    p = tmp_path / "kg.jsonld"
    p.write_text(json.dumps(doc))
    return p


@pytest.fixture
def minimal_otel_spans(tmp_path) -> Path:
    """Minimal MAS SDK OTel spans JSON file."""
    spans = [
        {
            "name": "mas.agent",
            "context": {"trace_id": "trace-aaa", "span_id": "s1"},
            "parent_id": None,
            "attributes": {
                "mas.boundary": "AgentCall",
                "mas.call.id": "call-root",
                "mas.agent.id": "agent-1",
                "mas.session.id": "sess-1",
                "mas.run.id": "run-otel",
            },
            "start_time": "2024-01-01T00:00:00Z",
            "end_time": "2024-01-01T00:01:00Z",
        },
    ]
    p = tmp_path / "otel.json"
    p.write_text(json.dumps(spans))
    return p


# ---------------------------------------------------------------------------
# run_verify_events
# ---------------------------------------------------------------------------


class TestRunVerifyEvents:
    def test_valid_file_returns_no_errors(self, minimal_events_jsonl):
        from mas.library.kg.steps.verify_events import run_verify_events

        result = run_verify_events(minimal_events_jsonl, strictness="required")
        assert isinstance(result["error_count"], int)
        assert isinstance(result["event_count"], int)
        assert result["event_count"] == 2

    def test_missing_file_raises(self, tmp_path):
        from mas.library.kg.steps.verify_events import run_verify_events

        with pytest.raises(FileNotFoundError):
            run_verify_events(tmp_path / "nonexistent.jsonl")

    def test_fail_on_error_raises_when_errors(self, tmp_path):
        from mas.library.kg.steps.verify_events import run_verify_events

        # invalid json line will trigger errors
        bad = tmp_path / "bad.jsonl"
        bad.write_text('{"kind": "agent_call_start"}\n')  # missing required fields
        result = run_verify_events(bad, strictness="required", fail_on_error=False)
        # Should not raise even if there are errors
        assert "error_count" in result


# ---------------------------------------------------------------------------
# run_validate_kg
# ---------------------------------------------------------------------------


class TestRunValidateKg:
    def test_valid_kg_returns_result_dict(self, tmp_path):
        from mas.library.kg.steps.validate_kg import run_validate_kg

        kg_path = tmp_path / "kg.jsonld"
        kg_path.write_text(json.dumps({"nodes": [], "edges": []}))

        result = run_validate_kg(
            kg_path,
            checks=[
                "unknown_node_types",
                "unknown_edge_types",
            ],
        )
        assert "error_count" in result
        assert "warning_count" in result
        assert "skipped_count" in result
        assert "results" in result
        assert isinstance(result["results"], list)
        assert result["error_count"] == 0

    def test_missing_kg_file_raises(self, tmp_path):
        from mas.library.kg.steps.validate_kg import run_validate_kg

        with pytest.raises(FileNotFoundError):
            run_validate_kg(tmp_path / "nonexistent.json")

    def test_unknown_check_name_is_a_validation_error(self, minimal_kg_json):
        from mas.library.kg.steps.validate_kg import KGValidationError, run_validate_kg

        # Unknown checks are explicit validation errors, and run_validate_kg
        # always raises when error_count is nonzero -- there is no
        # fail_on_error toggle to opt out of that.
        with pytest.raises(KGValidationError) as excinfo:
            run_validate_kg(minimal_kg_json, checks=["totally_fake_check"])
        assert excinfo.value.report["error_count"] == 1

    def test_always_raises_when_error_count_nonzero(self, tmp_path):
        from mas.library.kg.steps.validate_kg import KGValidationError, run_validate_kg

        # KG with unknown node type to force an error
        doc = {
            "nodes": [{"callId": "x", "node_type": "CompletelyUnknownXXX"}],
            "edges": [],
        }
        p = tmp_path / "bad_kg.jsonld"
        p.write_text(json.dumps(doc))
        with pytest.raises(KGValidationError, match="validation failed") as excinfo:
            run_validate_kg(p, checks=["unknown_node_types"])
        assert excinfo.value.report["error_count"] > 0


# ---------------------------------------------------------------------------
# run_normalize
# ---------------------------------------------------------------------------


class TestRunNormalize:
    def test_dry_run_returns_counts_without_writing(self, minimal_events_jsonl, tmp_path):
        from mas.library.kg.steps.normalize import run_normalize
        artifact = run_normalize(
            minimal_events_jsonl, run_id="test-run", output_dir=tmp_path, dry_run=True
        )
        assert artifact.node_count >= 0
        assert artifact.edge_count >= 0
        assert not (tmp_path / "kg.jsonld").exists()

    def test_writes_kg_json_to_output_dir(self, minimal_events_jsonl, tmp_path):
        from mas.library.kg.steps.normalize import run_normalize
        artifact = run_normalize(
            minimal_events_jsonl, run_id="test-run", output_dir=tmp_path
        )
        assert artifact.node_count >= 0
        assert artifact.edge_count >= 0
        assert (tmp_path / "kg.jsonld").exists()

    def test_result_has_required_keys(self, minimal_events_jsonl, tmp_path):
        from mas.library.kg.steps.normalize import run_normalize
        artifact = run_normalize(
            minimal_events_jsonl, run_id="test-run", output_dir=tmp_path, dry_run=True
        )
        assert hasattr(artifact, "nodes")
        assert hasattr(artifact, "edges")
        assert hasattr(artifact, "metadata")

    def test_infers_app_name_without_app_name_override(self, tmp_path):
        from mas.library.kg.steps.normalize import run_normalize

        events = [
            {
                "kind": "execution_start",
                "timestamp": 1704067200.0,
                "run_id": "trip-planner/exp-a/baseline/item1/r1",
                "call_id": "call-1",
                "agent_id": "agent-1",
            },
            {
                "kind": "execution_end",
                "timestamp": 1704067210.0,
                "run_id": "trip-planner/exp-a/baseline/item1/r1",
                "call_id": "call-1",
                "agent_id": "agent-1",
            },
        ]
        events_path = tmp_path / "events-hierarchical.jsonl"
        events_path.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")

        artifact = run_normalize(
            events_path,
            run_id="trip-planner/exp-a/baseline/item1/r1",
            output_dir=tmp_path,
            dry_run=True,
            all_attributes=True,
        )
        session = next(n for n in artifact.nodes if n.get("node_type") == "Session")
        assert session.get("appName") == "trip-planner"

    def test_application_node_disabled_by_default(self, tmp_path):
        from mas.library.kg.steps.normalize import run_normalize

        events = [
            {
                "kind": "execution_start",
                "timestamp": 1704067200.0,
                "run_id": "trip-planner/exp-a/baseline/item1/r1",
                "call_id": "call-1",
                "agent_id": "agent-1",
            },
            {
                "kind": "execution_end",
                "timestamp": 1704067210.0,
                "run_id": "trip-planner/exp-a/baseline/item1/r1",
                "call_id": "call-1",
                "agent_id": "agent-1",
            },
        ]
        events_path = tmp_path / "events-default-app-node.jsonl"
        events_path.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")

        artifact = run_normalize(
            events_path,
            run_id="trip-planner/exp-a/baseline/item1/r1",
            output_dir=tmp_path,
            dry_run=True,
        )

        assert not any(n.get("node_type") == "Application" for n in artifact.nodes)
        assert not any(e.get("edge_type") == "hasSession" for e in artifact.edges)

    def test_application_node_enabled_creates_node_and_edges(self, tmp_path):
        from mas.library.kg.steps.normalize import run_normalize

        events = [
            {
                "kind": "execution_start",
                "timestamp": 1704067200.0,
                "run_id": "trip-planner/exp-a/baseline/item1/r1",
                "call_id": "call-1",
                "agent_id": "agent-1",
            },
            {
                "kind": "execution_end",
                "timestamp": 1704067210.0,
                "run_id": "trip-planner/exp-a/baseline/item1/r1",
                "call_id": "call-1",
                "agent_id": "agent-1",
            },
        ]
        events_path = tmp_path / "events-enable-app-node.jsonl"
        events_path.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")

        artifact = run_normalize(
            events_path,
            run_id="trip-planner/exp-a/baseline/item1/r1",
            output_dir=tmp_path,
            application_node=True,
            dry_run=True,
        )

        app_nodes = [n for n in artifact.nodes if n.get("node_type") == "Application"]
        assert len(app_nodes) == 1
        assert app_nodes[0].get("appName") == "trip-planner"

        has_session = [e for e in artifact.edges if e.get("edge_type") == "hasSession"]
        assert len(has_session) >= 1
        assert has_session[0].get("from_id") == "app:trip-planner"


# ---------------------------------------------------------------------------
# run_annotate
# ---------------------------------------------------------------------------


class TestRunAnnotate:
    def test_annotates_matching_nodes(self, minimal_kg_json):
        from mas.library.kg.steps.annotate import run_annotate

        result = run_annotate(
            minimal_kg_json,
            value_fn=lambda n: {"score": 0.9},
            node_type="AgentCall",
            edge_name="hasScore",
            dry_run=True,
        )
        assert result.node_count >= 1
        assert result.edge_count >= 0

    def test_dry_run_does_not_write(self, minimal_kg_json):
        from mas.library.kg.steps.annotate import run_annotate

        original_mtime = minimal_kg_json.stat().st_mtime
        run_annotate(
            minimal_kg_json,
            value_fn=lambda n: {"v": 1},
            node_type="AgentCall",
            edge_name="hasScore",
            dry_run=True,
        )
        assert minimal_kg_json.stat().st_mtime == original_mtime

    def test_writes_output_to_specified_path(self, minimal_kg_json, tmp_path):
        from mas.library.kg.steps.annotate import run_annotate
        out = tmp_path / "enriched_kg.jsonld"
        _result = run_annotate(
            minimal_kg_json,
            value_fn=lambda n: {"v": 1},
            node_type="AgentCall",
            edge_name="hasScore",
            output_path=out,
        )
        assert out.exists()


# ---------------------------------------------------------------------------
# run_normalize_otel
# ---------------------------------------------------------------------------


class TestRunNormalizeOtel:
    def test_otel_spans_produce_output(self, minimal_otel_spans, tmp_path):
        # run_normalize_otel returns a KGArtifact (a dataclass, not a dict) —
        # otel_format/event_count live in its `.metadata` dict.
        from mas.library.kg.steps.normalize_otel import run_normalize_otel

        result = run_normalize_otel(
            minimal_otel_spans, run_id="test-run", output_dir=tmp_path, dry_run=True
        )
        assert "otel_format" in result.metadata
        assert result.metadata["otel_format"] in ("sdk", "clickhouse_row")
        assert result.metadata["event_count"] >= 0

    def test_result_has_required_keys(self, minimal_otel_spans, tmp_path):
        from mas.library.kg.steps.normalize_otel import run_normalize_otel

        result = run_normalize_otel(
            minimal_otel_spans, run_id="test-run", output_dir=tmp_path, dry_run=True
        )
        for key in ("event_count", "otel_format"):
            assert key in result.metadata
        assert result.node_count >= 0
        assert result.edge_count >= 0

    def test_unrecognisable_format_degrades_to_empty_artifact_instead_of_raising(
        self,
        tmp_path,
    ):
        """OtelFormatError (whole-batch gap: the entire span list can't be
        classified) must never crash the step — it should degrade to an
        empty, clearly-flagged KGArtifact instead, regardless of `strict`."""
        from mas.library.kg.steps.normalize_otel import run_normalize_otel

        spans_path = tmp_path / "unrecognisable.json"
        spans_path.write_text(json.dumps([{"foo": "bar", "baz": 42}]))

        result = run_normalize_otel(
            spans_path,
            run_id="test-run",
            output_dir=tmp_path,
            dry_run=True,
        )
        assert result.metadata["otel_format"] == "unknown"
        assert result.metadata["event_count"] == 0
        assert result.node_count == 0
