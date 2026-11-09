#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""
End-to-end test: demo worker topology + events feed animation pipeline.

Tests:
  1. POST /api/demos → demo worker created
  2. GET /api/demos/:id/topology → all node types present (user, agent, tool, llm)
  3. Tiered-layout band types covered
  4. Write mock events to UI feed → GET /api/demos/:id/events returns them
  5. Event fields match what _applyEvents expects (kind, agent_id, tool_name, model)
  6. Tool node IDs match between topology and event tool_name field
  7. LLM node ID derivation matches between backend and frontend helper

Run (from the repository root, with a demo server already running):
  python -m pytest lab/tests/test_demo_e2e.py

NOTE: This is an integration test requiring a running demo server.
Skipped in CI test suite as it requires external resources.
"""

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

# Skip this entire module - it's an integration test requiring a running server
pytestmark = pytest.mark.skip(reason="Integration test requiring running demo server")

BASE_URL = "http://localhost:8090"
REPO_ROOT = Path(__file__).resolve().parents[2]  # repository root


# ── helpers ──────────────────────────────────────────────────────────────────


def _req(method: str, path: str, body=None, timeout: int = 10):
    url = BASE_URL + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def check(cond: bool, msg: str) -> None:
    sym = "✓" if cond else "✗"
    print(f"  {sym}  {msg}")
    if not cond:
        raise AssertionError(msg)


# ── helper matching frontend _llmNodeId logic ─────────────────────────────────


def _llm_node_id(model: str) -> str:
    """Mirror of topology-viewer.js _llmNodeId()."""
    import re

    safe = re.sub(r"[^a-z0-9]", "_", model.lower())[:24]
    return "llm:" + safe


if __name__ == "__main__":
    # ── 1. Create demo worker ─────────────────────────────────────────────────────

    print("\n=== 1. Create demo worker ===")
    resp = _req("POST", "/api/demos", {"use_case": "trip-planner"})
    demo_id = resp.get("demo_id")
    check(bool(demo_id), f"demo_id present in response: {resp}")
    print(f"     demo_id = {demo_id}")

    # ── 2. Wait for demo to become available (poll job if async) ─────────────────

    print("\n=== 2. Wait for demo to become available ===")
    job_id = resp.get("job_id")
    if job_id:
        print(f"     async startup via job_id={job_id} — polling job status...")
        for i in range(30):
            try:
                job_status = _req("GET", f"/api/jobs/{job_id}")
                state = job_status.get("status")
                print(f"     attempt {i + 1}: job status={state}")
                if state in ("done", "success", "completed"):
                    # Extract demo_id from job result if needed
                    result = job_status.get("result") or {}
                    if result.get("demo_id"):
                        demo_id = result["demo_id"]
                    break
                if state in ("error", "failed"):
                    check(False, f"job failed: {job_status}")
                    break
            except Exception as e:
                print(f"     attempt {i + 1}: error {e}")
            time.sleep(0.8)

    # Now poll demo status itself
    for _ in range(15):
        try:
            status = _req("GET", f"/api/demos/{demo_id}/status")
            if status.get("available"):
                break
        except Exception:
            pass
        time.sleep(0.5)
    check(status.get("available"), f"demo available after wait: {status}")

    # ── 3. Verify topology ────────────────────────────────────────────────────────

    print("\n=== 3. Verify topology ===")
    topo = _req("GET", f"/api/demos/{demo_id}/topology")
    nodes = topo.get("nodes", [])
    edges = topo.get("edges", [])
    types = {n["type"] for n in nodes}

    check("user" in types, f"user node present (types={types})")
    check("agent" in types, "agent nodes present")
    check("tool" in types, "tool nodes present")
    check("llm" in types, "LLM nodes present")
    check(len(nodes) > 7, f"topology has >7 nodes, got {len(nodes)}")

    agent_ids = {n["id"] for n in nodes if n["type"] == "agent"}
    tool_ids = {n["id"] for n in nodes if n["type"] == "tool"}
    llm_ids = {n["id"] for n in nodes if n["type"] == "llm"}

    print(f"     agents: {sorted(agent_ids)}")
    print(f"     tools:  {sorted(tool_ids)}")
    print(f"     llms:   {sorted(llm_ids)}")

    # Verify entry agent is linked from user
    entry_agent = topo.get("entry_agent", "")
    check(bool(entry_agent), f"entry_agent set: {entry_agent}")
    agent_remote_sources = {e["from"] for e in edges if e.get("type") == "agent-remote"}
    check("user" in agent_remote_sources, "user→entry_agent edge present")

    # Verify each agent has at least an LLM edge
    agent_to_llm = {e["from"]: e["to"] for e in edges if e.get("type") == "llm"}
    check(len(agent_to_llm) > 0, f"LLM edges present ({len(agent_to_llm)})")

    # Verify each tool has at least one inbound agent edge
    tool_edge_targets = {e["to"] for e in edges if e.get("type") == "tool"}
    check(tool_ids.issubset(tool_edge_targets), "all tool nodes have inbound edges")

    # ── 4. Verify LLM node ID derivation matches frontend ────────────────────────

    print("\n=== 4. LLM node ID derivation ===")
    llm_nodes_with_models = [n for n in nodes if n["type"] == "agent" and n.get("llm_model")]
    for agent_node in llm_nodes_with_models[:2]:
        model = agent_node["llm_model"]
        expected_llm_id = _llm_node_id(model)
        check(expected_llm_id in llm_ids, f"LLM node '{expected_llm_id}' exists (model='{model}')")

    # ── 5. Simulate events and verify event format ────────────────────────────────

    print("\n=== 5. Simulate run + events feed ===")
    # Stop any existing run first to allow a fresh start
    existing_run_id = status.get("run_id")
    if status.get("running") and existing_run_id:
        print(f"     stopping existing run {existing_run_id} first...")
        try:
            _req("POST", f"/api/demos/{demo_id}/stop", {})
            time.sleep(0.5)
        except Exception as e:
            print(f"     stop error (ok): {e}")
    # Start a run
    run_resp = _req("POST", f"/api/demos/{demo_id}/run", {"scenario": "baseline"})
    run_id = run_resp.get("run_id")
    check(bool(run_id), f"run_id returned: {run_resp}")
    print(f"     run_id = {run_id}")

    # Find the UI feed path for this run
    # REPO_ROOT for controller module = mas-lab/src/mas/lab/
    ctrl_repo_root = REPO_ROOT / "mas-lab" / "src" / "mas_lab"
    feed_path = ctrl_repo_root / "output" / run_id / "logs" / "ui_feed.jsonl"
    feed_path.parent.mkdir(parents=True, exist_ok=True)

    # Minimal set of mock events mirroring observability_plugin emissions
    first_agent = sorted(agent_ids)[0]  # e.g. "backend"
    entry = entry_agent or first_agent
    first_tool = sorted(tool_ids)[0] if tool_ids else None
    first_llm_model = llm_nodes_with_models[0]["llm_model"] if llm_nodes_with_models else None

    mock_events = [
        {"kind": "execution_start", "agent_id": entry, "timestamp": time.time(), "call_id": "c1"},
        {
            "kind": "llm_call_start",
            "agent_id": entry,
            "timestamp": time.time() + 0.1,
            "call_id": "c2",
            "model": first_llm_model,
        },
        {
            "kind": "llm_call_end",
            "agent_id": entry,
            "timestamp": time.time() + 0.5,
            "call_id": "c2",
            "model": first_llm_model,
            "latency_ms": 400,
        },
    ]
    if first_tool:
        mock_events += [
            {
                "kind": "tool_call_start",
                "agent_id": entry,
                "tool_name": first_tool,
                "tool_category": "data",
                "timestamp": time.time() + 0.6,
                "call_id": "c3",
            },
            {
                "kind": "tool_call_end",
                "agent_id": entry,
                "tool_name": first_tool,
                "tool_category": "data",
                "timestamp": time.time() + 0.9,
                "call_id": "c3",
                "latency_ms": 300,
            },
        ]
    mock_events.append({"kind": "execution_end", "agent_id": entry, "timestamp": time.time() + 1.0, "call_id": "c1"})

    # Write events to feed
    with feed_path.open("w", encoding="utf-8") as fh:
        for ev in mock_events:
            fh.write(json.dumps(ev) + "\n")
    print(f"     wrote {len(mock_events)} mock events → {feed_path}")

    # Also write the run ID so the events endpoint finds it
    run_id_path = ctrl_repo_root / "output" / "current_run_id.txt"
    run_id_path.write_text(run_id, encoding="utf-8")

    # Poll events endpoint
    time.sleep(0.3)
    events_resp = _req("GET", f"/api/demos/{demo_id}/events")
    returned = events_resp if isinstance(events_resp, list) else events_resp.get("events", [])
    check(len(returned) >= len(mock_events), f"events returned ({len(returned)} >= {len(mock_events)})")

    # Verify required event fields
    for ev in returned:
        check("kind" in ev, f"event has 'kind' field: {ev}")
    kinds_returned = {e["kind"] for e in returned}
    print(f"     kinds returned: {sorted(kinds_returned)}")

    # ── 6. Verify _applyEvents coverage ──────────────────────────────────────────

    print("\n=== 6. _applyEvents field coverage ===")
    HANDLED_KINDS = {
        "execution_start",
        "execution_end",
        "llm_call_start",
        "llm_call_end",
        "tool_call_start",
        "tool_call_end",
        "llm_call",
        "audit",
        "object_upsert",
    }
    for ev in returned:
        k = ev.get("kind")
        if k in HANDLED_KINDS:
            if k in (
                "execution_start",
                "execution_end",
                "llm_call_start",
                "llm_call_end",
                "tool_call_start",
                "tool_call_end",
            ):
                check("agent_id" in ev, f"event '{k}' has 'agent_id'")
            if k in ("llm_call_start", "llm_call_end"):
                check("model" in ev, f"event '{k}' has 'model'")
            if k in ("tool_call_start", "tool_call_end"):
                check("tool_name" in ev, f"event '{k}' has 'tool_name'")

    # Verify tool_name in events matches node IDs in topology
    tool_call_events = [e for e in returned if e.get("kind") in ("tool_call_start", "tool_call_end")]
    for e in tool_call_events:
        tn = e.get("tool_name", "")
        if tn and e.get("tool_category") != "delegation":
            check(tn in tool_ids, f"tool_call tool_name='{tn}' exists as topology node")

    # Verify LLM model in events produces node IDs that exist in topology
    llm_call_events = [e for e in returned if e.get("kind") in ("llm_call_start", "llm_call_end")]
    for e in llm_call_events:
        model = e.get("model") or ""
        if model:
            lid = _llm_node_id(model)
            check(lid in llm_ids, f"llm_call model='{model}' → node '{lid}' in topology")

    # ── 7. Verify tiered layout band coverage ────────────────────────────────────

    print("\n=== 7. Tiered layout — band coverage ===")
    BANDS = [
        {"label": "Tools + Memory", "types": {"tool", "memory"}},
        {"label": "Agentic Core", "types": {"agent", "user"}},
        {"label": "LLM Layer", "types": {"llm"}},
    ]
    for band in BANDS:
        has = any(n["type"] in band["types"] for n in nodes)
        check(has, f"band '{band['label']}' has nodes")

    # ── Summary ───────────────────────────────────────────────────────────────────

    print("\n" + "=" * 60)
    print("  ALL CHECKS PASSED — demo worker topology + events pipeline OK")
    print("=" * 60 + "\n")
