#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""RunnerFactory — unified entry point for bench + library extension runners."""

from __future__ import annotations

from pathlib import Path

import pytest
from mas.lab.runners.constants import DEFAULT_LAB_RUNNER_ID
from mas.lab.runners.factory import RunnerFactory
from mas.lab.runners.protocol import ApplicationRunnerProtocol
from mas.lab.runners.registry import ApplicationRunnerRegistry


class _StubRunner(ApplicationRunnerProtocol):
    runner_id: str = "stub-factory"

    def run(self, prompt: str, **kwargs):
        from mas.lab.runners.protocol import RunResult

        return RunResult(content=prompt)

    def supports_contract(self, contract_id: str) -> bool:
        return False


class _MasLabStubRunner(_StubRunner):
    runner_id: str = DEFAULT_LAB_RUNNER_ID


@pytest.fixture(autouse=True)
def _reset_registry():
    ApplicationRunnerRegistry.reset()
    yield
    ApplicationRunnerRegistry.reset()


def test_factory_available_includes_registered():
    ApplicationRunnerRegistry.register("stub-factory", _StubRunner)
    assert "stub-factory" in RunnerFactory.available()


def test_factory_get_returns_instance():
    ApplicationRunnerRegistry.register("stub-factory", _StubRunner)
    runner = RunnerFactory.get("stub-factory")
    assert runner.runner_id == "stub-factory"


def test_factory_infer_and_get_default_mas_lab(monkeypatch):
    ApplicationRunnerRegistry.register(DEFAULT_LAB_RUNNER_ID, _MasLabStubRunner)
    runner = RunnerFactory.infer_and_get()
    assert runner.runner_id == DEFAULT_LAB_RUNNER_ID


def test_factory_get_default_native():
    ApplicationRunnerRegistry.register(DEFAULT_LAB_RUNNER_ID, _MasLabStubRunner)
    runner = RunnerFactory.get(DEFAULT_LAB_RUNNER_ID)
    assert runner.runner_id == DEFAULT_LAB_RUNNER_ID


def test_factory_infer_and_get_execution_override():
    ApplicationRunnerRegistry.register("stub-factory", _StubRunner)
    runner = RunnerFactory.infer_and_get(execution_runner="stub-factory")
    assert runner.runner_id == "stub-factory"


def test_factory_infer_from_mas_manifest(tmp_path: Path):
    mas_path = tmp_path / "mas.yaml"
    mas_path.write_text(
        "kind: MAS\nspec:\n  framework:\n    default_adapter: langgraph\n",
        encoding="utf-8",
    )

    ApplicationRunnerRegistry.register(DEFAULT_LAB_RUNNER_ID, _MasLabStubRunner)
    runner = RunnerFactory.infer_and_get(mas_manifest=mas_path)
    assert runner.runner_id == DEFAULT_LAB_RUNNER_ID
