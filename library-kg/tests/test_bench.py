#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Benchmark-adapter tests — skipped unless the bench framework is installed."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from mas.library.kg.steps import compare_kg as compare_kg_mod


def _has_bench() -> bool:
    try:
        return importlib.util.find_spec("mas.lab.benchmark.pipeline") is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


_HAS_BENCH = _has_bench()
requires_bench = pytest.mark.skipif(not _HAS_BENCH, reason="bench extra not installed")


def test_core_library_does_not_require_bench():
    import mas.library.kg  # noqa: F401

    assert "mas.library.kg.bench" not in __import__("sys").modules or _HAS_BENCH


@requires_bench
def test_compare_kg_adapter_registers():
    from mas.lab.benchmark.pipeline import PipelineStep

    from mas.library.kg.bench import CompareKgStep

    assert issubclass(CompareKgStep, PipelineStep)
    assert CompareKgStep.type == "compare_kg"


class _Ctx:
    def __init__(self, output_dir: Path, step_outputs: dict | None = None):
        self.output_dir = output_dir
        self.step_outputs = step_outputs or {}

    def get_step_output_dir(self, step_name: str) -> Path:
        d = self.output_dir / "data" / step_name
        d.mkdir(parents=True, exist_ok=True)
        return d


def _write_kg(path: Path, agents=("a",)) -> Path:
    nodes = [{"id": "sess", "node_type": "Session"}]
    for agent in agents:
        nodes.append({"id": f"ac-{agent}", "node_type": "AgentCall", "agentId": agent})
    path.write_text(json.dumps({"nodes": nodes, "edges": []}))
    return path


def test_node_type_helpers_cover_alt_keys():
    assert compare_kg_mod._node_type({"@type": "https://ex.example/kg#Session"}) == "Session"
    assert compare_kg_mod._node_type({"type": "https://ex.example/AgentCall"}) == "AgentCall"
    assert compare_kg_mod._node_type({"nodeType": "Tool"}) == "Tool"
    assert compare_kg_mod._node_type({}) == "unknown"
    assert compare_kg_mod._edge_end({"source": "a"}, "from_id", "source", "from") == "a"
    assert compare_kg_mod._edge_end({"from": "b"}, "from_id", "source", "from") == "b"
    assert compare_kg_mod._edge_end({}, "from_id", "source") == ""
    doc = {
        "nodes": [{"id": "n1", "node_type": "Agent"}, {"id": "", "node_type": "Agent"}],
        "edges": [{"source": "n1", "target": "n1"}],
    }
    assert compare_kg_mod._filter_doc(doc, None) is doc
    filtered = compare_kg_mod._filter_doc(doc, ["Agent"])
    assert filtered["nodes"] == [
        {"id": "n1", "node_type": "Agent"},
        {"id": "", "node_type": "Agent"},
    ]
    assert filtered["edges"] == [{"source": "n1", "target": "n1"}]


@requires_bench
@pytest.mark.asyncio
async def test_compare_kg_step_node_types_and_deps(tmp_path):
    from mas.lab.benchmark.pipeline import StepOutput

    from mas.library.kg.bench import CompareKgStep

    ref = _write_kg(tmp_path / "ref.json")
    cand = _write_kg(tmp_path / "cand.json")
    cand_doc = json.loads(cand.read_text())
    cand_doc["nodes"].append({"id": "ann-1", "node_type": "CallAnnotation"})
    cand.write_text(json.dumps(cand_doc))
    ctx = _Ctx(
        tmp_path / "out",
        step_outputs={
            "ref-step": StepOutput(data={"artifact": "kg", "kg_path": str(ref)}),
            "cand-step": StepOutput(data={"artifact": "kg_otel", "kg_path": str(cand)}),
            "empty": StepOutput(data={}),
        },
    )
    step = CompareKgStep(
        "compare",
        {"node_types": ["Session", "AgentCall"]},
        depends_on=["missing", "empty", "ref-step", "cand-step"],
    )
    out = await step.execute(ctx)
    assert out.data["passed"] is True
    report = json.loads(Path(out.data["report_path"]).read_text())
    assert report["node_types"] == ["Session", "AgentCall"]


@requires_bench
@pytest.mark.asyncio
async def test_compare_kg_step_non_list_node_types_and_empty_fail(tmp_path):
    from mas.library.kg.bench import CompareKgStep

    ref = _write_kg(tmp_path / "ref.json")
    cand = _write_kg(tmp_path / "cand.json")
    ctx = _Ctx(tmp_path / "out")
    ok = CompareKgStep(
        "compare",
        {
            "reference_kg_path": str(ref),
            "candidate_kg_path": str(cand),
            "node_types": "AgentCall",
        },
    )
    out = await ok.execute(ctx)
    assert out.data["passed"] is True

    empty_ref = tmp_path / "empty-ref.json"
    empty_cand = tmp_path / "empty-cand.json"
    empty_doc = {"nodes": [{"id": "ann", "node_type": "CallAnnotation"}], "edges": []}
    empty_ref.write_text(json.dumps(empty_doc))
    empty_cand.write_text(json.dumps(empty_doc))
    failing = CompareKgStep(
        "compare-empty",
        {
            "reference_kg_path": str(empty_ref),
            "candidate_kg_path": str(empty_cand),
            "node_types": ["Agent"],
            "fail_on_error": True,
        },
    )
    with pytest.raises(ValueError, match="empty graph"):
        await failing.execute(ctx)

    missing = CompareKgStep("compare-missing", {})
    with pytest.raises(ValueError, match="need candidate \\+ reference"):
        await missing.execute(ctx)
