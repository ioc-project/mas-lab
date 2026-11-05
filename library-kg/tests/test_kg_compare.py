#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for mas.library.kg.core.compare."""

import json

import pytest

from mas.library.kg.core.compare import (
    check_agent_coverage,
    check_call_depth,
    check_delegation_topology,
    check_edge_distribution,
    check_element_diff,
    check_node_distribution,
    check_tool_coverage,
    compare_kg,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_kg(agents=("a",), tools=(), extra_nodes=(), delegation_edges=()):
    nodes = [
        {"id": "sess", "node_type": "Session"},
    ]
    edges = []
    for i, agent in enumerate(agents):
        nodes.append(
            {"id": f"ac-{agent}", "node_type": "AgentCall", "agentId": agent, "startTime": float(i)}
        )
    for tool in tools:
        nodes.append(
            {
                "id": f"tc-{tool}",
                "node_type": "ToolCall",
                "agentId": agents[0] if agents else "unknown",
                "toolName": tool,
            }
        )
    for n in extra_nodes:
        nodes.append(n)
    for src_agent, tgt_agent in delegation_edges:
        # callsAgent edge from AgentCall to Agent node
        nodes.append({"id": f"agent-{tgt_agent}", "node_type": "Agent", "agentId": tgt_agent})
        edges.append(
            {"from_id": f"ac-{src_agent}", "to_id": f"agent-{tgt_agent}", "edge_type": "callsAgent"}
        )
    return {"nodes": nodes, "edges": edges}


# ---------------------------------------------------------------------------
# check_agent_coverage
# ---------------------------------------------------------------------------


class TestCheckAgentCoverage:
    def test_pass_when_same(self):
        ref = _make_kg(agents=("a", "b"))
        cand = _make_kg(agents=("a", "b"))
        result = check_agent_coverage(cand["nodes"], ref["nodes"])
        assert result["passed"] is True
        assert result["missing"] == []

    def test_fail_when_missing(self):
        ref = _make_kg(agents=("a", "b"))
        cand = _make_kg(agents=("a",))
        result = check_agent_coverage(cand["nodes"], ref["nodes"])
        assert result["passed"] is False
        assert "b" in result["missing"]

    def test_extra_agents_still_pass(self):
        ref = _make_kg(agents=("a",))
        cand = _make_kg(agents=("a", "extra"))
        result = check_agent_coverage(cand["nodes"], ref["nodes"])
        assert result["passed"] is True
        assert "extra" in result["extra"]


# ---------------------------------------------------------------------------
# check_node_distribution
# ---------------------------------------------------------------------------


class TestCheckNodeDistribution:
    def test_pass_when_equal(self):
        ref = _make_kg(agents=("a",), tools=("search",))
        cand = _make_kg(agents=("a",), tools=("search",))
        result = check_node_distribution(cand["nodes"], ref["nodes"])
        assert result["passed"] is True

    def test_fail_when_deficit(self):
        ref = _make_kg(agents=("a", "b"), tools=("t1", "t2"))
        cand = _make_kg(agents=("a",), tools=("t1",))
        result = check_node_distribution(cand["nodes"], ref["nodes"])
        assert result["passed"] is False
        assert "AgentCall" in result["deficits"]

    def test_more_is_ok(self):
        ref = _make_kg(agents=("a",))
        cand = _make_kg(agents=("a", "b", "c"))
        result = check_node_distribution(cand["nodes"], ref["nodes"])
        assert result["passed"] is True


# ---------------------------------------------------------------------------
# check_edge_distribution
# ---------------------------------------------------------------------------


class TestCheckEdgeDistribution:
    def test_pass_when_equal(self):
        kg = _make_kg(agents=("a", "b"), delegation_edges=[("a", "b")])
        result = check_edge_distribution(kg["edges"], kg["edges"])
        assert result["passed"] is True

    def test_fail_when_missing_edge_type(self):
        ref = _make_kg(agents=("a", "b"), delegation_edges=[("a", "b")])
        cand = _make_kg(agents=("a",))
        result = check_edge_distribution(cand["edges"], ref["edges"])
        assert result["passed"] is False


# ---------------------------------------------------------------------------
# check_call_depth
# ---------------------------------------------------------------------------


class TestCheckCallDepth:
    def test_pass_equal_depth(self):
        kg = {
            "nodes": [
                {"id": "a", "node_type": "AgentCall"},
                {"id": "b", "node_type": "LLMCall"},
            ],
            "edges": [{"from_id": "a", "to_id": "b", "edge_type": "contains"}],
        }
        result = check_call_depth(kg["nodes"], kg["edges"], kg["nodes"], kg["edges"])
        assert result["passed"] is True
        assert result["candidate_depth"] >= result["reference_depth"]

    def test_fail_shallower_depth(self):
        deep = {
            "nodes": [
                {"id": "a", "node_type": "AgentCall"},
                {"id": "b", "node_type": "AgentCall"},
                {"id": "c", "node_type": "LLMCall"},
            ],
            "edges": [
                {"from_id": "a", "to_id": "b", "edge_type": "contains"},
                {"from_id": "b", "to_id": "c", "edge_type": "contains"},
            ],
        }
        shallow = {
            "nodes": [{"id": "a", "node_type": "AgentCall"}],
            "edges": [],
        }
        result = check_call_depth(shallow["nodes"], shallow["edges"], deep["nodes"], deep["edges"])
        assert result["passed"] is False


# ---------------------------------------------------------------------------
# check_tool_coverage
# ---------------------------------------------------------------------------


class TestCheckToolCoverage:
    def test_pass_all_tools_present(self):
        ref = _make_kg(tools=("search", "lookup"))
        cand = _make_kg(tools=("search", "lookup", "extra"))
        result = check_tool_coverage(cand["nodes"], ref["nodes"])
        assert result["passed"] is True
        assert result["missing"] == []

    def test_fail_missing_tool(self):
        ref = _make_kg(tools=("search", "lookup"))
        cand = _make_kg(tools=("search",))
        result = check_tool_coverage(cand["nodes"], ref["nodes"])
        assert result["passed"] is False
        assert "lookup" in result["missing"]


# ---------------------------------------------------------------------------
# check_delegation_topology
# ---------------------------------------------------------------------------


class TestCheckDelegationTopology:
    def test_pass_matching_topology(self):
        ref = _make_kg(agents=("a", "b"), delegation_edges=[("a", "b")])
        cand = _make_kg(agents=("a", "b"), delegation_edges=[("a", "b")])
        result = check_delegation_topology(cand["nodes"], cand["edges"], ref["nodes"], ref["edges"])
        assert result["passed"] is True

    def test_fail_missing_delegation(self):
        ref = _make_kg(agents=("a", "b"), delegation_edges=[("a", "b")])
        cand = _make_kg(agents=("a", "b"))
        result = check_delegation_topology(cand["nodes"], cand["edges"], ref["nodes"], ref["edges"])
        assert result["passed"] is False
        assert len(result["missing"]) > 0


# ---------------------------------------------------------------------------
# check_element_diff
# ---------------------------------------------------------------------------


class TestCheckElementDiff:
    def test_identical_kgs(self):
        kg = _make_kg(agents=("a",), tools=("t1",))
        result = check_element_diff(kg["nodes"], kg["edges"], kg["nodes"], kg["edges"])
        assert result["passed"] is True

    def test_detects_missing_node(self):
        ref = _make_kg(agents=("a", "b"))
        cand = _make_kg(agents=("a",))
        result = check_element_diff(cand["nodes"], cand["edges"], ref["nodes"], ref["edges"])
        assert result["passed"] is False
        assert len(result["missing_nodes"]) > 0


# ---------------------------------------------------------------------------
# compare_kg — top-level
# ---------------------------------------------------------------------------


class TestCompareKG:
    def test_identical_passes(self):
        kg = _make_kg(agents=("a", "b"), tools=("t1",))
        result = compare_kg(kg, kg)
        assert result.passed is True
        assert result.summary["failed"] == 0

    def test_missing_agent_fails(self):
        ref = _make_kg(agents=("a", "b"))
        cand = _make_kg(agents=("a",))
        result = compare_kg(cand, ref)
        assert result.passed is False

    def test_strict_mode(self):
        ref = _make_kg(agents=("a",))
        cand = _make_kg(agents=("a", "extra"))
        result_lenient = compare_kg(cand, ref, strict=False)
        result_strict = compare_kg(cand, ref, strict=True)
        assert result_lenient.passed is True
        assert result_strict.passed is False

    def test_strict_nodes_allows_extra_when_off(self):
        ref = _make_kg(agents=("a",))
        cand = _make_kg(agents=("a", "extra"))
        result = compare_kg(
            cand,
            ref,
            strict=True,
            strict_nodes=False,
            strict_edges=True,
            strict_node_attributes=True,
            strict_edge_attributes=True,
        )
        node_check = next(c for c in result.checks if c["name"] == "node_distribution")
        assert node_check["extras"]
        assert result.passed is True

    def test_strict_node_attributes_rejects_extra_keys(self):
        ref = _make_kg(agents=("a",))
        cand = _make_kg(agents=("a",))
        cand["nodes"][1]["kindBase"] = "execution"
        result = compare_kg(cand, ref, strict=True)
        attr = next(c for c in result.checks if c["name"] == "node_attributes")
        assert "kindBase" in attr["extra"]
        assert result.passed is False
        result_open = compare_kg(cand, ref, strict=True, strict_node_attributes=False)
        assert result_open.passed is True

    def test_result_has_all_checks(self):
        kg = _make_kg(agents=("a",))
        result = compare_kg(kg, kg)
        check_names = {c["name"] for c in result.checks}
        assert "agent_coverage" in check_names
        assert "node_distribution" in check_names
        assert "edge_distribution" in check_names
        assert "call_depth" in check_names
        assert "tool_coverage" in check_names
        assert "delegation_topology" in check_names
        assert "node_attributes" in check_names
        assert "edge_attributes" in check_names
        assert "element_level_diff" in check_names

    def test_to_dict(self):
        kg = _make_kg(agents=("a",))
        result = compare_kg(kg, kg)
        d = result.to_dict()
        assert "passed" in d
        assert "checks" in d
        assert "summary" in d

    def test_stats_correct(self):
        ref = _make_kg(agents=("a", "b"))
        cand = _make_kg(agents=("a",))
        result = compare_kg(cand, ref)
        assert result.reference_stats["nodes"] == len(ref["nodes"])
        assert result.candidate_stats["nodes"] == len(cand["nodes"])
        assert "a" in result.reference_stats["agents"]
        assert "b" in result.reference_stats["agents"]

    def test_node_types_filter_ignores_other_classes(self, tmp_path):
        from mas.library.kg.steps.compare_kg import run_compare_kg

        ref = _make_kg(agents=("a",))
        cand = _make_kg(agents=("a",))
        cand["nodes"].append({"id": "ann-1", "node_type": "CallAnnotation"})
        rp = tmp_path / "ref.json"
        cp = tmp_path / "cand.json"
        rp.write_text(json.dumps(ref))
        cp.write_text(json.dumps(cand))
        report = run_compare_kg(cp, rp, node_types=["Session", "Agent", "AgentCall"])
        assert report["passed"] is True
        assert report["node_types"] == ["Session", "Agent", "AgentCall"]
        assert report["candidate_stats"]["nodes"] == report["reference_stats"]["nodes"]

    def test_node_types_filter_reads_alt_type_and_edge_keys(self, tmp_path):
        from mas.library.kg.steps.compare_kg import run_compare_kg

        def _doc():
            return {
                "nodes": [
                    {"id": "sess", "@type": "https://ex.example/kg#Session"},
                    {"id": "ac-a", "type": "https://ex.example/AgentCall"},
                    {"id": "ann-1", "nodeType": "CallAnnotation"},
                    {"id": "", "node_type": "AgentCall"},
                ],
                "edges": [
                    {"source": "sess", "target": "ac-a", "edge_type": "contains"},
                    {"from": "ac-a", "to": "ann-1", "edge_type": "annotates"},
                    {"from_id": "missing", "to_id": "ac-a", "edge_type": "contains"},
                ],
            }

        rp = tmp_path / "ref.json"
        cp = tmp_path / "cand.json"
        rp.write_text(json.dumps(_doc()))
        cp.write_text(json.dumps(_doc()))
        report = run_compare_kg(cp, rp, node_types=["Session", "AgentCall"])
        assert report["passed"] is True
        assert report["reference_stats"]["nodes"] == 3
        assert report["candidate_stats"]["nodes"] == 3


# ---------------------------------------------------------------------------
# run_compare_kg step integration
# ---------------------------------------------------------------------------


class TestRunCompareKGStep:
    def test_step_returns_report(self, tmp_path):
        from mas.library.kg.steps.compare_kg import run_compare_kg

        kg = _make_kg(agents=("a", "b"))
        cand_path = tmp_path / "candidate.json"
        ref_path = tmp_path / "reference.json"
        cand_path.write_text(json.dumps(kg))
        ref_path.write_text(json.dumps(kg))

        report = run_compare_kg(cand_path, ref_path)
        assert report["passed"] is True
        assert "summary" in report

    def test_step_writes_output(self, tmp_path):
        from mas.library.kg.steps.compare_kg import run_compare_kg

        kg = _make_kg(agents=("a",))
        p = tmp_path / "kg.jsonld"
        p.write_text(json.dumps(kg))
        out = tmp_path / "report.json"
        run_compare_kg(p, p, output_path=out)
        assert out.exists()
        data = json.loads(out.read_text())
        assert "passed" in data

    def test_step_fail_on_error(self, tmp_path):
        from mas.library.kg.steps.compare_kg import run_compare_kg

        ref = _make_kg(agents=("a", "b"))
        cand = _make_kg(agents=("a",))
        rp = tmp_path / "ref.json"
        cp = tmp_path / "cand.json"
        rp.write_text(json.dumps(ref))
        cp.write_text(json.dumps(cand))
        with pytest.raises(RuntimeError, match="KG parity FAILED"):
            run_compare_kg(cp, rp, fail_on_error=True)

    def test_step_missing_file(self, tmp_path):
        from mas.library.kg.steps.compare_kg import run_compare_kg

        with pytest.raises(FileNotFoundError):
            run_compare_kg(tmp_path / "missing.json", tmp_path / "missing2.json")
