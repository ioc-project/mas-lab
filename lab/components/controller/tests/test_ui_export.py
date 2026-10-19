#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
from pathlib import Path

from mas.lab.controller.ui_stream.export import build_bundle, build_envelopes, resolve_trace_paths, write_site
from mas.lab.controller.ui_stream.metrics import MetricsStore, flatten_metric_payload
from mas.lab.controller.ui_stream.refs import DataSourceRef
from mas.lab.controller.ui_stream.replay import (
    merge_replay_records,
    stamp_artefact_metrics,
    synthesize_metric_ticks,
)
from mas.lab.controller.ui_stream.spec_snapshot import snapshot_from_records


def test_flatten_nested_progress_and_scores() -> None:
    flat = flatten_metric_payload(
        {
            "kind": "metric",
            "metrics": {
                "elapsed_s": 1.5,
                "protocol": {"activated": True, "protocol_completion": 0.5, "note": "ignore"},
                "tokens": {"input": 3, "output": 2},
                "scored": {"challenge_cc_3_confirmed": {"value": 1.0, "error": None}},
            },
        }
    )
    assert flat["elapsed_s"] == 1.5
    assert flat["protocol.activated"] == 1
    assert flat["protocol.protocol_completion"] == 0.5
    assert "protocol.note" not in flat
    assert flat["tokens.input"] == 3
    assert flat["scored.challenge_cc_3_confirmed"] == 1.0
    assert flat["mce.challenge_cc_3_confirmed"] == 1.0
    assert flat["challenge_cc_3_confirmed"] == 1.0


def test_flatten_skips_panel_strings_and_nulls() -> None:
    flat = flatten_metric_payload(
        {
            "kind": "metric",
            "name": "eval_progress",
            "metrics": {
                "challenge": {"scoring": "pending_llm", "note": "wait"},
                "protocol": {
                    "activated": False,
                    "protocol_completion": None,
                    "_panel": {"anchors": ["a"], "matrix": {}},
                },
                "flag": {
                    "activated": True,
                    "fragments_present": ["goal"],
                    "fragments_missing": ["constraint"],
                },
                "session": {
                    "protocol_activated": {"value": 0.0},
                    "protocol_protocol_compliance": {"value": None},
                    "stateful_recovery": {"value": 1.0},
                },
            },
        }
    )
    assert flat["protocol.activated"] == 0
    assert flat["flag.activated"] == 1
    assert "challenge.scoring" not in flat
    assert "protocol.note" not in flat
    assert "protocol._panel" not in flat
    assert "protocol.protocol_completion" not in flat
    assert "flag.fragments_present" not in flat
    assert "flag.fragments_missing" not in flat
    assert flat["session.protocol_activated"] == 0.0
    assert flat["mce.protocol_activated"] == 0.0
    assert "protocol.protocol_compliance" not in flat
    assert "session.protocol_protocol_compliance" not in flat
    assert flat["stateful.recovery"] == 1.0
    assert flat["mce.stateful_recovery"] == 1.0


def test_apply_step_merges_flattened_keys() -> None:
    store = MetricsStore()
    ref = DataSourceRef(session="s1")
    store.apply_native(ref, {"kind": "llm_call_end", "timestamp": 1.0, "tokens_used": {"total_tokens": 4}})
    store.apply_step(
        ref,
        {"kind": "metric", "metrics": {"protocol": {"activated": False}, "elapsed_s": 9.0}},
    )
    snap = store.snapshot(ref)
    assert snap["total_tokens"] == 4
    assert snap["protocol.activated"] == 0
    assert snap["elapsed_s"] == 9.0


def test_snapshot_from_records_is_dynamic() -> None:
    records = [
        {"kind": "execution_start", "agent_id": "orchestrator", "timestamp": 1},
        {"kind": "tool_call_start", "agent_id": "orchestrator", "tool_name": "delegate_to_logs", "timestamp": 2},
        {"kind": "llm_call_end", "agent_id": "logs", "timestamp": 3, "tokens_used": {"total_tokens": 2}},
    ]
    spec = snapshot_from_records(records)
    assert spec["entry"] == "orchestrator"
    assert [item["id"] for item in spec["agents"]] == ["orchestrator", "logs"]
    assert spec["delegates_to"]["orchestrator"] == ["logs"]


def test_synthesize_metric_ticks_follow_llm_ends() -> None:
    native = [
        {"kind": "execution_start", "agent_id": "coordinator", "timestamp": 1.0, "call_id": "a"},
        {
            "kind": "llm_call_end",
            "agent_id": "coordinator",
            "timestamp": 2.0,
            "call_id": "b",
            "tokens_used": {"total_tokens": 10, "prompt_tokens": 8, "completion_tokens": 2},
        },
        {
            "kind": "llm_call_end",
            "agent_id": "specialist",
            "timestamp": 3.0,
            "call_id": "c",
            "tokens_used": {"total_tokens": 5, "prompt_tokens": 4, "completion_tokens": 1},
        },
        {"kind": "mas_call_end", "agent_id": "mas", "timestamp": 4.0, "call_id": "d"},
    ]
    ticks = synthesize_metric_ticks(native)
    assert ticks
    assert ticks[0]["timestamp"] == 2.0
    assert ticks[0]["nth"] == 1
    assert ticks[0]["metrics"]["total_tokens"] == 10
    assert ticks[-1]["metrics"]["total_tokens"] == 15
    assert ticks[-1]["timestamp"] == 4.0
    merged = merge_replay_records(native, ticks)
    kinds = [item["kind"] for item in merged]
    assert kinds[0] == "execution_start"
    assert "metric" in kinds
    assert kinds.index("metric") < kinds.index("mas_call_end") or kinds.count("metric") >= 1


def test_stamp_artefact_metrics_at_end() -> None:
    events = [{"kind": "execution_start", "timestamp": 1.0}, {"kind": "mas_call_end", "timestamp": 9.0}]
    stamped = stamp_artefact_metrics({"kind": "metric", "name": "eval_mce", "metrics": {"mce.x": 1}}, events)
    assert stamped is not None
    assert stamped["timestamp"] == 9.0


def test_merge_replay_records_interleaves_progress() -> None:
    native = [
        {"kind": "execution_start", "timestamp": 1.0},
        {"kind": "llm_call_end", "timestamp": 3.0},
    ]
    progress = [
        {"kind": "metric", "name": "eval_progress", "timestamp": 2.0, "metrics": {"protocol": {"activated": True}}},
        {"kind": "metric", "name": "eval_mce", "timestamp": 4.0, "metrics": {"mce.challenge_cc_3_confirmed": 1.0}},
    ]
    ordered = merge_replay_records(native, progress)
    assert [item["kind"] for item in ordered] == ["execution_start", "metric", "llm_call_end", "metric"]


def test_build_envelopes_include_spec_and_native() -> None:
    records = [
        {"kind": "execution_start", "agent_id": "entry", "timestamp": 10.0, "input": "hello"},
        {"kind": "llm_call_end", "agent_id": "entry", "timestamp": 11.0, "tokens_used": {"total_tokens": 5}},
        {"kind": "metric", "timestamp": 11.0, "metrics": {"protocol": {"activated": True}}},
    ]
    envelopes = build_envelopes(records, DataSourceRef(session="demo", mode="replay"))
    topics = [item["topic"] for item in envelopes]
    assert topics[0] == "spec.snapshot"
    assert envelopes[0]["payload"]["entry"] == "entry"
    assert "native.execution_start" in topics
    assert "native.llm_call_end" in topics
    snaps = [item for item in envelopes if item["topic"] == "metric.snapshot"]
    assert snaps
    assert snaps[-1]["payload"]["protocol.activated"] == 1
    assert snaps[-1]["payload"]["total_tokens"] == 5


def test_resolve_run_picks_up_progress(tmp_path: Path) -> None:
    from mas.lab.controller.ui_stream.run_index import resolve_run

    lib = tmp_path / "lab"
    run = lib / "output" / "baseline" / "item1" / "r1"
    (run / "traces").mkdir(parents=True)
    (run / "traces" / "events.jsonl").write_text(
        json.dumps({"kind": "execution_start", "agent_id": "a", "timestamp": 1}) + "\n",
        encoding="utf-8",
    )
    (run / "progress.jsonl").write_text(
        json.dumps({"elapsed_s": 1.0, "protocol": {"activated": False}, "snapshot_at": "2026-10-07T12:00:01Z"})
        + "\n",
        encoding="utf-8",
    )
    (run / "metrics.json").write_text(
        json.dumps({"session": {"challenge_cc_3_confirmed": {"value": 0.0}}}),
        encoding="utf-8",
    )
    (run / "progress.json").write_text('{"elapsed_s": 1.0, "protocol": {"activated": false}}\n', encoding="utf-8")
    (run / "protocol.json").write_text('{"protocol": {"_panel": {"anchors": []}}}\n', encoding="utf-8")
    record = resolve_run(lib, DataSourceRef(scenario="baseline", test="1", run="1"))
    assert record.artefacts["progress"].name == "progress.jsonl"
    assert record.artefacts["progress_latest"].name == "progress.json"
    assert record.artefacts["protocol"].name == "protocol.json"
    assert record.artefacts["metrics"].name == "metrics.json"


def test_build_bundle_and_write_site(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    events.write_text(
        json.dumps({"kind": "execution_start", "agent_id": "a1", "timestamp": 1.0, "input": "q"})
        + "\n"
        + json.dumps({"kind": "mas_call_end", "agent_id": "a1", "timestamp": 2.0, "output": "done"})
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "progress.jsonl").write_text(
        json.dumps({"elapsed_s": 1.0, "protocol": {"activated": True}, "snapshot_at": "2026-10-07T12:00:01Z"})
        + "\n",
        encoding="utf-8",
    )
    layouts = tmp_path / "layouts"
    layouts.mkdir()
    (layouts / "demo.yaml").write_text(
        "version: 1\ntitle: demo\nlayout:\n  type: panel\n  plugin: plot\n  props: {kind: chat, artefact: chat}\n",
        encoding="utf-8",
    )
    bundle = build_bundle(events, layout_name="demo.yaml", layouts_root=layouts)
    assert bundle["layout"]["title"] == "demo"
    topics = {item["topic"] for item in bundle["envelopes"]}
    assert "spec.snapshot" in topics
    assert "native.execution_start" in topics
    metrics = [item for item in bundle["envelopes"] if item["topic"] == "metric.snapshot"]
    assert any(row["payload"].get("protocol.activated") == 1 for row in metrics)

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text(
        "<!DOCTYPE html><html><head></head><body><div id='app'></div></body></html>",
        encoding="utf-8",
    )
    dest = tmp_path / "site"
    write_site(dest, bundle, dist_dir=dist, build_ui=False)
    assert (dest / "bundle.json").is_file()
    assert (dest / "bundle.js").is_file()
    html = (dest / "index.html").read_text(encoding="utf-8")
    assert "__DEMO_UI_OFFLINE__" in html
    assert "bundle.js" in html


def test_resolve_trace_paths_finds_custom_panel_siblings(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    events.write_text(json.dumps({"kind": "mas_call_start", "timestamp": 1.0}) + "\n", encoding="utf-8")
    (tmp_path / "custom.panel-a.jsonl").write_text(
        json.dumps({"kind": "custom.panel-a.panel", "ts": 1.5, "outcome": "SUCCESS"}) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "custom.panel-b.jsonl").write_text(
        json.dumps({"kind": "custom.panel-b.panel", "ts": 1.2, "locked": True}) + "\n",
        encoding="utf-8",
    )
    resolved_events, metrics, progress, custom = resolve_trace_paths(events)
    assert resolved_events == events
    assert metrics is None
    assert progress is None
    assert set(custom) == {"panel-a", "panel-b"}
    assert custom["panel-a"].name == "custom.panel-a.jsonl"


def test_build_bundle_includes_custom_panel_ticks(tmp_path: Path) -> None:
    # Offline export (mode #4) must carry the same custom.panel-a.panel/
    # custom.panel-b.panel data a live run or online replay would have --
    # this is the gap flagged as "not working at all yet" before this fix.
    events = tmp_path / "events.jsonl"
    events.write_text(
        json.dumps({"kind": "execution_start", "agent_id": "a1", "timestamp": 1.0, "input": "q"}) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "custom.panel-a.jsonl").write_text(
        json.dumps({"kind": "custom.panel-a.panel", "ts": 1.5, "x_star": "seed-a", "outcome": "SUCCESS"}) + "\n",
        encoding="utf-8",
    )
    layouts = tmp_path / "layouts"
    layouts.mkdir()
    (layouts / "demo.yaml").write_text(
        "version: 1\ntitle: demo\nlayout:\n  type: panel\n  plugin: plot\n  props: {kind: chat, artefact: chat}\n",
        encoding="utf-8",
    )
    bundle = build_bundle(events, layout_name="demo.yaml", layouts_root=layouts)
    topics = {item["topic"] for item in bundle["envelopes"]}
    assert "custom.panel-a.panel" in topics
    panel = next(item for item in bundle["envelopes"] if item["topic"] == "custom.panel-a.panel")
    assert panel["payload"]["x_star"] == "seed-a"
    assert panel["payload"]["outcome"] == "SUCCESS"
