#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tutorial 08 — Telemetry: overlay validation, replay, OTLP dry-run."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import REPO_ROOT, T08, run_cli

QA = REPO_ROOT / "library-telemetry" / "examples" / "qa-agent"

pytestmark = pytest.mark.skipif(
    not QA.is_dir(),
    reason="library-telemetry/examples/qa-agent not in this checkout",
)


class TestOverlays:
    def test_native_pkg_overlay_validates(self):
        r = run_cli(
            [
                "mas-ctl",
                "validate",
                str(REPO_ROOT / "docs/tutorials/01-building-an-agent/agent.yaml"),
                "-o",
                "pkg://mas.library.standard/overlays/observability-native.yaml",
            ],
            cwd=REPO_ROOT,
        )
        assert r.returncode == 0, r.stderr + r.stdout
        assert "OK" in r.stdout

    def test_otel_json_overlay_validates(self):
        overlay = REPO_ROOT / "library-telemetry/overlays/observability-otel-json.yaml"
        r = run_cli(
            [
                "mas-ctl",
                "validate",
                str(REPO_ROOT / "docs/tutorials/01-building-an-agent/agent.yaml"),
                "-o",
                str(overlay),
            ],
            cwd=REPO_ROOT,
        )
        assert r.returncode == 0, r.stderr + r.stdout

    def test_tutorial_readme_points_at_examples(self):
        text = (T08 / "README.md").read_text(encoding="utf-8")
        assert "library-telemetry/examples/qa-agent" in text


class TestExampleTraces:
    def test_qa_agent_example_files_exist(self):
        for name in (
            "events.jsonl",
            "otel_sdk_spans_live.jsonl",
            "otel_sdk_spans_replay.jsonl",
        ):
            path = QA / name
            assert path.is_file(), path
            assert path.stat().st_size > 0

    def test_native_events_are_jsonl(self):
        import json

        lines = [ln for ln in QA.joinpath("events.jsonl").read_text().splitlines() if ln.strip()]
        assert len(lines) > 10
        kinds = {json.loads(ln).get("kind") for ln in lines}
        assert "mas_call_start" in kinds


class TestReplayAndPush:
    def test_replay_qa_agent_events(self, tmp_path: Path):
        pytest.importorskip("opentelemetry.sdk")
        from mas.library.telemetry.pipeline import convert_events_to_spans_file

        out = tmp_path / "otel_sdk_spans_replay.jsonl"
        n = convert_events_to_spans_file(
            QA / "events.jsonl",
            out,
            app_name="qa-agent",
        )
        assert n > 0
        assert out.is_file() and out.stat().st_size > 0

    def test_push_live_spans_dry_run(self):
        r = run_cli(
            [
                "mas-lab",
                "telemetry",
                "push",
                str(QA / "otel_sdk_spans_live.jsonl"),
                "--dry-run",
            ],
            cwd=REPO_ROOT,
        )
        assert r.returncode == 0, r.stderr + r.stdout
        assert "dry-run" in (r.stdout + r.stderr).lower()
