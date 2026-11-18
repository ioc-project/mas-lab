#!/usr/bin/env python3
#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Emit the Python version matrices for ``.github/workflows/python-matrix.yml``.

``.github/python-support.json`` is the one place that lists the supported Python versions, the
default one the per-change CI runs, and the components covered on the free-threaded build.
Everything else (workflows, ``requires-python``, the README and the docs page) is checked against it
by ``tests/test_ci_python.py``.

Cost model: every supported version runs the fast suites (unit and libraries). The slow end-to-end
suites run on the oldest and the newest version only, which is where a version-specific break shows up.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SUPPORT_FILE = REPO_ROOT / ".github" / "python-support.json"


def _ci_paths():
    spec = importlib.util.spec_from_file_location("ci_paths", REPO_ROOT / "scripts" / "ci_paths.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("ci_paths", module)
    spec.loader.exec_module(module)
    return module


def _key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.rstrip("t").split("."))


def load_support(path: Path = SUPPORT_FILE) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    supported = sorted(data["supported"], key=_key)
    if data["default"] not in supported:
        raise ValueError(f"default {data['default']} is not in supported {supported}")
    free = data["free_threaded"]
    if free["version"].rstrip("t") not in supported:
        raise ValueError(f"free-threaded {free['version']} is not a supported version")
    return {**data, "supported": supported}


def build_matrices(support: dict) -> dict[str, object]:
    ci_paths = _ci_paths()
    versions: list[str] = support["supported"]
    edges = sorted({versions[0], versions[-1]}, key=_key)
    unit = [
        {"python": v, "name": f"{row['name']} {v}", "task": row["task"]} for v in versions for row in ci_paths.UNIT_ROWS
    ]
    library = [
        {"python": v, "name": f"{row['name']} {v}", "dir": row["dir"]}
        for v in versions
        for row in ci_paths.LIBRARY_ROWS
    ]
    suite = [
        {"python": v, "name": f"{row['name']} {v}", "task": row["task"]} for v in edges for row in ci_paths.SUITE_ROWS
    ]
    return {
        "default": support["default"],
        "oldest": versions[0],
        "newest": versions[-1],
        "free_threaded": support["free_threaded"]["version"],
        "unit_matrix": {"include": unit},
        "library_matrix": {"include": library},
        "suite_matrix": {"include": suite},
    }


def pytest_args(support: dict, directory: str) -> list[str]:
    """``--ignore`` for a file and ``--deselect`` for a test id, for the entries under *directory*."""
    prefix = directory.rstrip("/") + "/"
    out = []
    for entry in support["free_threaded"].get("not_covered", []):
        target = entry["path"]
        if not target.startswith(prefix):
            continue
        if "::" not in target:
            out.append(f"--ignore={target}")
            continue
        # a test id is relative to pytest's rootdir, which is the component when it has its own
        # pytest configuration and the repository otherwise; deselect both spellings
        out.append(f"--deselect={target}")
        out.append(f"--deselect={target[len(prefix) :]}")
    return out


def format_outputs(matrices: dict[str, object]) -> str:
    lines = []
    for key, value in matrices.items():
        lines.append(f"{key}={value if isinstance(value, str) else json.dumps(value, separators=(',', ':'))}")
    return "".join(line + "\n" for line in lines)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    support = load_support()
    if args == ["components"]:
        # the editable-install arguments and the test directories of the free-threaded job
        components = support["free_threaded"]["components"]
        sys.stdout.write(" ".join(f"-e {c}" for c in components) + "\n")
        return 0
    if len(args) == 2 and args[0] == "pytest-args":
        # --ignore / --deselect for the tests in one directory that the free-threaded job does not cover
        sys.stdout.write(" ".join(pytest_args(support, args[1])) + "\n")
        return 0
    if args == ["test-dirs"]:
        sys.stdout.write(" ".join(f"{c}/tests" for c in support["free_threaded"]["components"]) + "\n")
        return 0
    if args:
        sys.stderr.write("usage: ci_python.py [components | test-dirs | pytest-args DIR]\n")
        return 2
    sys.stdout.write(format_outputs(build_matrices(support)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
