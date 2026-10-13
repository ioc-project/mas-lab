#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mas.library.lab.steps.services.shell import ShellStep


@pytest.mark.asyncio
async def test_shell_step_runs_command(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    target.write_text("{}", encoding="utf-8")
    step = ShellStep("reset", {"command": "rm -f state.json", "cwd": str(tmp_path)})
    out = await step.execute(object())
    assert out.data["exit"] == 0
    assert not target.exists()
