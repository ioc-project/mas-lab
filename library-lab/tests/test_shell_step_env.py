#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""A shell step's child sees what services of the run export, not only the process environment."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from mas.library.lab.steps.services.shell import ShellStep
from mas.runtime import hostenv


def test_shell_step_child_sees_the_overlay(tmp_path):
    out = tmp_path / "seen.txt"
    step = ShellStep(name="s", config={"command": f'printf %s "$SVC_ENDPOINT" > {out}', "cwd": str(tmp_path)})
    with hostenv.overlay({"SVC_ENDPOINT": "http://svc"}):
        asyncio.run(step.execute(SimpleNamespace()))
    assert out.read_text() == "http://svc"
