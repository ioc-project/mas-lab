#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Registry listing must not pull A2A or OpenTelemetry."""

from __future__ import annotations

import os
import subprocess
import sys


def test_registry_list_does_not_import_a2a_or_otel_sdk() -> None:
    env = dict(os.environ)
    env.pop("OTEL_INSTRUMENTATION_A2A_SDK_ENABLED", None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; "
            "from mas.runtime.registry import get_registry; "
            "get_registry().list(); "
            "mods = set(sys.modules); "
            "print('a2a' if 'a2a' in mods or any(m.startswith('a2a.') for m in mods) else 'no-a2a'); "
            "print('otel' if any(m == 'opentelemetry' or m.startswith('opentelemetry.') for m in mods) else "
            "'no-otel'); "
            "print('a2a-plugin' if any(m.startswith('library_ioa.plugins.a2a.') and m != 'library_ioa.plugins.a2a' for "
            "m in mods) else 'no-a2a-plugin')",
        ],
        check=True,
        capture_output=True,
        env=env,
        text=True,
    )
    lines = result.stdout.strip().splitlines()
    assert lines == ["no-a2a", "no-otel", "no-a2a-plugin"], result.stdout + result.stderr
