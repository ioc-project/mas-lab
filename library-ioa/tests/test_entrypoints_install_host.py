#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Every console entry point installs the host environment before it does anything else.

Each runs in a fresh interpreter (the test session already has a host installed) with an API key
in its environment. After ``main`` returns or exits, hostenv must be installed, must not hand the
key to library code, and the process environment must no longer hold it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

_CANARY = "sk-entrypoint-canary-7f3a"

_PROBE = r"""
import importlib, json, os, sys
from mas.runtime import hostenv

before = hostenv.installed()
main = getattr(importlib.import_module(sys.argv[1]), sys.argv[2])
argv = json.loads(sys.argv[3])
try:
    main() if argv is None else main(argv)
except SystemExit:
    pass
print("PROBE " + json.dumps({
    "before": before,
    "installed": hostenv.installed(),
    "hostenv_key": hostenv.get("OPENAI_API_KEY"),
    "process_key": os.environ.get("OPENAI_API_KEY"),
    "path_kept": hostenv.get("PATH") is not None,
}))
"""

# (console script, module, function, argv); argv None calls main() with no argument.
_ENTRY_POINTS = [
    ("mas-mcp", "library_ioa.plugins.mcp.server", "main", ["--help"]),
    ("mas-slim", "library_ioa.plugins.slim.cli", "main", ["--help"]),
    ("mas-a2a", "library_ioa.plugins.a2a.cli", "main", ["--help"]),
    ("mas-runtime", "mas.runtime.cli.main", "main", None),
]


def _run_probe(module: str, function: str, argv: list[str] | None) -> dict:
    env = {
        key: os.environ[key]
        for key in ("PATH", "HOME", "PYTHONPATH", "VIRTUAL_ENV", "TMPDIR", "SYSTEMROOT")
        if key in os.environ
    }
    env["OPENAI_API_KEY"] = _CANARY
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE, module, function, json.dumps(argv)],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    lines = [line for line in proc.stdout.splitlines() if line.startswith("PROBE ")]
    assert lines, f"probe printed nothing (rc={proc.returncode}): {proc.stderr[-2000:]}"
    assert _CANARY not in proc.stderr
    return json.loads(lines[-1].removeprefix("PROBE "))


@pytest.mark.parametrize(("script", "module", "function", "argv"), _ENTRY_POINTS, ids=[e[0] for e in _ENTRY_POINTS])
def test_entry_point_installs_host_and_drops_api_key(script: str, module: str, function: str, argv) -> None:
    result = _run_probe(module, function, argv)
    assert result["before"] is False, f"{script}: importing the module must not install the host"
    assert result["installed"] is True, f"{script} never installed the host environment"
    assert result["path_kept"] is True
    assert result["hostenv_key"] is None, f"{script} left OPENAI_API_KEY readable through hostenv"
    assert result["process_key"] is None, f"{script} left OPENAI_API_KEY in the process environment"


def test_every_console_script_is_probed() -> None:
    """A new console script in these packages gets a row above (it must install the host too)."""
    import tomllib
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    scripts: dict[str, str] = {}
    for name in ("library-ioa", "runtime"):
        data = tomllib.loads((root / name / "pyproject.toml").read_text(encoding="utf-8"))
        scripts.update(data.get("project", {}).get("scripts", {}))
    probed = {f"{module}:{function}" for _, module, function, _ in _ENTRY_POINTS}
    assert set(scripts.values()) <= probed, sorted(set(scripts.values()) - probed)
