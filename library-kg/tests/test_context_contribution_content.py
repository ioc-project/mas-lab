# SPDX-License-Identifier: Apache-2.0
"""A ContextContribution is provenance of an LLM call's context, in SCOPE terms."""

from __future__ import annotations

import json

from mas.library.kg.pipeline import build_kg_document


def _events() -> list[dict]:
    return [
        {
            "kind": "context_part_contributed",
            "part_id": "p1",
            "agent_id": "cra",
            "source": "history",
            "access_mechanism": "inject",
            "token_estimate": 12,
            "parents": ["p0"],
            "content": "Patient unresponsive after near drowning in pool.",
            "retained": True,
            "llm_call_id": "llm-1",
            "timestamp": 0.5,
        },
        {"kind": "llm_call_start", "call_id": "llm-1", "agent_id": "cra", "timestamp": 1.0},
        {
            "kind": "llm_call_end",
            "call_id": "llm-1",
            "agent_id": "cra",
            "timestamp": 2.0,
            "output": "ok",
        },
    ]


def test_context_contribution_annotates_its_llm_call_with_scope_properties():
    doc = build_kg_document(_events(), run_id="test", categories="annotations")
    (cc,) = [n for n in doc["nodes"] if n.get("node_type") == "ContextContribution"]
    assert cc["partId"] == "p1"
    assert cc["contextSource"] == "history"
    assert cc["contributionMechanism"] == "inject"
    assert cc["tokenEstimate"] == 12
    assert cc["retained"] is True
    assert cc["eventKind"] == "context_part_contributed"
    assert cc["eventTime"] == 0.5
    llm = next(n for n in doc["nodes"] if n.get("node_type") == "LLMCall")
    assert [
        (e["edge_type"], e["from_id"], e["to_id"]) for e in doc["edges"] if e["from_id"] == cc["id"]
    ] == [("annotates", cc["id"], llm["id"])]
    assert not {"contributesTo", "derivedFrom"} & {e["edge_type"] for e in doc["edges"]}


def test_context_contribution_payload_keeps_the_text_and_parents():
    doc = build_kg_document(_events(), run_id="test", categories="annotations")
    (cc,) = [n for n in doc["nodes"] if n.get("node_type") == "ContextContribution"]
    payload = json.loads(cc["payload"])
    assert payload["content"].startswith("Patient unresponsive")
    assert payload["parents"] == ["p0"]
