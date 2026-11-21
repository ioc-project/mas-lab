#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Agent runner that drives an external harness over its standard input and output.

The command is spawned without a shell, from an absolute path that is on an explicit allow-list, with
exactly the environment the spec gives it (nothing is inherited, so no secret leaks in by accident). Every
read has a deadline and a size cap. The process is torn down, with its children, when the task ends
(``per-task``) or when the runner closes (``persistent``).
"""

from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, BinaryIO

from mas.runtime.aio import new_thread
from mas.runtime.boundary.runner import RunnerError, RunnerReply, RunnerTask

FRAMINGS = ("line", "jsonrpc")
LIFECYCLES = ("per-task", "persistent")
_EOF = object()


def escape_line(text: str) -> str:
    """One line of text: a backslash becomes ``\\\\``, a newline ``\\n`` (and a carriage return ``\\r``)."""
    return text.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r")


def unescape_line(line: str) -> str:
    out: list[str] = []
    chars = iter(line)
    for char in chars:
        if char != "\\":
            out.append(char)
            continue
        nxt = next(chars, "\\")
        out.append({"n": "\n", "r": "\r", "\\": "\\"}.get(nxt, "\\" + nxt))
    return "".join(out)


class _Child:
    """One spawned process and the thread that turns its output into lines."""

    def __init__(self, args: list[str], env: dict[str, str], cwd: str | None, max_line: int) -> None:
        self.proc = subprocess.Popen(  # noqa: S603 - no shell, allow-listed absolute path, explicit env
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            cwd=cwd,
            shell=False,
            start_new_session=os.name == "posix",
        )
        self.lines: queue.Queue[Any] = queue.Queue()
        self._max_line = max_line
        new_thread(target=self._read, args=(self.proc.stdout,), daemon=True, name="stdio-harness-read").start()

    def _read(self, stream: BinaryIO) -> None:
        try:
            while True:
                raw = stream.readline(self._max_line + 1)
                if not raw:
                    break
                if len(raw) > self._max_line and not raw.endswith(b"\n"):
                    self.lines.put(RunnerError(f"the harness wrote a line over {self._max_line} bytes"))
                    break
                self.lines.put(raw)
        except (OSError, ValueError):
            pass
        finally:
            self.lines.put(_EOF)

    def write(self, data: bytes) -> None:
        try:
            self.proc.stdin.write(data)
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as exc:
            raise RunnerError("the harness closed its input") from exc

    def end_input(self) -> None:
        try:
            self.proc.stdin.close()
        except OSError:
            pass

    def alive(self) -> bool:
        return self.proc.poll() is None

    def stop(self, grace: float) -> None:
        """End the process group: ask, wait ``grace`` seconds, then kill."""
        self.end_input()
        if self.proc.poll() is None:
            self._signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                self._signal(signal.SIGKILL)
                self.proc.wait()
        elif os.name == "posix":
            self._signal(signal.SIGKILL)  # a leader that exited may have left children
        if self.proc.stdout is not None:
            self.proc.stdout.close()

    def _signal(self, sig: int) -> None:
        try:
            if os.name == "posix":
                os.killpg(self.proc.pid, sig)
            elif sig == signal.SIGKILL:
                self.proc.kill()
            else:
                self.proc.terminate()
        except (ProcessLookupError, PermissionError, OSError):
            pass


class StdioHarnessRunner:
    """Drive a command that reads tasks on stdin and answers on stdout.

    ``framing: line``: the task is one line (newlines escaped, see :func:`escape_line`), the reply is
    one line. ``framing: jsonrpc``: newline-delimited JSON-RPC 2.0. The request is
    ``{"method": "task", "params": {"text", "session_id", "task_id", "caller", "headers"}}``; the harness
    may send ``{"method": "event", "params": {...}}`` notifications first (kept as the reply's events) and
    ends with ``{"result": {"text": ..., "artifacts": [...], "events": [...]}}`` or ``{"error": ...}``.

    ``lifecycle: per-task`` spawns one process per task and ends it after the reply. ``persistent`` keeps
    one process and respawns it if it died; tasks are served one at a time.
    """

    runner_id = "stdio-harness"

    def __init__(
        self,
        command: Sequence[str],
        allow: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        cwd: str | None = None,
        framing: str = "line",
        lifecycle: str = "per-task",
        timeout: float = 60.0,
        max_task_bytes: int = 1_048_576,
        max_reply_bytes: int = 1_048_576,
        term_grace: float = 2.0,
    ) -> None:
        argv = [str(part) for part in command]
        if not argv or isinstance(command, str):
            raise ValueError("stdio-harness: command is a non-empty list of arguments, not a shell string")
        if not allow or isinstance(allow, str):
            raise ValueError("stdio-harness: allow lists the executables this runner may start")
        if not Path(argv[0]).is_absolute():
            raise ValueError("stdio-harness: command[0] must be an absolute path (no PATH lookup)")
        allowed = {os.path.realpath(str(item)) for item in allow}
        if os.path.realpath(argv[0]) not in allowed:
            raise ValueError(f"stdio-harness: {argv[0]!r} is not in allow")
        if framing not in FRAMINGS:
            raise ValueError(f"stdio-harness: framing is one of {FRAMINGS}, got {framing!r}")
        if lifecycle not in LIFECYCLES:
            raise ValueError(f"stdio-harness: lifecycle is one of {LIFECYCLES}, got {lifecycle!r}")
        if timeout <= 0 or max_task_bytes <= 0 or max_reply_bytes <= 0:
            raise ValueError("stdio-harness: timeout and the size caps must be positive")
        self._argv = argv
        self._env = {str(k): str(v) for k, v in (env or {}).items()}
        self._cwd = cwd
        self._framing = framing
        self._lifecycle = lifecycle
        self._timeout = float(timeout)
        self._max_task = int(max_task_bytes)
        self._max_reply = int(max_reply_bytes)
        self._grace = float(term_grace)
        self._lock = threading.Lock()
        self._child: _Child | None = None
        self._next_id = 0
        self._closed = False

    def run(self, task: RunnerTask) -> RunnerReply:
        with self._lock:
            if self._closed:
                raise RunnerError("the runner is closed")
            request = self._request(task)
            if len(request) > self._max_task:
                raise RunnerError(f"the task is {len(request)} bytes, over the {self._max_task} byte cap")
            child = self._child if self._lifecycle == "persistent" and self._child and self._child.alive() else None
            if child is None:
                child = _Child(self._argv, self._env, self._cwd, self._max_reply)
            if self._lifecycle == "persistent":
                self._child = child
            try:
                child.write(request)
                if self._lifecycle == "per-task":
                    child.end_input()
                return self._collect(child, task)
            except BaseException:
                self._drop(child)
                raise
            finally:
                if self._lifecycle == "per-task":
                    child.stop(self._grace)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            child, self._child = self._child, None
        if child is not None:
            child.stop(self._grace)

    def _drop(self, child: _Child) -> None:
        if self._child is child:
            self._child = None
        if self._lifecycle == "persistent":
            child.stop(self._grace)

    def _request(self, task: RunnerTask) -> bytes:
        if self._framing == "line":
            return (escape_line(task.text) + "\n").encode("utf-8")
        self._next_id += 1
        params = {
            "text": task.text,
            "session_id": task.session_id,
            "task_id": task.task_id,
            "caller": task.caller,
            "headers": dict(task.headers),
        }
        message = {"jsonrpc": "2.0", "id": self._next_id, "method": "task", "params": params}
        return (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")

    def _collect(self, child: _Child, task: RunnerTask) -> RunnerReply:
        deadline = time.monotonic() + self._timeout
        received = 0
        events: list[dict[str, Any]] = []
        while True:
            try:
                item = child.lines.get(timeout=max(0.0, deadline - time.monotonic()))
            except queue.Empty:
                raise RunnerError(f"the harness gave no reply within {self._timeout:g} seconds") from None
            if item is _EOF:
                raise RunnerError(f"the harness ended without a reply (exit status {child.proc.poll()})")
            if isinstance(item, RunnerError):
                raise item
            received += len(item)
            if received > self._max_reply:
                raise RunnerError(f"the harness reply is over the {self._max_reply} byte cap")
            line = item.decode("utf-8", errors="replace").rstrip("\r\n")
            if self._framing == "line":
                return RunnerReply(text=unescape_line(line), context_id=task.session_id)
            reply = self._jsonrpc(line, task, events)
            if reply is not None:
                return reply

    def _jsonrpc(self, line: str, task: RunnerTask, events: list[dict[str, Any]]) -> RunnerReply | None:
        try:
            message = json.loads(line)
        except ValueError as exc:
            raise RunnerError("the harness wrote a line that is not JSON") from exc
        if not isinstance(message, dict):
            raise RunnerError("the harness wrote a JSON-RPC message that is not an object")
        if "id" not in message:
            if message.get("method") == "event" and isinstance(message.get("params"), dict):
                events.append(dict(message["params"]))
            return None
        if message["id"] != self._next_id:
            raise RunnerError("the harness answered a different request")
        if "error" in message:
            error = message["error"]
            text = error.get("message") if isinstance(error, dict) else error
            raise RunnerError(f"the harness failed: {str(text)[:500]}")
        result = message.get("result")
        if not isinstance(result, dict):
            raise RunnerError("the harness result is not an object")
        artifacts = result.get("artifacts") or ()
        if not isinstance(artifacts, (list, tuple)) or not all(isinstance(a, dict) for a in artifacts):
            raise RunnerError("the harness artifacts are not a list of objects")
        own = result.get("events") or ()
        if not isinstance(own, (list, tuple)) or not all(isinstance(e, dict) for e in own):
            raise RunnerError("the harness events are not a list of objects")
        return RunnerReply(
            text=str(result.get("text") or ""),
            context_id=str(result.get("context_id") or task.session_id),
            artifacts=tuple(artifacts),
            task_state=str(result.get("task_state") or ""),
            events=tuple(events) + tuple(own),
        )
