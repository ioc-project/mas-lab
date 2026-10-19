#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Golden test for EXAMPLE.md — every offline command/output in the story.

If EXAMPLE.md and this test disagree, one of them is wrong.  The collector
round-trip (steps ③/④) needs Docker and is documented but not executed here.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from tests.conftest import requires_otel

EXAMPLE_EVENTS = (
    Path(__file__).resolve().parents[1] / "examples" / "travel-planner.events.jsonl"
)
INFRA = Path(__file__).resolve().parents[1] / "infra" / "local-otel.yaml"


@requires_otel
def test_story_step1_convert(tmp_path):
    from mas.library.telemetry.steps.convert import run_convert

    ss = run_convert(
        EXAMPLE_EVENTS,
        output_dir=tmp_path,
        output_filename="spans.jsonl",
        converter_profile="raw",
    )
    # 14 events → root + TaskCall + 3 agents + 2 routing + graph + ...
    assert ss.span_count == 10
    names = Counter(s["name"] for s in ss.spans)
    assert names["root"] == 1
    assert names["AgentCall"] == 3
    assert names["TaskCall"] == 1
    assert names["CallAnnotation"] == 2  # two routing hand-offs
    assert names["travel-planner.graph"] == 1

    # App name derived from the trace (not "mas-runtime").
    assert all(
        s["attributes"].get("application_id") == "travel-planner" for s in ss.spans
    )

    # The graph span carries all four agents.
    g = next(s for s in ss.spans if s["name"].endswith(".graph"))
    topo = json.loads(g["attributes"]["gen_ai.ioa.graph"])
    nodes = topo["nodes"]
    assert isinstance(nodes, dict)
    node_ids = set(nodes)
    assert node_ids == {
        "orchestrator",
        "moderator",
        "itinerary_agent",
        "budget_agent",
    }


@requires_otel
def test_story_step2_verify(tmp_path):
    from mas.library.telemetry.steps.convert import run_convert
    from mas.library.telemetry.steps.verify_spans import run_verify_spans

    run_convert(
        EXAMPLE_EVENTS,
        output_dir=tmp_path,
        output_filename="spans.jsonl",
        converter_profile="raw",
    )
    report = run_verify_spans(tmp_path / "spans.jsonl", spanspec_level="L3")
    assert report["ok"]  # no structural errors
    conf = report["spanspec"]["conformance"]
    assert conf["L2"] == "full"  # OXP-ready
    assert conf["L3"] in {"full", "passing"}


@requires_otel
def test_story_step3_push_via_infra_dry_run(tmp_path):
    from mas.library.telemetry.steps.convert import run_convert
    from mas.library.telemetry.steps.push_otlp import run_push_otlp

    run_convert(
        EXAMPLE_EVENTS,
        output_dir=tmp_path,
        output_filename="spans.jsonl",
        converter_profile="raw",
    )
    result = run_push_otlp(tmp_path / "spans.jsonl", infra=INFRA, dry_run=True)
    assert result["status"] == "dry-run"
    assert result["spans"] == 10
    assert "localhost:4318" in result["detail"]


@requires_otel
def test_story_step4_roundtrip_parity(tmp_path):
    # ④ (dump) needs a store; we simulate the round-trip by re-converting and
    # comparing — the same semantic parity the doc's `compare` command asserts.
    from mas.library.telemetry.steps.compare_spans import run_compare_spans
    from mas.library.telemetry.steps.convert import run_convert

    run_convert(
        EXAMPLE_EVENTS,
        output_dir=tmp_path,
        output_filename="a.jsonl",
        converter_profile="raw",
    )
    run_convert(
        EXAMPLE_EVENTS,
        output_dir=tmp_path,
        output_filename="b.jsonl",
        converter_profile="raw",
    )
    report = run_compare_spans(tmp_path / "a.jsonl", tmp_path / "b.jsonl", strict=True)
    assert report["passed"]
    assert report["summary"] == {"passed": 6, "failed": 0, "total_checks": 6}
