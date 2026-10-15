#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

"""ShellStep — run an inline shell command as a pipeline step."""

import asyncio
import logging
from pathlib import Path
from typing import Any

from mas.lab.benchmark.pipeline import ConfigParam, PipelineStep, StepOutput

logger = logging.getLogger(__name__)


class ShellStep(PipelineStep):
    """Execute ``config.command`` with ``/bin/sh -c`` in the lab (or given) cwd.

    YAML::

        run:
          pre:
            - name: reset-state
              type: shell
              config:
                command: rm -f mas/skills/skill-state.json
    """

    type = "shell"

    PARAMS = [
        ConfigParam("command", str, description="Shell command."),
        ConfigParam("cwd", str, default=None, description="Working directory (else lab root)."),
    ]

    async def execute(self, ctx: Any) -> StepOutput:
        command = str(self.config.get("command") or "").strip()
        if not command:
            raise ValueError("shell step needs config.command")
        cwd = self.config.get("cwd")
        if cwd:
            workdir = Path(str(cwd)).expanduser()
        else:
            workdir = Path(getattr(ctx, "experiment_dir", None) or getattr(ctx, "lab_dir", None) or ".")
        proc = await asyncio.create_subprocess_exec(
            "/bin/sh",
            "-c",
            command,
            cwd=str(workdir),
        )
        code = await proc.wait()
        if code != 0:
            raise RuntimeError(f"shell step {self.name!r} exited {code}: {command}")
        logger.info("shell step %s ok in %s: %s", self.name, workdir, command)
        return StepOutput(data={"command": command, "cwd": str(workdir), "exit": code}, files=[], metadata={})
