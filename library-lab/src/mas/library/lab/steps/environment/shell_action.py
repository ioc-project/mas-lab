#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E402
from __future__ import annotations

"""shell — run /bin/sh -c in the experiment dir. Records exit code only."""

import asyncio
import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from mas.lab.benchmark.environment.step import ActionResult, EnvironmentContext, EnvironmentStep


def _run(command: str, cwd: Path, timeout: float, cancel: threading.Event, base_env: Any) -> int:
    from mas.lab.benchmark.environment.fixtures import scrubbed_environ

    proc = subprocess.Popen(
        ["/bin/sh", "-c", command],
        cwd=str(cwd),
        env=scrubbed_environ(base_env or {}),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    deadline = time.monotonic() + timeout
    while True:
        try:
            return int(proc.wait(timeout=0.05))
        except subprocess.TimeoutExpired:
            pass
        expired = time.monotonic() >= deadline
        if expired or cancel.is_set():
            # Kill the whole group: a grandchild must not outlive the action that started it.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
            proc.wait()
            raise TimeoutError(f"shell command {'exceeded ' + str(timeout) + 's' if expired else 'was cancelled'}")


class ShellActionStep(EnvironmentStep):
    type = "shell"

    async def act(self, ctx: EnvironmentContext) -> ActionResult:
        command = str(self.config.get("command") or "")  # sh owns ${...}: no template pass
        if not command:
            raise ValueError("shell requires config.command")
        cwd = Path(ctx.work_dir or ".")
        cancel = threading.Event()
        try:
            status = await asyncio.to_thread(_run, command, cwd, ctx.limits.action_timeout_s, cancel, ctx.base_env)
        except asyncio.CancelledError:
            cancel.set()  # the action timeout fired: stop the process now, not when the thread notices
            raise
        return ActionResult(outcome="ok" if status == 0 else "failed", payload={"status": status})
