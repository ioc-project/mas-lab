#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Controller API — list runtime runner plugins."""

from __future__ import annotations


def test_list_runtime_runners_includes_mas_lab() -> None:
    from mas.lab.controller.api import ControllerAPI
    from mas.lab.runners.constants import DEFAULT_LAB_RUNNER_ID
    from mas.lab.runners.factory import RunnerFactory
    from mas.lab.runners.registry import ApplicationRunnerRegistry

    ApplicationRunnerRegistry.reset()
    RunnerFactory.available()
    runners = ControllerAPI().list_runtime_runners()
    ids = {r["id"] for r in runners}
    assert DEFAULT_LAB_RUNNER_ID in ids
    ApplicationRunnerRegistry.reset()
