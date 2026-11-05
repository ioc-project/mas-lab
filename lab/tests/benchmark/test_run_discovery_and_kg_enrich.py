#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from pathlib import Path

from mas.lab.benchmark.schedule.run_discovery import discover_benchmark_runs
from mas.lab.plots.kg_adapter import (
    _enrich_kg_plot_data,
    kg_to_call_records,
    kg_to_events,
)


def test_discover_benchmark_runs(tmp_path: Path) -> None:
    base = tmp_path / "exp"
    run_dir = base / "full" / "item1" / "r1"
    run_dir.mkdir(parents=True)
    (base / "data" / "plot-x").mkdir(parents=True)

    runs = discover_benchmark_runs(base)
    assert len(runs) == 1
    assert runs[0].scenario == "full"
    assert runs[0].test == "item1"
    assert runs[0].run == "r1"
    assert runs[0].path == run_dir.resolve()


def test_kg_enrich_synthesizes_agents_and_cpr() -> None:
    kg = {
        "nodes": [
            {
                "node_type": "AgentCall",
                "callId": "root-exec",
                "agentId": "planner",
                "startTime": 1_000_000_000.0,
                "endTime": 1_000_000_100.0,
            },
            {
                "node_type": "LLMCall",
                "callId": "llm-a",
                "agentId": "flights",
                "parentCallId": "root-exec",
                "modelName": "gpt-test",
                "startTime": 1_000_000_010.0,
                "endTime": 1_000_000_020.0,
            },
            {
                "node_type": "ContextContribution",
                "id": "cpr-1",
                "agentId": "flights",
                "timestamp": 1_000_000_010.0,
                "source": "context/system",
                "content": "hello",
                "tokenEstimate": 3,
            },
        ],
        "edges": [
            {
                "edge_type": "contributesTo",
                "from_id": "cpr-1",
                "to_id": "llm-a",
            },
        ],
    }
    records = kg_to_call_records(kg)
    events = kg_to_events(kg)
    records, events = _enrich_kg_plot_data(kg, records, events)

    agent_ids = {r["agent_id"] for r in records if r["call_type"] == "AgentCall"}
    assert "flights" in agent_ids
    assert max(r["end_ts"] for r in records) < 200.0
    cpr = [e for e in events if e.get("kind") == "context_part_contributed"]
    assert len(cpr) == 1
    assert cpr[0].get("llm_call_id") == "llm-a"


def test_kg_enrich_is_idempotent_on_already_synthesized_cpr_events() -> None:
    """Regression: _enrich_kg_plot_data must not double-count context_part_contributed
    events if called again on data that already carries them -- e.g. if a caller
    mistakenly composes it with _kg_to_plot_inputs, which already synthesizes CPR
    events internally."""
    kg = {
        "nodes": [
            {
                "node_type": "AgentCall",
                "callId": "root-exec",
                "agentId": "planner",
                "startTime": 1_000_000_000.0,
                "endTime": 1_000_000_100.0,
            },
            {
                "node_type": "LLMCall",
                "callId": "llm-a",
                "agentId": "flights",
                "parentCallId": "root-exec",
                "modelName": "gpt-test",
                "startTime": 1_000_000_010.0,
                "endTime": 1_000_000_020.0,
            },
            {
                "node_type": "ContextContribution",
                "id": "cpr-1",
                "agentId": "flights",
                "timestamp": 1_000_000_010.0,
                "source": "context/system",
                "content": "hello",
                "tokenEstimate": 3,
            },
        ],
        "edges": [
            {
                "edge_type": "contributesTo",
                "from_id": "cpr-1",
                "to_id": "llm-a",
            },
        ],
    }
    records = kg_to_call_records(kg)
    events = kg_to_events(kg)
    records, events = _enrich_kg_plot_data(kg, records, events)
    # Simulate an accidental re-enrichment of already-enriched data.
    records, events = _enrich_kg_plot_data(kg, records, events)

    cpr = [e for e in events if e.get("kind") == "context_part_contributed"]
    assert len(cpr) == 1
