#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The controller daemon serves the one store it started with and refuses a job that names another."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def store(tmp_path: Path) -> Path:
    from mas.ctl.secrets import write_file_secret

    path = tmp_path / "flag-store"
    write_file_secret(path, "openai", "from-the-flag-store")
    return path


def test_daemon_main_installs_the_store_of_its_flags(store: Path, tmp_path: Path) -> None:
    code = f"""
import json
import mas.lab.controller.daemon as d
from mas.runtime.secrets import SecretRef, current_resolver
class _Stop(Exception):
    pass
def _api(_workspace):
    print(json.dumps(current_resolver().reveal(SecretRef(name="openai"))))
    raise _Stop
d.ControllerAPI = _api
try:
    d.main(["--no-http", "--socket", {str(tmp_path / "c.sock")!r}, "--secrets-dir", {str(store)!r}])
except _Stop:
    pass
"""
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "PYTHONPATH": os.environ.get("PYTHONPATH", "")}
    out = subprocess.run([sys.executable, "-c", code], env=env, cwd=tmp_path, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout.strip().splitlines()[-1]) == "from-the-flag-store"


def test_a_job_for_another_store_is_refused(store: Path, tmp_path: Path) -> None:
    from mas.lab.controller.registry import WorkerRegistry
    from mas.lab.controller.workers import WorkerRunner, run_benchmark_worker
    from mas.lab.secret_host import SecretHostSettings

    own = SecretHostSettings(directory=str(store))
    own.install_process()
    other = {"experiment_yaml": str(tmp_path / "x.yaml"), "secrets": {"directory": str(tmp_path / "other")}}
    registry = WorkerRegistry()
    with pytest.raises(ValueError, match="restart the controller"):
        run_benchmark_worker(registry, WorkerRunner(registry), other)
    assert own.args() == ["--secret-store", "file", "--secrets-dir", str(store)]
