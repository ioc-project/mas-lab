#!/usr/bin/env python3
#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Decide which CI jobs a changed-path set should run.

Prints ``key=true|false`` lines for GitHub Actions ``GITHUB_OUTPUT``.
Lint always runs. Libraries, labs, and docs are independent flags.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

LIBRARIES = {
    "lib_standard": "library-standard/",
    "lib_eval": "library-eval/",
    "lib_ioa": "library-ioa/",
    "lib_telemetry": "library-telemetry/",
    "lib_kg": "library-kg/",
    "lib_skills": "library-skills/",
    "lib_samples": "library-samples/",
    "lib_lab": "library-lab/",
}

JOBS = (
    "lint",
    "runtime",
    "ctl",
    "lab_unit",
    "repo",
    "unit_311",
    "controller",
    "smoke",
    "golden",
    "tutorials",
    "validate",
    "hitl",
    "docs",
    "demo_ui",
    "mcp",
    "a2a",
    *LIBRARIES,
)

# Libraries whose code shapes the recorded event stream the golden runs compare.
GOLDEN_LIBRARIES = frozenset({"lib_standard", "lib_skills", "lib_samples", "lib_lab"})

SUITE_JOBS = ("controller", "smoke", "golden", "tutorials", "validate", "hitl")
UNIT_JOBS = ("runtime", "ctl", "lab_unit", "repo")
CORE_JOBS = (*UNIT_JOBS, *SUITE_JOBS, "docs", "demo_ui", "mcp", "a2a", *LIBRARIES)

# Static matrix rows. Job-level `if` cannot use `matrix` (GitHub rejects the
# workflow before any job starts). `changes` emits a filtered JSON matrix.
SUITE_ROWS = (
    {"flag": "controller", "name": "Controller", "task": "verify-controller"},
    {"flag": "smoke", "name": "Lab smoke", "task": "verify-lab-smoke"},
    {"flag": "golden", "name": "Golden runs", "task": "verify-golden-runs"},
    {"flag": "tutorials", "name": "Tutorials", "task": "verify-tutorials"},
    {"flag": "validate", "name": "Dry-run", "task": "validate"},
    {"flag": "hitl", "name": "HITL", "task": "verify-hitl"},
)
UNIT_ROWS = (
    {"flag": "runtime", "name": "Runtime", "task": "verify-runtime"},
    {"flag": "ctl", "name": "Ctl", "task": "verify-ctl"},
    {"flag": "lab_unit", "name": "Lab", "task": "verify-lab-unit"},
    {"flag": "repo", "name": "Repo", "task": "verify-repo"},
)
LIBRARY_ROWS = (
    {
        "flag": "lib_standard",
        "name": "standard",
        "dir": "library-standard",
        "cov": "mas.library.standard",
        "fail_under": "90",
    },
    {"flag": "lib_eval", "name": "eval", "dir": "library-eval", "cov": "mas.library.eval", "fail_under": "90"},
    {"flag": "lib_ioa", "name": "ioa", "dir": "library-ioa", "cov": "library_ioa", "fail_under": "90"},
    {
        "flag": "lib_telemetry",
        "name": "telemetry",
        "dir": "library-telemetry",
        "cov": "mas.library.telemetry",
        "fail_under": "90",
    },
    {"flag": "lib_kg", "name": "kg", "dir": "library-kg", "cov": "mas.library.kg", "fail_under": "80"},
    {
        "flag": "lib_skills",
        "name": "skills",
        "dir": "library-skills",
        "cov": "mas.library.skills",
        "fail_under": "90",
    },
    {
        "flag": "lib_samples",
        "name": "samples",
        "dir": "library-samples",
        "cov": "mas.library.samples",
        "fail_under": "90",
    },
    {"flag": "lib_lab", "name": "lab", "dir": "library-lab", "cov": "mas.library.lab", "fail_under": "90"},
)

DOCS_EXACT = frozenset({"mkdocs.yml", "requirements-docs.txt"})
DOCS_PREFIXES = ("docs/", "overrides/")
DOCS_SCRIPTS = frozenset(
    {
        "scripts/gen_docs.py",
        "scripts/gen_doc_paths.py",
        "scripts/mkdocs_github_stats.py",
    }
)
SMOKE_TESTS = frozenset(
    {
        "tests/test_lab_smoke_benchmark.py",
        "tests/test_envelope_prompt_parity.py",
        "tests/test_lab_cache_invalidation.py",
    }
)
WORKFLOW_SELF_PREFIXES = (".github/workflows/test.yml", ".github/actions/")
MCP_PREFIXES = (
    "library-ioa/src/library_ioa/plugins/mcp/",
    "library-ioa/tests/mcp/",
    "library-ioa/compliance/mcp/",
)
A2A_PREFIXES = (
    "library-ioa/src/library_ioa/plugins/a2a/",
    "library-ioa/tests/a2a/",
    "library-ioa/compliance/a2a/",
)
A2A_EXACT = frozenset({"library-ioa/tests/test_a2a_instrumentation_pin.py"})


def default_base() -> str:
    env_base = os.environ.get("GITHUB_BASE_SHA") or os.environ.get("DIFF_BASE")
    if env_base and not set(env_base) <= {"0"}:
        return env_base
    for ref in ("origin/main", "main"):
        result = subprocess.run(
            ["git", "rev-parse", "--verify", ref],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return ref
    return "HEAD~1"


def changed_files(base: str, head: str = "HEAD") -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=ACMRT", f"{base}...{head}"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        raise SystemExit(result.returncode)
    files = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    if head == "HEAD":
        extra = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=ACMRT", "--cached"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        files.update(line.strip() for line in extra.stdout.splitlines() if line.strip())
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        files.update(line.strip() for line in untracked.stdout.splitlines() if line.strip())
    return sorted(files)


def _starts(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path.startswith(prefix) for prefix in prefixes)


def _enable_all(flags: dict[str, bool]) -> None:
    for name in CORE_JOBS:
        flags[name] = True


def classify(
    paths: list[str],
    *,
    event: str = "pull_request",
    force_all: bool = False,
) -> dict[str, bool]:
    flags = {name: False for name in JOBS}
    flags["lint"] = True
    if force_all:
        _enable_all(flags)
        flags["unit_311"] = True
        return flags

    for path in paths:
        posix = path.replace("\\", "/")

        if _starts(posix, WORKFLOW_SELF_PREFIXES) or posix == "Taskfile.yml":
            _enable_all(flags)
            continue
        if posix in {".github/workflows/compliance.yml"}:
            flags["mcp"] = True
            flags["a2a"] = True
            flags["lib_ioa"] = True
            continue
        if posix == ".github/workflows/nightly.yml":
            flags["runtime"] = True
            flags["ctl"] = True
            flags["lab_unit"] = True
            flags["repo"] = True
            flags["unit_311"] = True
            continue
        if posix.startswith(".github/"):
            flags["repo"] = True
            continue

        matched_lib = False
        for flag, prefix in LIBRARIES.items():
            if posix.startswith(prefix):
                flags[flag] = True
                if flag in GOLDEN_LIBRARIES:
                    flags["golden"] = True
                if flag == "lib_ioa":
                    if _starts(posix, MCP_PREFIXES):
                        flags["mcp"] = True
                    if _starts(posix, A2A_PREFIXES) or posix in A2A_EXACT:
                        flags["a2a"] = True
                matched_lib = True
                break
        if matched_lib:
            continue

        if posix.startswith("tests/tutorials/") or posix.startswith("docs/tutorials/"):
            flags["tutorials"] = True
            if posix.startswith("docs/"):
                flags["docs"] = True
            continue
        if posix.startswith("demo-ui/"):
            flags["demo_ui"] = True
            continue
        if posix.startswith("tests/test_hitl") or "/test_hitl" in posix:
            flags["hitl"] = True
            continue
        if posix in SMOKE_TESTS or posix.startswith("tests/fixtures/lab-smoke/"):
            flags["smoke"] = True
            continue
        if "golden" in posix and (posix.startswith("tests/") or posix.startswith("lab/")):
            flags["golden"] = True
            continue
        if posix.startswith("tests/test_reproduction") or posix.startswith("tests/fixtures/reproduction/"):
            flags["validate"] = True
            continue
        if posix.startswith("labs/"):
            flags["smoke"] = True
            flags["golden"] = True
            flags["validate"] = True
            continue
        if posix.startswith("lab/components/controller/"):
            flags["controller"] = True
            continue
        if posix.startswith("lab/components/bench/"):
            flags["lab_unit"] = True
            flags["smoke"] = True
            flags["golden"] = True
            flags["validate"] = True
            continue
        if posix.startswith("lab/"):
            flags["lab_unit"] = True
            flags["smoke"] = True
            flags["golden"] = True
            continue
        if posix.startswith("runtime/"):
            flags["runtime"] = True
            flags["hitl"] = True
            flags["smoke"] = True
            flags["golden"] = True
            continue
        if posix.startswith("ctl/"):
            flags["ctl"] = True
            flags["hitl"] = True
            flags["smoke"] = True
            flags["golden"] = True
            continue
        if posix in DOCS_EXACT or _starts(posix, DOCS_PREFIXES) or posix in DOCS_SCRIPTS:
            flags["docs"] = True
            continue
        if posix in {"pyproject.toml", "uv.lock"}:
            flags["runtime"] = True
            flags["ctl"] = True
            flags["lab_unit"] = True
            flags["repo"] = True
            flags["docs"] = True
            for flag in LIBRARIES:
                flags[flag] = True
            continue
        if posix == "lab/.importlinter":
            flags["lab_unit"] = True
            continue
        if posix.startswith("scripts/") or posix.startswith("tests/"):
            flags["repo"] = True
            continue

    flags["unit_311"] = any(flags[name] for name in UNIT_JOBS) and event != "pull_request"
    return flags


def _matrix_payload(
    rows: tuple[dict[str, str], ...],
    flags: dict[str, bool],
) -> dict[str, list[dict[str, str]]]:
    include = [{k: v for k, v in row.items() if k != "flag"} for row in rows if flags.get(row["flag"])]
    return {"include": include}


def format_outputs(flags: dict[str, bool]) -> str:
    lines = [f"{name}={'true' if flags[name] else 'false'}" for name in JOBS]
    suite = _matrix_payload(SUITE_ROWS, flags)
    library = _matrix_payload(LIBRARY_ROWS, flags)
    unit = _matrix_payload(UNIT_ROWS, flags)
    lines.append(f"suite={'true' if suite['include'] else 'false'}")
    lines.append(f"library={'true' if library['include'] else 'false'}")
    lines.append(f"unit={'true' if unit['include'] else 'false'}")
    lines.append(f"suite_matrix={json.dumps(suite, separators=(',', ':'))}")
    lines.append(f"library_matrix={json.dumps(library, separators=(',', ':'))}")
    lines.append(f"unit_matrix={json.dumps(unit, separators=(',', ':'))}")
    return "".join(line + "\n" for line in lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=None)
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--event", default=os.environ.get("GITHUB_EVENT_NAME", "pull_request"))
    args = parser.parse_args(argv)

    force_all = args.event in {"workflow_dispatch", "schedule"}
    paths = [] if force_all else changed_files(args.base or default_base(), args.head)
    flags = classify(paths, event=args.event, force_all=force_all)
    sys.stdout.write(format_outputs(flags))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
