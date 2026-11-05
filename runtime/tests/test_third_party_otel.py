#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Third-party OTel stays off unless the user or the otel plugin opts in."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest
from mas.third_party_otel import (
    A2A_SDK_OTEL_FLAG,
    OBSERVE_REALTIME_FLAG,
    pin_third_party_otel_off,
)


def test_pin_defaults_a2a_sdk_flag_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(A2A_SDK_OTEL_FLAG, raising=False)
    pin_third_party_otel_off()
    assert os.environ[A2A_SDK_OTEL_FLAG] == "false"


def test_pin_respects_explicit_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(A2A_SDK_OTEL_FLAG, "true")
    pin_third_party_otel_off()
    assert os.environ[A2A_SDK_OTEL_FLAG] == "true"


def test_pin_defaults_realtime_observability_flag_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(OBSERVE_REALTIME_FLAG, raising=False)
    pin_third_party_otel_off()
    assert os.environ[OBSERVE_REALTIME_FLAG] == "false"


def test_pin_respects_explicit_realtime_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(OBSERVE_REALTIME_FLAG, "true")
    pin_third_party_otel_off()
    assert os.environ[OBSERVE_REALTIME_FLAG] == "true"


def test_importing_mas_runtime_pins_both_flags() -> None:
    """``test_registry_import_hygiene.py`` covers the import-side-effect case
    for ``get_registry().list()``; this covers the same effect for plainly
    importing ``mas.runtime`` itself — the first of the independent entry
    points that import :mod:`mas.third_party_otel` for defense in depth
    (``mas.runtime``, ``mas.lab.cli``, ``mas.ctl.cli``, ``library_ioa``,
    ``library_ioa.plugins.a2a`` — see each site's own import comment)."""
    env = dict(os.environ)
    env.pop(A2A_SDK_OTEL_FLAG, None)
    env.pop(OBSERVE_REALTIME_FLAG, None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os; import mas.runtime; "
            f"print(os.environ[{A2A_SDK_OTEL_FLAG!r}]); "
            f"print(os.environ[{OBSERVE_REALTIME_FLAG!r}])",
        ],
        check=True,
        capture_output=True,
        env=env,
        text=True,
    )
    assert result.stdout.strip().splitlines() == ["false", "false"]
