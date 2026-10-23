#!/usr/bin/env python3
#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Decide which CI jobs a changed-path set should run.

Prints ``key=true|false`` lines for GitHub Actions ``GITHUB_OUTPUT``.
Lint always runs. Libraries, labs, and docs are independent flags.
"""

from __future__ import annotations

import argparse
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
    "unit",
    "unit_311",
    "controller",
    "smoke",
    "golden",
    "tutorials",
    "validate",
    "hitl",
    "docs",
    "demo_ui",
    "ioa",
    *LIBRARIES,
)

SUITE_JOBS = ("controller", "smoke", "golden", "tutorials", "validate", "hitl")
CORE_JOBS = ("unit", *SUITE_JOBS, "docs", "demo_ui", "ioa", *LIBRARIES)

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
            flags["ioa"] = True
            flags["lib_ioa"] = True
            continue
        if posix == ".github/workflows/nightly.yml":
            flags["unit"] = True
            flags["unit_311"] = True
            continue
        if posix.startswith(".github/"):
            flags["unit"] = True
            continue

        matched_lib = False
        for flag, prefix in LIBRARIES.items():
            if posix.startswith(prefix):
                flags[flag] = True
                if flag == "lib_ioa":
                    flags["ioa"] = True
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
            flags["unit"] = True
            flags["smoke"] = True
            flags["golden"] = True
            flags["validate"] = True
            continue
        if posix.startswith("lab/"):
            flags["unit"] = True
            flags["smoke"] = True
            continue
        if posix.startswith("runtime/") or posix.startswith("ctl/"):
            flags["unit"] = True
            flags["hitl"] = True
            flags["smoke"] = True
            continue
        if posix in DOCS_EXACT or _starts(posix, DOCS_PREFIXES) or posix in DOCS_SCRIPTS:
            flags["docs"] = True
            continue
        if posix in {"pyproject.toml", "uv.lock"}:
            flags["unit"] = True
            flags["docs"] = True
            for flag in LIBRARIES:
                flags[flag] = True
            continue
        if posix.startswith("scripts/") or posix.startswith("tests/") or posix == "lab/.importlinter":
            flags["unit"] = True
            continue

    flags["unit_311"] = flags["unit"] and event != "pull_request"
    return flags


def format_outputs(flags: dict[str, bool]) -> str:
    return "".join(f"{name}={'true' if flags[name] else 'false'}\n" for name in JOBS)


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
