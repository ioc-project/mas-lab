#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""ShellStep — run an inline shell command as a pipeline step."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from mas.lab.benchmark.environment.declared_env import compile_env
from mas.lab.benchmark.pipeline import ConfigParam, PipelineStep, StepOutput
from mas.runtime import hostenv
from mas.runtime.secrets.process import child_env
from mas.runtime.secrets.resolver import reveal_mapping

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
                env: {API_TOKEN: {secret: tool-token}, MODE: fast}
                passthrough: [SVC_ENDPOINT]

    The child gets the lab allowlist plus ``env`` (a SecretRef is revealed into this child
    only) and the named ``passthrough`` keys a running service exported (never a host variable:
    a name the host set but no service exported is not copied). Nothing else.
    """

    type = "shell"

    PARAMS = [
        ConfigParam("command", str, description="Shell command."),
        ConfigParam("cwd", str, default=None, description="Working directory (else lab root)."),
        ConfigParam("env", dict, default=None, description="Variables for the command: strings or {secret: NAME}."),
        ConfigParam("passthrough", list, default=None, description="Names of exported service variables to copy."),
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
        declared = compile_env(self.config.get("env"), where=f"shell step {self.name!r}")
        extra: dict[str, str] = {}
        for name in self.config.get("passthrough") or []:
            value = hostenv.exported(str(name))
            if value is not None:
                extra[str(name)] = value
        extra.update(reveal_mapping(declared))
        proc = await asyncio.create_subprocess_exec(
            "/bin/sh",
            "-c",
            command,
            cwd=str(workdir),
            env=child_env(extra, lab=True),
        )
        code = await proc.wait()
        if code != 0:
            raise RuntimeError(f"shell step {self.name!r} exited {code}: {command}")
        logger.info("shell step %s ok in %s: %s", self.name, workdir, command)
        return StepOutput(data={"command": command, "cwd": str(workdir), "exit": code}, files=[], metadata={})
