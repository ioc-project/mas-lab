#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Run scripts/check_path_consistency.py on the small paired fixture."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_EVENTS = _ROOT / "tests" / "fixtures" / "path_consistency" / "events.jsonl"
_SPANS = _ROOT / "tests" / "fixtures" / "path_consistency" / "otel_sdk_spans.jsonl"


def test_path_consistency_script_on_fixture() -> None:
    import importlib.util

    script = _ROOT / "scripts" / "check_path_consistency.py"
    spec = importlib.util.spec_from_file_location("check_path_consistency", script)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    report = mod.compare(_EVENTS, _SPANS)
    assert "native_node_types" in report
    assert "otel_node_types" in report
    assert "expected_native_extra_types" in report
    assert "unexpected_core_mismatch" in report
    assert "AgentCall" in report["native_node_types"]
