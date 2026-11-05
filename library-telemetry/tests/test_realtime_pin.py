#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""observe-sdk realtime stays off unless the user opts in."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest


def test_importing_telemetry_pins_observe_realtime_off() -> None:
    env = dict(os.environ)
    env.pop("OBSERVE_REALTIME_OBSERVABILITY_ENABLED", None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os; import mas.library.telemetry; print(os.environ['OBSERVE_REALTIME_OBSERVABILITY_ENABLED'])",
        ],
        check=True,
        capture_output=True,
        env=env,
        text=True,
    )
    assert result.stdout.strip() == "false"


def test_explicit_realtime_opt_in_is_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OBSERVE_REALTIME_OBSERVABILITY_ENABLED", "true")
    import importlib

    import mas.library.telemetry as telemetry

    importlib.reload(telemetry)
    assert os.environ["OBSERVE_REALTIME_OBSERVABILITY_ENABLED"] == "true"
