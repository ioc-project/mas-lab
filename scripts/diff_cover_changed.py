#!/usr/bin/env python3
#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Fail if changed executable lines in coverage-measured packages are under the gate.

Reads a Cobertura ``coverage.xml`` (pytest-cov) and ``git diff -U0 base...head``.
Only packages listed in ``[tool.coverage.run] source`` are gated. Test modules,
scripts, and other trees are ignored. Lines that coverage.xml does not list as
executable (comments, blanks) are ignored when the file is in the report.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Paths that correspond to pyproject.toml [tool.coverage.run] source.
COVERED_PREFIXES = (
    "runtime/src/mas/runtime/kernel/",
    "runtime/src/mas/runtime/machines/",
    "ctl/src/mas/ctl/workspace/",
    "lab/components/bench/src/mas/lab/benchmark/pipeline/",
)


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


def parse_cobertura(xml_text: str) -> dict[str, dict[int, int]]:
    """Map coverage filename → {lineno: hits}."""
    root = ET.fromstring(xml_text)
    coverage: dict[str, dict[int, int]] = {}
    for cls in root.iter("class"):
        filename = (cls.get("filename") or "").replace("\\", "/")
        if not filename:
            continue
        lines = coverage.setdefault(filename, {})
        for line in cls.iter("line"):
            number = line.get("number")
            hits = line.get("hits")
            if number is None or hits is None:
                continue
            lines[int(number)] = int(hits)
    return coverage


def parse_unified_diff(diff_text: str) -> dict[str, set[int]]:
    """Map repo-relative path → added line numbers in the new file."""
    changed: dict[str, set[int]] = {}
    current: str | None = None
    new_lineno = 0
    for raw in diff_text.splitlines():
        if raw.startswith("+++ "):
            token = raw[4:].strip()
            if token == "/dev/null":
                current = None
                continue
            if token.startswith("b/"):
                token = token[2:]
            current = token
            changed.setdefault(current, set())
            continue
        if raw.startswith("@@") and current is not None:
            plus = raw.split("+", 1)[-1].split("@@", 1)[0].strip()
            start = plus.split(",", 1)[0]
            new_lineno = int(start)
            continue
        if current is None:
            continue
        if raw.startswith("+"):
            changed[current].add(new_lineno)
            new_lineno += 1
        elif raw.startswith("-"):
            continue
        elif raw.startswith("\\"):
            continue
        else:
            new_lineno += 1
    return {path: lines for path, lines in changed.items() if lines}


def coverage_for_path(coverage: dict[str, dict[int, int]], git_path: str) -> dict[int, int] | None:
    posix = git_path.replace("\\", "/")
    candidates = [posix]
    if "/src/" in posix:
        candidates.append(posix.split("/src/", 1)[1])
    for candidate in candidates:
        if candidate in coverage:
            return coverage[candidate]
    for key, lines in coverage.items():
        key_posix = key.replace("\\", "/")
        if posix.endswith(key_posix) or key_posix.endswith(posix):
            return lines
    return None


def is_measured_path(git_path: str, prefixes: tuple[str, ...] = COVERED_PREFIXES) -> bool:
    posix = git_path.replace("\\", "/")
    return posix.endswith(".py") and any(posix.startswith(prefix) for prefix in prefixes)


def evaluate(
    coverage: dict[str, dict[int, int]],
    changed_lines: dict[str, set[int]],
    fail_under: float,
    prefixes: tuple[str, ...] = COVERED_PREFIXES,
) -> tuple[int, str]:
    measured = 0
    hit = 0
    missing: list[str] = []
    skipped: list[str] = []

    for path in sorted(changed_lines):
        lines = changed_lines[path]
        if not path.endswith(".py"):
            continue
        if not is_measured_path(path, prefixes):
            skipped.append(path)
            continue
        hits = coverage_for_path(coverage, path) or {}
        executable = {lineno for lineno in lines if lineno in hits} if hits else set(lines)
        if not executable and hits:
            continue
        if not executable:
            executable = set(lines)
        for lineno in sorted(executable):
            measured += 1
            if hits.get(lineno, 0) > 0:
                hit += 1
            else:
                missing.append(f"{path}:{lineno}")

    if measured == 0:
        extra = ""
        if skipped:
            extra = " Skipped non-measured paths: " + ", ".join(skipped[:8])
        return 0, "No changed executable lines in coverage-measured packages; skipping gate." + extra

    pct = 100.0 * hit / measured
    report = [f"Changed-line coverage: {hit}/{measured} ({pct:.1f}%)"]
    if missing:
        report.append("Uncovered changed lines:")
        report.extend(f"  {item}" for item in missing[:50])
        if len(missing) > 50:
            report.append(f"  … {len(missing) - 50} more")
    text = "\n".join(report)
    if pct + 1e-9 < fail_under:
        return 1, text + f"\nFAIL: changed-line coverage {pct:.1f}% is under {fail_under:g}%."
    return 0, text + f"\nPASS: changed-line coverage meets {fail_under:g}%."


def git_diff(base: str, head: str) -> str:
    result = subprocess.run(
        ["git", "diff", "-U0", "--diff-filter=ACMRT", f"{base}...{head}"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        raise SystemExit(result.returncode)
    return result.stdout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage", default="coverage.xml", help="Cobertura XML from pytest-cov")
    parser.add_argument("--base", default=None, help="Base git ref or SHA")
    parser.add_argument("--head", default="HEAD", help="Head git ref or SHA")
    parser.add_argument("--fail-under", type=float, default=90.0)
    parser.add_argument(
        "--prefix",
        action="append",
        default=None,
        help="Repo-relative src prefix to measure (repeatable). Default: kernel/machines/workspace/pipeline",
    )
    args = parser.parse_args(argv)

    coverage_path = Path(args.coverage)
    if not coverage_path.is_file():
        print(f"coverage file not found: {coverage_path}", file=sys.stderr)
        return 2

    prefixes = tuple(args.prefix) if args.prefix else COVERED_PREFIXES
    base = args.base or default_base()
    coverage = parse_cobertura(coverage_path.read_text(encoding="utf-8"))
    changed = parse_unified_diff(git_diff(base, args.head))
    code, report = evaluate(coverage, changed, args.fail_under, prefixes)
    print(report)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
