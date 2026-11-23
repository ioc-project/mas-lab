#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""observe-sdk realtime stays off unless the process owner opts in. The library does not write os.environ."""

from __future__ import annotations

import os
import subprocess
import sys


def test_importing_telemetry_does_not_write_the_process_environment() -> None:
    env = dict(os.environ)
    env.pop("OBSERVE_REALTIME_OBSERVABILITY_ENABLED", None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os; import mas.library.telemetry; print('OBSERVE_REALTIME_OBSERVABILITY_ENABLED' in os.environ)",
        ],
        check=True,
        capture_output=True,
        env=env,
        text=True,
    )
    assert result.stdout.strip() == "False"
