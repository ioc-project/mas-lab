#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Helpers for the loopback fixture."""

from __future__ import annotations

import builtins
import csv
import io
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "scaling_lab"
WALL_CLOCK_GROUP = "wall-clock"
_PORTS_FILE = ".ports.json"
_FIXTURE_PORTS = {"18765": "model", "18766": "tools", "18767": "tools_health"}


def free_ports(count: int) -> list[int]:
    """Distinct free loopback ports, found by binding port 0 (held together so they differ)."""
    sockets = [socket.socket() for _ in range(count)]
    try:
        for sock in sockets:
            sock.bind(("127.0.0.1", 0))
        return [int(sock.getsockname()[1]) for sock in sockets]
    finally:
        for sock in sockets:
            sock.close()


def lab_ports(lab_dir: Path) -> list[int]:
    """The ports allocated to one copied lab: model, tools, tools health."""
    return list(json.loads((lab_dir / _PORTS_FILE).read_text(encoding="utf-8")))


def _is_descendant(pid: int, ancestor: int) -> bool:
    seen = 0
    while pid > 1 and seen < 64:
        if pid == ancestor:
            return True
        try:
            pid = int(subprocess.check_output(["ps", "-o", "ppid=", "-p", str(pid)], text=True).strip() or 0)
        except (subprocess.CalledProcessError, ValueError, FileNotFoundError):
            return False
        seen += 1
    return False


def stop_spawned_listeners(*ports: int) -> None:
    """Stop leftover listeners on this lab's ports, only ones that are descendants of this process."""
    me = os.getpid()
    for port in ports:
        try:
            raw = subprocess.check_output(
                ["lsof", "-nP", "-iTCP:" + str(port), "-sTCP:LISTEN", "-t"],
                stderr=subprocess.DEVNULL,
                text=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue
        for pid_text in raw.split():
            try:
                pid = int(pid_text)
            except ValueError:
                continue
            if not _is_descendant(pid, me):
                continue
            try:
                os.kill(pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                continue


def prepare_lab(tmp_path: Path) -> Path:
    """Copy the fixture and rewrite process-service commands to absolute paths."""
    dest = tmp_path / "scaling_lab"
    shutil.copytree(FIXTURE, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    services = dest / "infra" / "services.yaml"
    text = services.read_text(encoding="utf-8")
    text = text.replace("PYTHON", sys.executable)
    text = text.replace("MODEL_SCRIPT", str(dest / "servers" / "fake_model.py"))
    text = text.replace("TOOLS_SCRIPT", str(dest / "servers" / "fake_tools.py"))
    services.write_text(text, encoding="utf-8")
    ports = free_ports(len(_FIXTURE_PORTS))
    for name in ("services.yaml", "loopback.yaml", "tools.yaml"):
        path = dest / "infra" / name
        text = path.read_text(encoding="utf-8")
        for fixed, port in zip(_FIXTURE_PORTS, ports, strict=True):
            text = text.replace(fixed, str(port))
        path.write_text(text, encoding="utf-8")
    (dest / _PORTS_FILE).write_text(json.dumps(ports), encoding="utf-8")
    return dest


def isolate_lab_env(monkeypatch: Any, tmp_path: Path) -> None:
    """Keep user default_infra and live API keys out of the loopback fixture."""
    xdg = tmp_path / "xdg"
    for name, folder in (
        ("XDG_CONFIG_HOME", xdg / "config"),
        ("XDG_DATA_HOME", xdg / "data"),
        ("XDG_CACHE_HOME", xdg / "cache"),
        ("XDG_STATE_HOME", xdg / "state"),
    ):
        folder.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(name, str(folder))
    (xdg / "config" / "mas").mkdir(parents=True, exist_ok=True)
    (xdg / "config" / "mas" / "config.yaml").write_text("mas_ctl: {}\n", encoding="utf-8")
    secrets = tmp_path / "secrets"
    from mas.ctl.secrets import write_file_secret

    write_file_secret(secrets, "openai", "test-key")
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "config.yaml").write_text(f"secrets:\n  dir: {secrets}\n", encoding="utf-8")
    monkeypatch.setenv("MAS_WORKSPACE_ROOT", str(ws))
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.delenv("MAS_INFRA_REFS", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)


def write_cpu_bound_dataset(
    lab_dir: Path,
    *,
    n_items: int = 72,
    rounds: int = 1_200_000,
    sleep_s: float = 0.2,
) -> None:
    """Rewrite the copied lab dataset: each item is CPU work plus a wait."""
    import yaml

    items = [{"id": index, "inputs": {"user": f"work {rounds} wait {sleep_s}"}} for index in range(1, n_items + 1)]
    path = lab_dir / "dataset.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["spec"]["items"] = items
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def write_schedule_overlay(
    tmp_path: Path,
    *,
    parallel: int,
    limit: int | None = None,
    extra: dict[str, Any] | None = None,
    worker_processes: int = 1,
    timeout: int | None = None,
) -> Path:
    schedule: dict[str, Any] = {
        "parallel_scenarios": parallel,
        "pause_between_runs": 0,
    }
    executor: dict[str, Any] = {"worker_processes": worker_processes}
    if timeout is not None:
        executor["timeout"] = timeout
    spec: dict[str, Any] = {
        "schedule": schedule,
        "executor": executor,
        "retry": {"params": {"max_attempts": 1, "backoff_s": 0}},
    }
    if limit is not None:
        spec["dataset"] = {"limit": limit}
    if extra:
        spec.update(extra)
    import yaml

    overlay = {
        "apiVersion": "mas/v1",
        "kind": "ExperimentOverlay",
        "metadata": {"name": "schedule"},
        "spec": spec,
    }
    path = tmp_path / f"overlay-p{parallel}-n{limit or 'all'}.yaml"
    path.write_text(yaml.safe_dump(overlay, sort_keys=False), encoding="utf-8")
    return path


async def run_scaling_lab(
    lab_dir: Path,
    tmp_path: Path,
    *,
    parallel: int = 1,
    limit: int | None = None,
    extra_overlay: dict[str, Any] | None = None,
    progress: bool = False,
    worker_processes: int = 1,
    timeout: int | None = None,
    cache_name: str = "trace-cache",
) -> Path:
    """Run the loopback fixture. Returns the output directory."""
    from mas.lab.benchmark.schedule.run_batch import run_mas_benchmark

    overlay = write_schedule_overlay(
        tmp_path,
        parallel=parallel,
        limit=limit,
        extra=extra_overlay,
        worker_processes=worker_processes,
        timeout=timeout,
    )
    output_dir = tmp_path / f"out-p{parallel}-n{limit or 'all'}-{time.time_ns()}"
    cache_dir = tmp_path / cache_name
    cache_dir.mkdir(parents=True, exist_ok=True)
    try:
        ok = await run_mas_benchmark(
            lab_dir / "experiment.yaml",
            progress=progress,
            force=True,
            output_dir=output_dir,
            trace_cache_dir=cache_dir,
            experiment_overlays=[str(overlay)],
        )
    finally:
        stop_spawned_listeners(*lab_ports(lab_dir))
    if not ok:
        csv_path = output_dir / "results.csv"
        errors = []
        if csv_path.is_file():
            import csv

            with csv_path.open(encoding="utf-8", newline="") as handle:
                errors = [row.get("error") or row.get("output") or "" for row in csv.DictReader(handle)]
        raise AssertionError(f"loopback fixture failed (parallel={parallel}, limit={limit}): {errors[:4]}")
    return output_dir


def results_rows(output_dir: Path) -> list[dict[str, str]]:
    csv_path = output_dir / "results.csv"
    with csv_path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _tool_codes(events_path: Path) -> list[str]:
    codes: list[str] = []
    if not events_path.is_file():
        return codes
    for line in events_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue
        kind = str(rec.get("kind") or "")
        if kind not in {"tool_call_end", "tool_call_result", "engine.io.return"}:
            continue
        tool = str(rec.get("tool_name") or rec.get("name") or "")
        error = rec.get("error") or rec.get("error_code") or rec.get("code") or ""
        status = rec.get("status") or rec.get("outcome") or ""
        codes.append(f"{kind}:{tool}:{error}:{status}")
    return codes


def outcome_row(row: dict[str, str]) -> tuple[Any, ...]:
    """Equality key for one result row: status, output, per-call codes. No timing."""
    trace = Path(row.get("trace_path") or "")
    events = trace if trace.name == "events.jsonl" else (trace / "events.jsonl" if trace.is_dir() else trace)
    return (
        str(row.get("item_id") or ""),
        str(row.get("scenario") or ""),
        str(row.get("run") or ""),
        str(row.get("status") or ""),
        str(row.get("output") or ""),
        tuple(_tool_codes(events)),
    )


def event_profile(row: dict[str, str]) -> tuple[Any, ...]:
    """Per-run event count and kind multiset from the run's events.jsonl. No timing, no ids."""
    from collections import Counter

    trace = Path(row.get("trace_path") or "")
    events = trace if trace.name == "events.jsonl" else (trace / "events.jsonl" if trace.is_dir() else trace)
    kinds: Counter[str] = Counter()
    if events.is_file():
        for line in events.read_text(encoding="utf-8").splitlines():
            if line.strip():
                kinds[str(json.loads(line).get("kind") or "")] += 1
    return (
        str(row.get("item_id") or ""),
        str(row.get("scenario") or ""),
        str(row.get("run") or ""),
        sum(kinds.values()),
        tuple(sorted(kinds.items())),
    )


def event_profile_table(output_dir: Path, *, exclude_wall_clock: bool = True) -> list[tuple[Any, ...]]:
    return sorted(
        event_profile(row)
        for row in results_rows(output_dir)
        if not (exclude_wall_clock and str(row.get("group") or "") == WALL_CLOCK_GROUP)
    )


def stream_profile(cache_dir: Path) -> list[tuple[Any, ...]]:
    """Kind multiset of each published run stream, sorted. The channel side of the event profile."""
    from collections import Counter

    profiles = []
    streams = cache_dir / "streams"
    for path in sorted(streams.glob("*.jsonl")) if streams.is_dir() else []:
        kinds: Counter[str] = Counter()
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                kinds[str(record.get("kind") or "")] += 1
        profiles.append(tuple(sorted(kinds.items())))
    return sorted(profiles)


def outcome_table(output_dir: Path, *, exclude_wall_clock: bool = True) -> list[tuple[Any, ...]]:
    rows = results_rows(output_dir)
    out: list[tuple[Any, ...]] = []
    for row in rows:
        if exclude_wall_clock and str(row.get("group") or "") == WALL_CLOCK_GROUP:
            continue
        out.append(outcome_row(row))
    return sorted(out)


class WriteOpenCounter:
    """Count open-for-write / append calls.

    ``Path.open`` uses ``io.open``. ``JsonlFileEmitter`` uses ``os.open``.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self._real_io = io.open
        self._real_builtins = builtins.open
        self._real_os = os.open

    def __call__(self, file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        text = str(mode)
        if any(flag in text for flag in ("w", "a", "x", "+")):
            self.calls.append((str(file), text))
        return self._real_io(file, mode, *args, **kwargs)

    def os_open(self, path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_APPEND):
            self.calls.append((str(path), f"os:{flags}"))
        return self._real_os(path, flags, *args, **kwargs)

    @contextmanager
    def patch(self) -> Iterator["WriteOpenCounter"]:
        io.open = self  # type: ignore[assignment]
        builtins.open = self  # type: ignore[assignment]
        os.open = self.os_open  # type: ignore[assignment]
        try:
            yield self
        finally:
            io.open = self._real_io  # type: ignore[assignment]
            builtins.open = self._real_builtins  # type: ignore[assignment]
            os.open = self._real_os


def write_opens_per_run(counter: WriteOpenCounter, output_dir: Path) -> int:
    """Opens of per-run event and stream logs, divided by completed runs."""
    n_runs = max(1, len(results_rows(output_dir)))
    interesting = 0
    for path, _mode in counter.calls:
        name = Path(path).name
        if name in {"events.jsonl", "otel_sdk_spans.jsonl", "observe_sdk_spans.jsonl"} or name.endswith(".jsonl"):
            interesting += 1
    return interesting // n_runs
