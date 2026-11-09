#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for lab core ApplicationRunner types."""

from __future__ import annotations

from pathlib import Path

import pytest
from mas.lab.runners.protocol import RunResult
from mas.lab.runners.registry import ApplicationRunnerRegistry


def test_run_result_accessors():
    from mas.runtime.run_artifact import RunArtifact

    events = Path("/tmp/events.jsonl")
    stats = Path("/tmp/stats-a.json")
    result = RunResult(
        content="ok",
        artifacts=[
            RunArtifact(kind="events", path=events),
            RunArtifact(kind="sys_stats", path=stats),
            RunArtifact(kind="other", path=None),
        ],
    )
    assert result.events_path == events
    assert result.stats_paths == [stats]
    assert result.artifact("events").path == events
    assert result.artifact("missing") is None


def test_registry_register_and_reset():
    from mas.lab.runners.protocol import ApplicationRunnerProtocol

    class _Stub(ApplicationRunnerProtocol):
        runner_id = "stub"

        def run(self, *a, **k):
            return RunResult(content="x")

        def supports_contract(self, contract_id: str) -> bool:
            return True

        def get_supported_contracts(self):
            return ["x"]

    ApplicationRunnerRegistry.reset()
    ApplicationRunnerRegistry.register("stub", _Stub)
    assert "stub" in ApplicationRunnerRegistry.available()
    inst = ApplicationRunnerRegistry.get("stub")
    assert inst.runner_id == "stub"
    ApplicationRunnerRegistry.reset()


def test_registry_unknown_runner():
    ApplicationRunnerRegistry.reset()
    ApplicationRunnerRegistry._initialized = True
    with pytest.raises(ValueError, match="not registered"):
        ApplicationRunnerRegistry.get("does-not-exist")
    ApplicationRunnerRegistry.reset()


def test_registry_mas_lab_runner_available():
    from mas.lab.runners.constants import DEFAULT_LAB_RUNNER_ID
    from mas.lab.runners.registry import ApplicationRunnerRegistry

    ApplicationRunnerRegistry.reset()
    ApplicationRunnerRegistry._ensure_initialized()
    assert DEFAULT_LAB_RUNNER_ID in ApplicationRunnerRegistry.available()
    ApplicationRunnerRegistry.reset()
