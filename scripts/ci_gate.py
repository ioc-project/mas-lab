#!/usr/bin/env python3
#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Pass or fail the always-on ci-summary aggregator.

``changes``, ``lint`` and ``docs-links`` must succeed. A path flag of true means that job
must succeed (skipped is a failure). A path flag of false allows skipped.
"""

from __future__ import annotations

import json
import os
import sys

ALWAYS_SUCCESS = ("changes", "lint", "docs-links")
PASS = frozenset({"success", "skipped"})

# changes.outputs key → GitHub job id. Several flags share a matrix job.
FLAG_JOB = {
    "unit": "unit",
    "runtime": "unit",
    "ctl": "unit",
    "lab_unit": "unit",
    "repo": "unit",
    "unit_311": "unit-311",
    "controller": "suite",
    "smoke": "suite",
    "golden": "suite",
    "tutorials": "suite",
    "validate": "suite",
    "hitl": "suite",
    "docs": "docs",
    "demo_ui": "demo-ui",
    "mcp": "compliance",
    "a2a": "compliance",
    "lib_standard": "library",
    "lib_eval": "library",
    "lib_ioa": "library",
    "lib_telemetry": "library",
    "lib_kg": "library",
    "lib_skills": "library",
    "lib_samples": "library",
    "lib_lab": "library",
}


def _truthy(value: object) -> bool:
    return value in (True, "true", "True", "1", 1)


def expected_jobs(flags: dict[str, object]) -> set[str]:
    jobs: set[str] = set()
    for flag, job in FLAG_JOB.items():
        if _truthy(flags.get(flag, False)):
            jobs.add(job)
    return jobs


def evaluate(
    needs: dict[str, dict],
    flags: dict[str, object] | None = None,
) -> tuple[int, str]:
    if not needs:
        return 1, "ci-summary received an empty needs context."

    if flags:
        expected = expected_jobs(flags)
    else:
        expected = set(needs)

    lines: list[str] = []
    failed: list[str] = []
    for name in sorted(needs):
        result = str(needs[name].get("result", ""))
        want = "required" if name in ALWAYS_SUCCESS or name in expected else "optional"
        lines.append(f"{name}: {result} ({want})")
        if name in ALWAYS_SUCCESS or name in expected:
            if result != "success":
                failed.append(name)
        elif result not in PASS:
            failed.append(name)

    report = "\n".join(lines)
    if failed:
        return 1, report + "\nfailed: " + ", ".join(failed)
    return 0, report + "\nci-summary passed."


def main() -> int:
    raw = (os.environ.get("NEEDS") or os.environ.get("NEEDS_JSON") or "").strip()
    if not raw:
        print("NEEDS is empty", file=sys.stderr)
        return 2
    needs = json.loads(raw)
    if not isinstance(needs, dict):
        print("NEEDS must be an object", file=sys.stderr)
        return 2
    expected_raw = (os.environ.get("EXPECTED") or "").strip()
    flags: dict[str, object] | None = None
    if expected_raw:
        parsed = json.loads(expected_raw)
        if not isinstance(parsed, dict):
            print("EXPECTED must be an object", file=sys.stderr)
            return 2
        flags = parsed
    code, report = evaluate(needs, flags)
    print(report)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
