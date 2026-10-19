#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Every native event kind can emit an OTel span (unless its layer is off)."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from mas.library.telemetry.conversion.envelope import KIND_ENVELOPE
from mas.library.telemetry.conversion.layers import ExportLayers
from mas.library.telemetry.conversion.mappings.base import registered_kinds
from mas.library.telemetry.verification.spanspec import SpanValidator
from tests.conftest import requires_otel

SAMPLES = (
    Path(__file__).resolve().parents[4]
    / "library-samples"
    / "apps"
    / "trip-planner"
    / "traces"
    / "events.jsonl"
)


@requires_otel
def test_unknown_kind_emits_span_when_execution_enabled(tmp_path):
    from mas.library.telemetry.conversion.replay import replay_events_file

    events = [
        {
            "kind": "totally_novel_kind",
            "timestamp": 1.0,
            "agent_id": "planner",
            "call_id": "x1",
        }
    ]
    src = tmp_path / "e.jsonl"
    src.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    out = tmp_path / "s.jsonl"
    replay_events_file(
        src,
        out,
        app_name="svc",
        converter_profile="observe_sdk",
        export_layers={"annotation": True},
    )
    spans = [json.loads(line) for line in out.read_text().splitlines() if line.strip()]
    names = {s.get("name") or "" for s in spans}
    assert any("totally_novel_kind" in n for n in names)


@requires_otel
def test_trip_planner_sample_emits_observe_sdk_spans_and_validates(tmp_path):
    from mas.library.telemetry.conversion.replay import replay_events_file
    from mas.library.telemetry.steps.verify_spans import run_verify_spans

    if not SAMPLES.exists():
        raise AssertionError(f"missing tutorial sample {SAMPLES}")

    kinds = Counter()
    for line in SAMPLES.read_text(encoding="utf-8").splitlines():
        if line.strip():
            kinds[json.loads(line)["kind"]] += 1
    assert sum(kinds.values()) >= 190
    missing = sorted(k for k in kinds if k not in set(registered_kinds()))
    assert not missing, missing

    out = tmp_path / "otel.jsonl"
    replay_events_file(
        SAMPLES,
        out,
        service_name="mas-runtime",
        app_name="trip-planner",
        converter_profile="observe_sdk",
        export_layers=ExportLayers.complete(),
        extensions=True,
    )
    spans = [json.loads(line) for line in out.read_text().splitlines() if line.strip()]
    # Interval start/end pairs collapse; still far above the 9-span toy dump.
    assert len(spans) > 80
    names = {s.get("name") or "" for s in spans}
    assert any(n.endswith(".agent") for n in names)
    assert any(n.endswith(".chat") for n in names)
    assert any(n.endswith(".tool") for n in names)
    assert any(n.endswith(".graph") for n in names)
    assert any(n.endswith(".processing") for n in names)
    assert "session.start" in names
    assert "session.end" in names

    report = run_verify_spans(out, spanspec_level="L2", fail_on_error=False)
    assert report["ok"] is True
    spec = report["spanspec"]
    assert spec["conformance_at_level"] in {"passing", "full"}
    assert SpanValidator.looks_like_observe_sdk(spans)
    from mas.library.telemetry.verification.spanspec import _OBSERVE_SDK_SPEC

    observe = SpanValidator(_OBSERVE_SDK_SPEC)
    observe_report = observe.validate(spans)
    assert observe_report.errors("L2") == []
    # Keep envelope coverage honest as kinds are added.
    for kind in kinds:
        assert kind in KIND_ENVELOPE or kind in set(registered_kinds())
