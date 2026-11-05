#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for RunContext, invoke_runner, and ArtifactCollector."""

from __future__ import annotations

from pathlib import Path

from mas.lab.runners.artifacts import ArtifactCollector
from mas.lab.runners.context import RunContext
from mas.lab.runners.invoke import invoke_runner
from mas.lab.runners.protocol import RunArtifact, RunResult
from mas.lab.runners.registry import ApplicationRunnerRegistry


def test_artifact_collector_discovers_events(tmp_path: Path) -> None:
    trace = tmp_path / "traces"
    trace.mkdir(parents=True)
    events = trace / "events.jsonl"
    events.write_text('{"type":"test"}\n', encoding="utf-8")

    found = ArtifactCollector.discover(tmp_path)
    assert any(a.kind == "events" and a.path == events for a in found)


def test_artifact_collector_enrich_merges_missing(tmp_path: Path) -> None:
    trace = tmp_path / "traces"
    trace.mkdir(parents=True)
    events = trace / "events.jsonl"
    events.write_text("{}\n", encoding="utf-8")

    base = RunResult(content="ok", artifacts=[])
    enriched = ArtifactCollector.enrich(base, tmp_path)
    assert enriched.events_path == events


def test_invoke_runner_uses_registry(tmp_path: Path) -> None:
    class _Stub:
        runner_id = "stub-invoke"

        def run(self, prompt, **kwargs):
            out = kwargs["output_dir"]
            out.mkdir(parents=True, exist_ok=True)
            (out / "traces").mkdir(exist_ok=True)
            ep = out / "traces" / "events.jsonl"
            ep.write_text("{}\n", encoding="utf-8")
            return RunResult(content=prompt, artifacts=[RunArtifact(kind="events", path=ep)])

        def supports_contract(self, _):
            return False

    ApplicationRunnerRegistry.reset()
    ApplicationRunnerRegistry.register("stub-invoke", _Stub)

    ctx = RunContext(
        prompt="hello",
        config={},
        spec_path=tmp_path / "agent.yaml",
        output_dir=tmp_path / "out",
        runner_id="stub-invoke",
    )
    result = invoke_runner(ctx)
    assert result.content == "hello"
    assert result.events_path is not None
    ApplicationRunnerRegistry.reset()
