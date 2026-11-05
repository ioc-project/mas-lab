#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Registry edge cases."""

from __future__ import annotations

import pytest
from mas.lab.runners.registry import ApplicationRunnerRegistry


def test_registry_unknown_runner():
    ApplicationRunnerRegistry.reset()
    ApplicationRunnerRegistry._initialized = True
    with pytest.raises(ValueError, match="not registered"):
        ApplicationRunnerRegistry.get("nonexistent")


def test_registry_register_override():
    ApplicationRunnerRegistry.reset()

    class Dummy:
        runner_id = "dummy"

        def run(self, *a, **k):
            raise NotImplementedError

        def supports_contract(self, _):
            return False

    ApplicationRunnerRegistry.register("dummy", Dummy)
    assert ApplicationRunnerRegistry.get("dummy").runner_id == "dummy"


def test_run_result_artifact_helpers():
    from pathlib import Path

    from mas.lab.runners.protocol import RunArtifact, RunResult

    events = RunArtifact(kind="events", path=Path("/tmp/events.jsonl"))
    stats = RunArtifact(kind="sys_stats", path=Path("/tmp/stats.json"))
    result = RunResult(content="ok", artifacts=[events, stats])
    assert result.events_path == Path("/tmp/events.jsonl")
    assert result.stats_paths == [Path("/tmp/stats.json")]
    assert result.artifact("events") is events
    assert result.artifact("missing") is None


def test_registry_entry_point_load_warning(monkeypatch):
    from mas.lab.runners.registry import ApplicationRunnerRegistry

    ApplicationRunnerRegistry.reset()

    class _Ep:
        name = "broken"

        def load(self):
            raise ImportError("nope")

    monkeypatch.setattr(
        "importlib.metadata.entry_points",
        lambda group: [_Ep()] if group == "mas.lab.runners" else [],
    )
    ApplicationRunnerRegistry._ensure_initialized()
    assert "broken" not in ApplicationRunnerRegistry.available()


def test_registry_concurrent_init_is_thread_safe():
    import concurrent.futures

    from mas.lab.runners.constants import DEFAULT_LAB_RUNNER_ID

    ApplicationRunnerRegistry.reset()

    def _probe(_: int) -> list[str]:
        ApplicationRunnerRegistry._ensure_initialized()
        return ApplicationRunnerRegistry.available()

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_probe, range(32)))

    assert all(DEFAULT_LAB_RUNNER_ID in r for r in results)
    assert DEFAULT_LAB_RUNNER_ID in ApplicationRunnerRegistry.available()
