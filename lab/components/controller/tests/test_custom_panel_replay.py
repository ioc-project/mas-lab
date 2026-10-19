#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Replay mode must be able to rebuild custom.panel-a.panel/custom.panel-b.panel
from a recorded run, not just receive them live. This exercises the full
chain: Pipeline steps persist each tick to a sibling
``custom.{owner}.jsonl`` (via file_tail.append_custom_trace); run_index.py's
resolve_run() must find that sibling (and its infra/.cache gold-replay
fallback); replay.py's custom_panel_records() must read it back verbatim;
NativeIngest.ingest() (patched separately) must republish it under its own
``custom.*`` topic during replay, same as a live run would."""

import json
from pathlib import Path

from mas.lab.controller.ui_stream.refs import DataSourceRef
from mas.lab.controller.ui_stream.replay import custom_panel_records, merge_replay_records
from mas.lab.controller.ui_stream.run_index import resolve_run


def _make_run(tmp_path: Path, *, scenario: str = "arm-b") -> Path:
    run_dir = tmp_path / "output" / scenario / "item1" / "r1"
    (run_dir / "traces").mkdir(parents=True)
    (run_dir / "traces" / "events.jsonl").write_text(
        json.dumps({"kind": "mas_call_start", "timestamp": 1.0}) + "\n"
    )
    return run_dir


def test_resolve_run_finds_sibling_custom_traces(tmp_path):
    run_dir = _make_run(tmp_path)
    (run_dir / "custom.panel-a.jsonl").write_text(
        json.dumps({"kind": "custom.panel-a.panel", "ts": 1.5, "x_star": "seed-a", "outcome": "SUCCESS"}) + "\n"
    )
    (run_dir / "custom.panel-b.jsonl").write_text(
        json.dumps({"kind": "custom.panel-b.panel", "ts": 1.2, "locked": True}) + "\n"
    )
    ref = DataSourceRef(library="lib", experiment="exp", scenario="arm-b", test="1", run="1")
    record = resolve_run(tmp_path, ref)
    assert "custom_panel-a" in record.artefacts
    assert "custom_panel-b" in record.artefacts
    assert record.artefacts["custom_panel-a"] == run_dir / "custom.panel-a.jsonl"


def test_custom_panel_records_reads_persisted_ticks(tmp_path):
    run_dir = _make_run(tmp_path)
    path = run_dir / "custom.panel-a.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(rec)
            for rec in [
                {"kind": "custom.panel-a.panel", "ts": 1.1, "t": 0},
                {"kind": "custom.panel-a.panel", "ts": 1.9, "t": 1, "outcome": "SUCCESS"},
            ]
        )
        + "\n"
    )
    records = custom_panel_records(path)
    assert len(records) == 2
    assert records[0]["t"] == 0
    assert records[1]["outcome"] == "SUCCESS"


def test_custom_panel_records_merges_in_time_order_with_native_events():
    native = [{"kind": "mas_call_start", "timestamp": 1.0}, {"kind": "mas_call_end", "timestamp": 3.0}]
    custom = [{"kind": "custom.panel-a.panel", "ts": 2.0, "x_star": "seed-a"}]
    merged = merge_replay_records(native, custom)
    assert [r.get("kind") for r in merged] == ["mas_call_start", "custom.panel-a.panel", "mas_call_end"]


def test_custom_panel_records_missing_file_returns_empty(tmp_path):
    assert custom_panel_records(None) == []
    assert custom_panel_records(tmp_path / "does-not-exist.jsonl") == []


def test_resolve_run_discovers_a_third_owner_not_hardcoded_owners(tmp_path):
    # Proves the artefact discovery is a real glob, not a hardcoded
    # ("panel-a", "panel-b") tuple -- a hypothetical future owner ("mediate")
    # must be picked up with zero code changes.
    run_dir = _make_run(tmp_path, scenario="mediate-scenario")
    (run_dir / "custom.mediate.jsonl").write_text(
        json.dumps({"kind": "custom.mediate.panel", "ts": 1.0, "status": "pending"}) + "\n"
    )
    ref = DataSourceRef(library="lib", experiment="exp", scenario="mediate-scenario", test="1", run="1")
    record = resolve_run(tmp_path, ref)
    assert "custom_mediate" in record.artefacts
    panels = custom_panel_records(record.artefacts["custom_mediate"])
    assert panels[0]["status"] == "pending"


def test_gold_cache_fallback_for_custom_traces(tmp_path):
    cache = tmp_path / "infra" / ".cache"
    cache.mkdir(parents=True)
    (cache / "baseline.events.jsonl").write_text(json.dumps({"kind": "mas_call_start", "timestamp": 1.0}) + "\n")
    (cache / "baseline.custom.panel-a.jsonl").write_text(
        json.dumps({"kind": "custom.panel-a.panel", "ts": 1.0}) + "\n"
    )
    ref = DataSourceRef(library="lib", experiment="exp", scenario="baseline")
    record = resolve_run(tmp_path, ref)
    assert "custom_panel-a" in record.artefacts
    assert record.artefacts["custom_panel-a"].name == "baseline.custom.panel-a.jsonl"
