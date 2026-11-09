#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""a2a-sdk self-instrumentation stays off unless the user opts in."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest


def test_importing_library_ioa_pins_a2a_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OTEL_INSTRUMENTATION_A2A_SDK_ENABLED", raising=False)
    env = dict(os.environ)
    env.pop("OTEL_INSTRUMENTATION_A2A_SDK_ENABLED", None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os; import library_ioa; print(os.environ['OTEL_INSTRUMENTATION_A2A_SDK_ENABLED'])",
        ],
        check=True,
        capture_output=True,
        env=env,
        text=True,
    )
    assert result.stdout.strip() == "false"


def test_explicit_a2a_opt_in_is_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_INSTRUMENTATION_A2A_SDK_ENABLED", "true")
    import importlib

    import library_ioa

    importlib.reload(library_ioa)
    assert os.environ["OTEL_INSTRUMENTATION_A2A_SDK_ENABLED"] == "true"
