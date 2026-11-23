#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Runtime, lab, and default A2A imports must not pull grpc. The extra is opt-in."""

from __future__ import annotations

import os
import subprocess
import sys


def test_importing_runtime_and_lab_does_not_import_grpc() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; "
            "import mas.runtime; "
            "import mas.lab.cli; "
            "import library_ioa.plugins.a2a.agent_comm; "
            "import library_ioa.plugins.a2a.client; "
            "import library_ioa.plugins.a2a.exposure; "
            "mods = sorted(m for m in sys.modules if m == 'grpc' or m.startswith('grpc.')); "
            "print('grpc' if mods else 'no-grpc'); "
            "print(','.join(mods))",
        ],
        check=True,
        capture_output=True,
        env=dict(os.environ),
        text=True,
    )
    lines = result.stdout.strip().splitlines()
    assert lines[0] == "no-grpc", result.stdout + result.stderr
