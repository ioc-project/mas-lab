#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Completeness summary of a results tree: per scenario, what finished and what did not."""

from __future__ import annotations

import json
from typing import Any

from mas.lab.benchmark.execution.roles import AnalysisContext, NoParams
from mas.lab.benchmark.execution.wire import read_results


class NativeAnalyzer(NoParams):
    plugin_id = "native"

    def analyze(self, ctx: AnalysisContext) -> dict[str, Any]:
        results = read_results(ctx.results_root, ctx.runs, ctx.dir_names)
        scenarios: dict[str, dict[str, int]] = {}
        for run in ctx.runs:
            row = scenarios.setdefault(run.scenario_id, {"planned": 0, "complete": 0, "error": 0, "missing": 0})
            row["planned"] += 1
            status = results[run.id]
            row["complete" if status.complete else ("missing" if status.status == "missing" else "error")] += 1
        incomplete = sorted(ident for ident, res in results.items() if not res.complete)
        report = {
            "planned": len(ctx.runs),
            "complete": sum(1 for res in results.values() if res.complete),
            "incomplete": incomplete,
            "scenarios": scenarios,
        }
        out = ctx.results_root / "analysis"
        out.mkdir(parents=True, exist_ok=True)
        (out / "summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        return report
