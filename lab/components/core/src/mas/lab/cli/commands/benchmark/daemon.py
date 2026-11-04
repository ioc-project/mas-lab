#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``mas-lab benchmark daemon`` subcommands."""
from __future__ import annotations

import json as _json
import os as _os
import signal as _signal
import subprocess as _subprocess
import sys as _sys
from pathlib import Path

import click
from mas.runtime.xdg import mas_state_root

_DAEMON_DIR = mas_state_root()
_DAEMON_PID = _DAEMON_DIR / "daemon.pid"


def _read_daemon_pid() -> dict | None:
    """Return the daemon metadata dict, or None if no PID file exists."""
    if not _DAEMON_PID.exists():
        return None
    try:
        return _json.loads(_DAEMON_PID.read_text())
    except Exception:
        return None


def _daemon_running(info: dict) -> bool:
    """True if the PID in *info* is still alive."""
    try:
        _os.kill(info["pid"], 0)
        return True
    except (ProcessLookupError, PermissionError, KeyError):
        return False


@click.group("daemon")
def daemon_group() -> None:
    """Manage the MAS Lab UI server as a background daemon.

    The daemon runs ``mas-lab serve`` as a detached background process
    and writes a PID file under ``$XDG_STATE_HOME/mas/`` so subsequent CLI/UI invocations can
    find it.

    \b
    Examples
    --------
    mas-lab benchmark daemon start                         # start on port 8090
    mas-lab benchmark daemon start --port 9000 -o ~/data   # custom port
    mas-lab benchmark daemon status
    mas-lab benchmark daemon stop
    """


@daemon_group.command("start")
@click.option("--port", "-p", type=int, default=8090, show_default=True,
              help="TCP port for the Benchmark Explorer.")
@click.option("-o", "--output-dir", type=Path, default=None,
              help="Benchmarks root directory (persisted in the PID file).")
@click.option("--force", is_flag=True, default=False,
              help="Kill an existing daemon before starting a new one.")
def daemon_start_cmd(port: int, output_dir: Path | None, force: bool) -> None:
    """Start the Benchmark Explorer daemon."""
    info = _read_daemon_pid()
    if info and _daemon_running(info):
        if not force:
            click.echo(
                f"Daemon already running  pid={info['pid']}  port={info['port']}\n"
                f"Use --force to restart, or: mas-lab benchmark daemon stop"
            )
            return
        _os.kill(info["pid"], _signal.SIGTERM)
        click.echo(f"Stopped daemon  pid={info['pid']}")

    _DAEMON_DIR.mkdir(parents=True, exist_ok=True)
    import shutil as _shutil
    mas_lab_bin = _shutil.which("mas-lab") or _sys.argv[0]
    cmd: list[str] = [mas_lab_bin, "serve",
                      "--port", str(port), "--no-browser"]
    if output_dir:
        cmd += ["-o", str(output_dir)]

    proc = _subprocess.Popen(
        cmd,
        stdout=_subprocess.DEVNULL,
        stderr=_subprocess.DEVNULL,
        start_new_session=True,
    )
    pid_data = {
        "pid":        proc.pid,
        "port":       port,
        "output_dir": str(output_dir) if output_dir else None,
    }
    _DAEMON_PID.write_text(_json.dumps(pid_data))
    click.echo(
        f"Daemon started  pid={proc.pid}  port={port}\n"
        f"  http://localhost:{port}\n"
        f"  PID file: {_DAEMON_PID}"
    )


@daemon_group.command("stop")
def daemon_stop_cmd() -> None:
    """Stop the running daemon."""
    info = _read_daemon_pid()
    if not info:
        click.echo("No daemon PID file found.")
        return
    if not _daemon_running(info):
        click.echo(f"Daemon not running (stale PID {info['pid']}).")
        _DAEMON_PID.unlink(missing_ok=True)
        return
    _os.kill(info["pid"], _signal.SIGTERM)
    _DAEMON_PID.unlink(missing_ok=True)
    click.echo(f"Daemon stopped  pid={info['pid']}")


@daemon_group.command("status")
def daemon_status_cmd() -> None:
    """Show daemon status."""
    info = _read_daemon_pid()
    if not info:
        click.echo("Status: stopped  (no PID file)")
        return
    alive = _daemon_running(info)
    status = "running" if alive else "stopped (stale PID)"
    click.echo(f"Status: {status}")
    click.echo(f"  pid:        {info.get('pid')}")
    click.echo(f"  port:       {info.get('port', 8090)}")
    click.echo(f"  output_dir: {info.get('output_dir', '(default)')}")
    if alive:
        click.echo(f"  url:        http://localhost:{info.get('port', 8090)}")
    else:
        _DAEMON_PID.unlink(missing_ok=True)
        click.echo("  (PID file removed)")
