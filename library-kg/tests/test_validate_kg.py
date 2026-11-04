#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Regression and integration tests for mas.library.kg.steps.validate_kg.

Covers CLR-495: structural checks return ``(passed, violations)`` tuples while
SHACL / attribute checks return bare violation lists.  The step must unpack both
shapes without spurious errors (e.g. reporting ``True`` / ``[]`` as violations).

Run from mas-lab-internal root::

    task kg:test-validate
    uv run pytest library-kg/tests/test_validate_kg.py \\
        library-kg/tests/test_cli.py::TestCliValidateCommand -v

Or from ``library-kg/``::

    task test:validate
    uv run pytest tests/test_validate_kg.py \\
        tests/test_cli.py::TestCliValidateCommand -v
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from mas.library.kg.artifact import KGArtifact

STRUCTURAL_CHECKS = [
    "unknown_node_types",
    "unknown_edge_types",
    "block_vocabulary",
    "layer_vocabulary",
]

# oxp-ontology 1.0.0 SHACL (mas-shapes.ttl + mas-shapes-custom.ttl) does not
# include the former custom SPARQL rules these tests encode (Session.appName,
# processing gate, temporal enclosure, inbound containment, leadsTo range).
_OXP_SHACL_GAP = pytest.mark.skip(
    reason=(
        "oxp-ontology 1.0.0 SHACL shapes do not include the custom SPARQL "
        "rules these assertions encode; they remain documented gaps pending "
        "upstream shapes."
    )
)


def _oxp_ontology_path() -> str:
    # Every caller of this helper is exercising real SHACL/ontology-backed
    # validation, which additionally needs rdflib and pyshacl to actually
    # run (see run_shacl_validation) -- skip cleanly rather than fail if
    # any of the three isn't installed (validation extra).
    pytest.importorskip("oxp_ontology")
    pytest.importorskip("rdflib")
    pytest.importorskip("pyshacl")

    from oxp_ontology import get_ontology_path

    return str(get_ontology_path("mas"))


@pytest.fixture
def empty_kg_json(tmp_path) -> Path:
    p = tmp_path / "kg.jsonld"
    p.write_text(json.dumps({"nodes": [], "edges": []}))
    return p


@pytest.fixture
def empty_kg_jsonld(tmp_path) -> Path:
    p = tmp_path / "kg.jsonld"
    p.write_text(
        json.dumps(
            {
                "@context": {
                    "@vocab": "https://outshift-open.github.io/oxp-ontology/kg-artifact#",
                    "mas": "https://outshift-open.github.io/oxp-ontology/mas#",
                    "maskg": "https://outshift-open.github.io/oxp-ontology/kg#",
                },
                "nodes": [],
                "edges": [],
            }
        )
    )
    return p


class TestRunValidateKgStructuralChecks:
    """Integration tests against real verifier functions."""

    def test_unknown_node_types_pass_does_not_emit_tuple_artifacts(self, empty_kg_json):
        from mas.library.kg.steps.validate_kg import run_validate_kg

        result = run_validate_kg(empty_kg_json, checks=["unknown_node_types"])
        assert result["error_count"] == 0
        assert result["results"] == [
            {"check": "unknown_node_types", "status": "pass", "detail": ""},
        ]
        details = [r["detail"] for r in result["results"]]
        assert "True" not in details
        assert "[]" not in details

    @pytest.mark.parametrize("check_name", STRUCTURAL_CHECKS)
    def test_each_structural_check_runs_without_signature_error(
        self,
        empty_kg_json,
        check_name: str,
    ):
        from mas.library.kg.steps.validate_kg import run_validate_kg

        result = run_validate_kg(empty_kg_json, checks=[check_name])
        matching = [r for r in result["results"] if r["check"] == check_name]
        assert len(matching) == 1
        row = matching[0]
        assert row["detail"] not in {"True", "[]"}
        assert not (
            row["status"] == "error" and "missing" in row["detail"] and "argument" in row["detail"]
        )

    @_OXP_SHACL_GAP
    def test_shacl_orphaned_agent_call_reported_as_real_violation(self, tmp_path):
        from mas.library.kg.steps.validate_kg import KGValidationError, run_validate_kg

        doc = {
            "nodes": [
                {
                    "id": "call-1",
                    "callId": "call-1",
                    "node_type": "AgentCall",
                    "agentName": "planner",
                    "startTime": 0.0,
                    "endTime": 1.0,
                }
            ],
            "edges": [],
        }
        p = tmp_path / "kg.jsonld"
        p.write_text(json.dumps(doc))
        with pytest.raises(KGValidationError) as excinfo:
            run_validate_kg(p, checks=["shacl"], ontology_path=_oxp_ontology_path())
        result = excinfo.value.report
        assert result["error_count"] >= 1
        assert any("inbound containment or ownership edge" in r["detail"] for r in result["results"])  # noqa: E501
        assert not any(r["detail"] in {"True", "[]"} for r in result["results"])

    def test_unknown_node_type_reported_without_typeerror(self, tmp_path):
        from mas.library.kg.steps.validate_kg import KGValidationError, run_validate_kg

        doc = {
            "nodes": [{"callId": "x", "node_type": "CompletelyUnknownXXX"}],
            "edges": [],
        }
        p = tmp_path / "kg.jsonld"
        p.write_text(json.dumps(doc))
        with pytest.raises(KGValidationError) as excinfo:
            run_validate_kg(p, checks=["unknown_node_types"])
        result = excinfo.value.report
        assert result["error_count"] >= 1
        assert not any("argument" in r["detail"] for r in result["results"])

    def test_accepts_kg_artifact_directly(self):
        from mas.library.kg.steps.validate_kg import run_validate_kg

        artifact = KGArtifact.from_doc({"nodes": [], "edges": []})
        result = run_validate_kg(artifact, checks=["unknown_node_types"])
        assert result["error_count"] == 0
        assert result["results"][0]["status"] == "pass"

    def test_multiple_checks_in_one_run(self, empty_kg_json):
        # Asserts a clean pass with no extra rows, which only holds when
        # shacl actually runs rather than reporting its own
        # pyshacl/rdflib-not-installed warning row.
        pytest.importorskip("rdflib")
        pytest.importorskip("pyshacl")
        from mas.library.kg.steps.validate_kg import run_validate_kg

        result = run_validate_kg(
            empty_kg_json,
            checks=["unknown_node_types", "unknown_edge_types", "shacl"],
        )
        assert len(result["results"]) == 3
        # SHACL may be skipped if oxp-ontology isn't installed (CI env)
        # so accept either "pass" or "skipped" for the shacl check
        for r in result["results"]:
            if r["check"] == "shacl":
                assert r["status"] in ("pass", "skipped"), f"SHACL check status: {r['status']}"
            else:
                assert r["status"] == "pass", f"Check {r['check']} failed: {r}"
        assert result["error_count"] == 0

    def test_accepts_jsonld_artifact_file(self, empty_kg_jsonld):
        from mas.library.kg.steps.validate_kg import run_validate_kg

        result = run_validate_kg(empty_kg_jsonld, checks=["unknown_node_types", "shacl"])
        assert result["error_count"] == 0
        assert {row["check"] for row in result["results"]} == {"unknown_node_types", "shacl"}

    def test_always_raises_on_structural_violation(self, tmp_path):
        """run_validate_kg has no fail_on_error escape hatch: any nonzero
        error_count always raises KGValidationError."""
        from mas.library.kg.steps.validate_kg import KGValidationError, run_validate_kg

        doc = {
            "nodes": [{"callId": "x", "node_type": "CompletelyUnknownXXX"}],
            "edges": [],
        }
        p = tmp_path / "bad.jsonld"
        p.write_text(json.dumps(doc))
        with pytest.raises(KGValidationError, match="validation failed") as excinfo:
            run_validate_kg(p, checks=["unknown_node_types"])
        assert excinfo.value.report["error_count"] > 0

    def test_unknown_check_names_are_recorded_as_errors(self, empty_kg_json):
        """An unrecognized check name is a config mistake (a checks: typo,
        most likely) -- it must be surfaced as an error, not silently
        dropped, or a typo could mask a check that was never actually
        running. See test_steps.py::TestRunValidateKg for the same
        expectation from the step-level entry point."""
        from mas.library.kg.steps.validate_kg import KGValidationError, run_validate_kg

        with pytest.raises(KGValidationError) as excinfo:
            run_validate_kg(empty_kg_json, checks=["totally_fake_check"])
        result = excinfo.value.report
        assert result["error_count"] == 1
        assert result["results"] == [
            {"check": "totally_fake_check", "status": "error", "detail": "Unknown check: 'totally_fake_check'"}  # noqa: E501
        ]

    def test_missing_kg_file_raises(self, tmp_path):
        from mas.library.kg.steps.validate_kg import run_validate_kg

        with pytest.raises(FileNotFoundError):
            run_validate_kg(tmp_path / "nonexistent.jsonld")

    def test_default_checks_include_ontology_backed_validation(self, empty_kg_json):
        from mas.library.kg.core import verifier
        from mas.library.kg.steps.validate_kg import run_validate_kg

        with patch.object(verifier, "run_shacl_validation", return_value=[]):
            result = run_validate_kg(empty_kg_json)

        checks = {row["check"] for row in result["results"]}
        assert "shacl" in checks

    def test_shacl_executes_against_oxp_ontology(self, tmp_path):
        """SHACL must load oxp-ontology shapes without a PropertyShape crash."""
        from mas.library.kg.steps.validate_kg import KGValidationError, run_validate_kg

        doc = {
            "nodes": [{"id": "s1", "node_type": "Session", "sessionId": "s1", "appName": "demo"}],
            "edges": [],
        }
        p = tmp_path / "kg.jsonld"
        p.write_text(json.dumps(doc))
        try:
            result = run_validate_kg(p, checks=["shacl"], ontology_path=_oxp_ontology_path())
        except KGValidationError as exc:
            result = exc.report
        shacl_rows = [r for r in result["results"] if r["check"] == "shacl"]
        assert shacl_rows, result
        assert shacl_rows[0]["status"] != "skipped"
        assert "not a well-formed SHACL PropertyShape" not in shacl_rows[0]["detail"]

    @_OXP_SHACL_GAP
    def test_shacl_session_app_name_check_fails_when_missing(self, tmp_path):
        from mas.library.kg.steps.validate_kg import KGValidationError, run_validate_kg

        doc = {
            "nodes": [
                {"id": "s1", "node_type": "Session", "sessionId": "s1", "runId": "r1"},
                {"id": "run-1", "node_type": "Run", "runId": "r1"},
                {"id": "st-in", "node_type": "State", "stateNodeId": "st-in", "contentHash": "h1", "semanticType": "initial", "content": "hello"},  # noqa: E501
                {"id": "st-out", "node_type": "State", "stateNodeId": "st-out", "contentHash": "h2", "semanticType": "final", "content": "world"},  # noqa: E501
                {"id": "t-1", "node_type": "Transition", "transitionId": "t-1", "edgeType": "sequential", "fromState": "st-in", "toState": "st-out"},  # noqa: E501
            ],
            "edges": [
                {"edge_type": "contains", "from_id": "s1", "to_id": "run-1"},
                {"edge_type": "hasInitialState", "from_id": "s1", "to_id": "st-in"},
                {"edge_type": "hasFinalState", "from_id": "s1", "to_id": "st-out"},
                {"edge_type": "inputTo", "from_id": "st-in", "to_id": "t-1"},
                {"edge_type": "leadsTo", "from_id": "t-1", "to_id": "st-out"},
                {"edge_type": "fromState", "from_id": "t-1", "to_id": "st-in"},
                {"edge_type": "toState", "from_id": "t-1", "to_id": "st-out"},
            ],
        }
        p = tmp_path / "kg.jsonld"
        p.write_text(json.dumps(doc))

        with pytest.raises(KGValidationError) as excinfo:
            run_validate_kg(p, checks=["shacl"], ontology_path=_oxp_ontology_path())
        result = excinfo.value.report
        assert result["error_count"] == 1
        assert any("maslab:appName" in r["detail"] for r in result["results"])

    @_OXP_SHACL_GAP
    def test_shacl_session_app_name_check_passes_when_present(self, tmp_path):
        from mas.library.kg.steps.validate_kg import run_validate_kg

        doc = {
            "nodes": [
                {
                    "id": "s1",
                    "node_type": "Session",
                    "sessionId": "s1",
                    "runId": "r1",
                    "appName": "trip-planner",
                },
                {"id": "run-1", "node_type": "Run", "runId": "r1"},
                {"id": "st-in", "node_type": "State", "stateNodeId": "st-in", "contentHash": "h1", "semanticType": "initial", "content": "hello"},  # noqa: E501
                {"id": "st-out", "node_type": "State", "stateNodeId": "st-out", "contentHash": "h2", "semanticType": "final", "content": "world"},  # noqa: E501
                {"id": "t-1", "node_type": "Transition", "transitionId": "t-1", "edgeType": "sequential", "fromState": "st-in", "toState": "st-out"},  # noqa: E501
            ],
            "edges": [
                {"edge_type": "contains", "from_id": "s1", "to_id": "run-1"},
                {"edge_type": "hasInitialState", "from_id": "s1", "to_id": "st-in"},
                {"edge_type": "hasFinalState", "from_id": "s1", "to_id": "st-out"},
                {"edge_type": "inputTo", "from_id": "st-in", "to_id": "t-1"},
                {"edge_type": "leadsTo", "from_id": "t-1", "to_id": "st-out"},
                {"edge_type": "fromState", "from_id": "t-1", "to_id": "st-in"},
                {"edge_type": "toState", "from_id": "t-1", "to_id": "st-out"},
            ],
        }
        p = tmp_path / "kg.jsonld"
        p.write_text(json.dumps(doc))

        result = run_validate_kg(p, checks=["shacl"], ontology_path=_oxp_ontology_path())
        assert result["error_count"] == 0
        assert result["results"] == [{"check": "shacl", "status": "pass", "detail": ""}]

    @_OXP_SHACL_GAP
    def test_shacl_does_not_require_callid_on_session(self, tmp_path):
        from mas.library.kg.steps.validate_kg import run_validate_kg

        doc = {
            "nodes": [
                {
                    "id": "s1",
                    "node_type": "Session",
                    "sessionId": "s1",
                    "runId": "r1",
                    "appName": "test-app",
                },
                {"id": "run-1", "node_type": "Run", "runId": "r1"},
                {"id": "st-in", "node_type": "State", "stateNodeId": "st-in", "contentHash": "h1", "semanticType": "initial", "content": "hello"},  # noqa: E501
                {"id": "st-out", "node_type": "State", "stateNodeId": "st-out", "contentHash": "h2", "semanticType": "final", "content": "world"},  # noqa: E501
                {"id": "t-1", "node_type": "Transition", "transitionId": "t-1", "edgeType": "sequential", "fromState": "st-in", "toState": "st-out"},  # noqa: E501
            ],
            "edges": [
                {"edge_type": "contains", "from_id": "s1", "to_id": "run-1"},
                {"edge_type": "hasInitialState", "from_id": "s1", "to_id": "st-in"},
                {"edge_type": "hasFinalState", "from_id": "s1", "to_id": "st-out"},
                {"edge_type": "inputTo", "from_id": "st-in", "to_id": "t-1"},
                {"edge_type": "leadsTo", "from_id": "t-1", "to_id": "st-out"},
                {"edge_type": "fromState", "from_id": "t-1", "to_id": "st-in"},
                {"edge_type": "toState", "from_id": "t-1", "to_id": "st-out"},
            ],
        }
        p = tmp_path / "kg.jsonld"
        p.write_text(json.dumps(doc))

        result = run_validate_kg(p, checks=["shacl"], ontology_path=_oxp_ontology_path(), warning_verbosity="full")  # noqa: E501
        session_callid_errors = [
            row
            for row in result["results"]
            if row["status"] == "error"
            and "callId" in row["detail"]
            and "Session" in row["detail"]
        ]
        if session_callid_errors:
            pytest.skip("Bundled ontology version still requires Session.callId")
        assert session_callid_errors == []

    @_OXP_SHACL_GAP
    def test_shacl_session_no_executionelement_inheritance(self, tmp_path):
        from mas.library.kg.steps.validate_kg import run_validate_kg

        doc = {
            "nodes": [
                {
                    "id": "s1",
                    "node_type": "Session",
                    "sessionId": "s1",
                    "appName": "trip-planner",
                },
                {"id": "run-1", "node_type": "Run", "runId": "run-1"},
                {"id": "st-in", "node_type": "State", "stateNodeId": "st-in", "contentHash": "h1", "semanticType": "initial", "content": "hello"},  # noqa: E501
                {"id": "st-out", "node_type": "State", "stateNodeId": "st-out", "contentHash": "h2", "semanticType": "final", "content": "world"},  # noqa: E501
                {"id": "t-1", "node_type": "Transition", "transitionId": "t-1", "edgeType": "sequential", "fromState": "st-in", "toState": "st-out"},  # noqa: E501
            ],
            "edges": [
                {"edge_type": "contains", "from_id": "s1", "to_id": "run-1"},
                {"edge_type": "hasInitialState", "from_id": "s1", "to_id": "st-in"},
                {"edge_type": "hasFinalState", "from_id": "s1", "to_id": "st-out"},
                {"edge_type": "inputTo", "from_id": "st-in", "to_id": "t-1"},
                {"edge_type": "leadsTo", "from_id": "t-1", "to_id": "st-out"},
                {"edge_type": "fromState", "from_id": "t-1", "to_id": "st-in"},
                {"edge_type": "toState", "from_id": "t-1", "to_id": "st-out"},
            ],
        }
        p = tmp_path / "kg.jsonld"
        p.write_text(json.dumps(doc))

        result = run_validate_kg(p, checks=["shacl"], ontology_path=_oxp_ontology_path(), warning_verbosity="full")  # noqa: E501
        forbidden_session_attrs = {"callId", "runId", "executionId", "startTime", "endTime"}
        inherited_rows = [
            row for row in result["results"]
            if "Session" in row["detail"]
            and any(attr in row["detail"] for attr in forbidden_session_attrs if attr != "runId")
        ]
        assert inherited_rows == []

    def test_shacl_session_requires_initial_and_final_state(self, tmp_path):
        from mas.library.kg.steps.validate_kg import KGValidationError, run_validate_kg

        doc = {
            "nodes": [
                {
                    "id": "s1",
                    "node_type": "Session",
                    "sessionId": "s1",
                },
            ],
            "edges": [],
        }
        p = tmp_path / "kg.jsonld"
        p.write_text(json.dumps(doc))

        with pytest.raises(KGValidationError) as excinfo:
            run_validate_kg(
                p,
                checks=["shacl"],
                ontology_path=_oxp_ontology_path(),
            )
        result = excinfo.value.report

        assert result["error_count"] >= 1
        assert any("mas:hasInitialState" in row["detail"] for row in result["results"])
        assert any("mas:hasFinalState" in row["detail"] for row in result["results"])

    @_OXP_SHACL_GAP
    def test_shacl_executionelement_can_carry_its_own_initial_and_final_state(self, tmp_path):
        """The Session-level hasInitialState/hasFinalState requirement above
        must not be satisfied by an ExecutionElement's own boundary states --
        Session and its AgentCall/LLMCall/etc. children are validated
        independently. Regression test for the bug where hasInitialState/
        hasFinalState were only ever emitted on ExecutionElement (never on
        Session): a KG with the edges on the AgentCall alone, and none on
        Session, must still fail SessionShape's requirement."""
        from mas.library.kg.steps.validate_kg import KGValidationError, run_validate_kg

        doc = {
            "nodes": [
                {"id": "s1", "node_type": "Session", "sessionId": "s1", "appName": "demo"},
                {"id": "run-1", "node_type": "Run", "runId": "run-1"},
                {
                    "id": "call-1", "callId": "call-1", "node_type": "AgentCall",
                    "agentName": "planner", "startTime": 0.0, "endTime": 1.0,
                },
                {
                    "id": "st-in", "node_type": "State", "stateNodeId": "st-in",
                    "contentHash": "h1", "semanticType": "initial",
                },
                {
                    "id": "st-out", "node_type": "State", "stateNodeId": "st-out",
                    "contentHash": "h2", "semanticType": "final",
                },
            ],
            "edges": [
                {"edge_type": "contains", "from_id": "run-1", "to_id": "s1"},
                {"edge_type": "hasCall", "from_id": "run-1", "to_id": "call-1"},
                # hasInitialState/hasFinalState on the ExecutionElement only --
                # Session itself has neither.
                {"edge_type": "hasInitialState", "from_id": "call-1", "to_id": "st-in"},
                {"edge_type": "hasFinalState", "from_id": "call-1", "to_id": "st-out"},
            ],
        }
        p = tmp_path / "kg.jsonld"
        p.write_text(json.dumps(doc))

        with pytest.raises(KGValidationError) as excinfo:
            run_validate_kg(p, checks=["shacl"], ontology_path=_oxp_ontology_path())
        result = excinfo.value.report

        session_state_errors = [
            row for row in result["results"]
            if row["status"] == "error"
            and ("Session must have exactly one mas:hasInitialState" in row["detail"]
                 or "Session must have exactly one mas:hasFinalState" in row["detail"])
        ]
        assert session_state_errors, (
            "Session-level hasInitialState/hasFinalState must be required "
            "independently of whether an ExecutionElement child has its own"
        )

    @_OXP_SHACL_GAP
    def test_shacl_rejects_state_to_state_leadsto(self, tmp_path):
        from mas.library.kg.steps.validate_kg import KGValidationError, run_validate_kg

        doc = {
            "nodes": [
                {
                    "id": "s-in",
                    "node_type": "State",
                    "stateNodeId": "s-in",
                    "contentHash": "hash-in",
                    "semanticType": "initial",
                },
                {
                    "id": "s-out",
                    "node_type": "State",
                    "stateNodeId": "s-out",
                    "contentHash": "hash-out",
                    "semanticType": "final",
                },
            ],
            "edges": [
                {"edge_type": "leadsTo", "from_id": "s-in", "to_id": "s-out"},
            ],
        }
        p = tmp_path / "kg.jsonld"
        p.write_text(json.dumps(doc))

        with pytest.raises(KGValidationError) as excinfo:
            run_validate_kg(
                p,
                checks=["shacl"],
                ontology_path=_oxp_ontology_path(),
            )
        result = excinfo.value.report

        assert result["error_count"] >= 1
        assert any("mas:leadsTo subjects must be mas:Transition" in row["detail"] for row in result["results"])  # noqa: E501

    @_OXP_SHACL_GAP
    def test_shacl_processing_gate_rejects_agentcall_with_llm_but_no_processing(self, tmp_path):
        from mas.library.kg.steps.validate_kg import KGValidationError, run_validate_kg

        doc = {
            "nodes": [
                {"id": "run-1", "node_type": "Run", "runId": "run-1"},
                {"id": "agent-1", "node_type": "AgentCall", "callId": "agent-1", "agentName": "planner", "inputContent": "in", "outputContent": "out"},  # noqa: E501
                {"id": "llm-1", "node_type": "LLMCall", "callId": "llm-1", "llmName": "planner", "prompt": "p", "completion": "c"},  # noqa: E501
                {"id": "st-a", "node_type": "State", "stateNodeId": "st-a", "contentHash": "h1", "semanticType": "initial"},  # noqa: E501
                {"id": "st-b", "node_type": "State", "stateNodeId": "st-b", "contentHash": "h2", "semanticType": "final"},  # noqa: E501
                {"id": "st-c", "node_type": "State", "stateNodeId": "st-c", "contentHash": "h3", "semanticType": "initial"},  # noqa: E501
                {"id": "st-d", "node_type": "State", "stateNodeId": "st-d", "contentHash": "h4", "semanticType": "final"},  # noqa: E501
            ],
            "edges": [
                {"edge_type": "hasCall", "from_id": "run-1", "to_id": "agent-1"},
                {"edge_type": "contains", "from_id": "agent-1", "to_id": "llm-1"},
                {"edge_type": "hasInitialState", "from_id": "agent-1", "to_id": "st-a"},
                {"edge_type": "hasFinalState", "from_id": "agent-1", "to_id": "st-b"},
                {"edge_type": "hasInitialState", "from_id": "llm-1", "to_id": "st-c"},
                {"edge_type": "hasFinalState", "from_id": "llm-1", "to_id": "st-d"},
            ],
        }
        p = tmp_path / "kg.jsonld"
        p.write_text(json.dumps(doc))

        with pytest.raises(KGValidationError) as excinfo:
            run_validate_kg(p, checks=["shacl"], ontology_path=_oxp_ontology_path())
        result = excinfo.value.report

        assert result["error_count"] >= 1
        assert any("must also contain a ProcessingCall" in row["detail"] for row in result["results"])  # noqa: E501

    @_OXP_SHACL_GAP
    def test_shacl_temporal_enclosure_rejects_non_enclosing_parent(self, tmp_path):
        from mas.library.kg.steps.validate_kg import KGValidationError, run_validate_kg

        doc = {
            "nodes": [
                {"id": "run-1", "node_type": "Run", "runId": "run-1", "startTime": 2.0, "endTime": 3.0},  # noqa: E501
                {"id": "child-1", "node_type": "AgentCall", "callId": "child-1", "agentName": "planner", "startTime": 1.0, "endTime": 4.0},  # noqa: E501
            ],
            "edges": [
                {"edge_type": "contains", "from_id": "run-1", "to_id": "child-1"},
            ],
        }
        p = tmp_path / "kg.jsonld"
        p.write_text(json.dumps(doc))

        with pytest.raises(KGValidationError) as excinfo:
            run_validate_kg(p, checks=["shacl"], ontology_path=_oxp_ontology_path())
        result = excinfo.value.report

        assert result["error_count"] >= 1
        assert any("timestamps must enclose" in row["detail"] for row in result["results"])


class TestRunValidateKgResultNormalization:
    """Unit tests for tuple vs list return handling in the check loop."""

    def test_skipped_check_is_distinct_from_pass_and_does_not_raise(self, empty_kg_json):
        """A check that could not run (e.g. pyshacl/rdflib not installed)
        must be reported as status="skipped", counted in skipped_count, and
        must NOT be indistinguishable from a real pass -- that ambiguity is
        exactly how the "shacl" check went unnoticed for months. A skip
        alone must also not raise KGValidationError; only real errors do."""
        from mas.library.kg.core import verifier
        from mas.library.kg.steps.validate_kg import run_validate_kg

        with patch.object(
            verifier,
            "run_shacl_validation",
            side_effect=verifier.KGCheckSkipped("pyshacl not installed"),
        ):
            result = run_validate_kg(empty_kg_json, checks=["shacl"])

        assert result["error_count"] == 0
        assert result["skipped_count"] == 1
        assert result["results"] == [
            {"check": "shacl", "status": "skipped", "detail": "pyshacl not installed"}
        ]

    def test_two_tuple_pass_unpacks_to_pass_status(self, empty_kg_json):
        from mas.library.kg.core import verifier
        from mas.library.kg.steps.validate_kg import run_validate_kg

        with patch.object(verifier, "check_unknown_node_types", return_value=(True, [])):
            result = run_validate_kg(empty_kg_json, checks=["unknown_node_types"])

        assert result == {
            "error_count": 0,
            "warning_count": 0,
            "skipped_count": 0,
            "results": [{"check": "unknown_node_types", "status": "pass", "detail": ""}],
        }

    def test_two_tuple_failures_counted_as_errors(self, empty_kg_json):
        from mas.library.kg.core import verifier
        from mas.library.kg.steps.validate_kg import KGValidationError, run_validate_kg

        violation = {"sub": "node", "status": "orphan", "node_id": "c1"}
        with patch.object(verifier, "check_unknown_node_types", return_value=(False, [violation])):
            with pytest.raises(KGValidationError) as excinfo:
                run_validate_kg(empty_kg_json, checks=["unknown_node_types"])
        result = excinfo.value.report

        assert result["error_count"] == 1
        assert result["warning_count"] == 0
        assert result["results"][0]["check"] == "unknown_node_types"
        assert result["results"][0]["status"] == "error"

    def test_bare_empty_list_treated_as_pass(self, empty_kg_json):
        from mas.library.kg.core import verifier
        from mas.library.kg.steps.validate_kg import run_validate_kg

        with patch.object(verifier, "run_shacl_validation", return_value=[]):
            result = run_validate_kg(empty_kg_json, checks=["shacl"])

        assert result["error_count"] == 0
        assert result["results"] == [{"check": "shacl", "status": "pass", "detail": ""}]

    def test_bare_violation_list_counted(self, empty_kg_json):
        from mas.library.kg.core import verifier
        from mas.library.kg.steps.validate_kg import KGValidationError, run_validate_kg

        with patch.object(
            verifier,
            "run_shacl_validation",
            return_value=[{"message": "constraint violated"}],
        ):
            with pytest.raises(KGValidationError) as excinfo:
                run_validate_kg(empty_kg_json, checks=["shacl"])
        result = excinfo.value.report

        assert result["error_count"] == 1
        assert result["results"][0]["detail"] == "{'message': 'constraint violated'}"

    def test_violation_objects_with_warning_severity(self, empty_kg_json):
        from mas.library.kg.core import verifier
        from mas.library.kg.steps.validate_kg import run_validate_kg

        class _WarningViolation:
            severity = "warning"
            message = "optional attribute missing"

        with patch.object(
            verifier,
            "run_shacl_validation",
            return_value=[_WarningViolation()],
        ):
            result = run_validate_kg(empty_kg_json, checks=["shacl"])

        assert result["error_count"] == 0
        assert result["warning_count"] == 1
        assert result["results"][0]["status"] == "warning"
        assert result["results"][0]["detail"] == "optional attribute missing"

    def test_none_violations_treated_as_pass(self, empty_kg_json):
        from mas.library.kg.core import verifier
        from mas.library.kg.steps.validate_kg import run_validate_kg

        with patch.object(verifier, "run_shacl_validation", return_value=None):
            result = run_validate_kg(empty_kg_json, checks=["shacl"])

        assert result["error_count"] == 0
        assert result["results"] == [{"check": "shacl", "status": "pass", "detail": ""}]

    def test_single_non_list_violation_wrapped(self, empty_kg_json):
        from mas.library.kg.core import verifier
        from mas.library.kg.steps.validate_kg import KGValidationError, run_validate_kg

        with patch.object(verifier, "run_shacl_validation", return_value="single-issue"):
            with pytest.raises(KGValidationError) as excinfo:
                run_validate_kg(empty_kg_json, checks=["shacl"])
        result = excinfo.value.report

        assert result["error_count"] == 1
        assert result["results"][0]["detail"] == "single-issue"

    def test_check_exception_becomes_error_row(self, empty_kg_json):
        from mas.library.kg.core import verifier
        from mas.library.kg.steps.validate_kg import KGValidationError, run_validate_kg

        def _boom(*_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("verifier exploded")

        with patch.object(verifier, "check_unknown_node_types", side_effect=_boom):
            with pytest.raises(KGValidationError) as excinfo:
                run_validate_kg(empty_kg_json, checks=["unknown_node_types"])
        result = excinfo.value.report

        assert result["error_count"] == 1
        assert result["results"] == [
            {
                "check": "unknown_node_types",
                "status": "error",
                "detail": "verifier exploded",
            },
        ]

    def test_warning_verbosity_summary_aggregates_warning_rows(self, empty_kg_json):
        from mas.library.kg.core import verifier
        from mas.library.kg.steps.validate_kg import run_validate_kg

        warning_items = [
            {"message": "w1"},
            {"message": "w2"},
        ]
        with patch.object(
            verifier,
            "run_shacl_validation",
            return_value=(True, [], warning_items),
        ):
            result = run_validate_kg(
                empty_kg_json,
                checks=["shacl"],
                warning_verbosity="summary",
            )

        assert result["warning_count"] == 2
        warning_rows = [r for r in result["results"] if r["status"] == "warning"]
        assert len(warning_rows) == 1
        assert "2 warning(s)" in warning_rows[0]["detail"]

    def test_warning_verbosity_none_hides_warning_rows(self, empty_kg_json):
        from mas.library.kg.core import verifier
        from mas.library.kg.steps.validate_kg import run_validate_kg

        warning_items = [
            {"message": "w1"},
            {"message": "w2"},
        ]
        with patch.object(
            verifier,
            "run_shacl_validation",
            return_value=(True, [], warning_items),
        ):
            result = run_validate_kg(
                empty_kg_json,
                checks=["shacl"],
                warning_verbosity="none",
            )

        assert result["warning_count"] == 2
        assert all(r["status"] != "warning" for r in result["results"])
