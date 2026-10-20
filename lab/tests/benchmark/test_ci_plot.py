#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for CIPlotStep — confidence interval computation and step output.

Run with:
    cd lab && uv run pytest tests/benchmark/test_ci_plot.py -v
"""
from __future__ import annotations

import csv
import math
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Unit tests for _compute_ci (no dependencies beyond scipy/numpy)
# ---------------------------------------------------------------------------

from mas.library.lab.steps.viz.ci_plot import _compute_ci


class TestComputeCI:
    """Test _compute_ci for all three methods."""

    VALUES_5 = [0.8, 0.85, 0.82, 0.79, 0.83]  # n=5

    def test_t_returns_finite(self):
        mean, std, n, ci_low, ci_high, ci_half = _compute_ci(self.VALUES_5, "t", 0.95)
        assert math.isfinite(mean)
        assert math.isfinite(ci_low)
        assert math.isfinite(ci_high)
        assert ci_half > 0
        assert ci_low < mean < ci_high

    def test_t_symmetric(self):
        mean, std, n, ci_low, ci_high, ci_half = _compute_ci(self.VALUES_5, "t", 0.95)
        assert abs((mean - ci_low) - (ci_high - mean)) < 1e-9

    def test_normal_wider_than_t_for_small_n(self):
        # t-distribution has heavier tails → t CI should be wider than normal for n=5
        _, _, _, _, _, t_half = _compute_ci(self.VALUES_5, "t", 0.95)
        _, _, _, _, _, z_half = _compute_ci(self.VALUES_5, "normal", 0.95)
        assert t_half > z_half

    def test_bootstrap_reasonable(self):
        mean, std, n, ci_low, ci_high, ci_half = _compute_ci(self.VALUES_5, "bootstrap", 0.95)
        assert ci_half > 0
        assert ci_low < mean < ci_high

    def test_single_value_zero_half(self):
        mean, std, n, ci_low, ci_high, ci_half = _compute_ci([0.9], "t", 0.95)
        assert ci_half == 0.0
        assert ci_low == ci_high == mean

    def test_empty_returns_nan(self):
        mean, std, n, ci_low, ci_high, ci_half = _compute_ci([], "t", 0.95)
        assert math.isnan(mean)
        assert math.isnan(ci_low)
        assert math.isnan(ci_half)

    def test_higher_level_wider(self):
        _, _, _, _, _, half_90 = _compute_ci(self.VALUES_5, "t", 0.90)
        _, _, _, _, _, half_99 = _compute_ci(self.VALUES_5, "t", 0.99)
        assert half_99 > half_90


# ---------------------------------------------------------------------------
# Integration tests for CIPlotStep (mocked execution context)
# ---------------------------------------------------------------------------

class FakeExecutionContext:
    def __init__(self, output_dir=None):
        self.output_dir = output_dir


def _write_fake_results(path: Path) -> None:
    """Write a minimal tidy CSV with 3 runs × 3 scenarios × 2 items × 2 metrics."""
    rows = []
    scenarios = ["baseline", "with-vector-memory", "with-letta-memory"]
    item_groups = ["recall", "neutral"]
    metrics = ["goal_success_rate", "response_completeness"]
    for run in range(3):
        for scenario in scenarios:
            for item_group in item_groups:
                for metric in metrics:
                    rows.append({
                        "scenario": scenario,
                        "item_group": item_group,
                        "metric": metric,
                        "value": 0.7 + run * 0.05 if scenario != "baseline" else 0.5,
                        "run_idx": run,
                    })
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["scenario", "item_group", "metric", "value", "run_idx"])
        writer.writeheader()
        writer.writerows(rows)


@pytest.mark.asyncio
async def test_ci_step_writes_summary_csv(tmp_path):
    import asyncio
    from mas.library.lab.steps.viz.ci_plot import CIPlotStep

    data_csv = tmp_path / "results.csv"
    _write_fake_results(data_csv)

    step = CIPlotStep(
        name="ci-test",
        config={
            "data": "results.csv",
            "metrics": ["goal_success_rate"],
            "groupby": ["scenario", "item_group", "metric"],
            "ci_method": "t",
            "ci_level": 0.95,
        },
    )
    output = await step.execute(FakeExecutionContext(tmp_path))

    summary_path = tmp_path / "ci_summary.csv"
    assert summary_path.exists(), "ci_summary.csv should be created"
    assert str(summary_path) in [str(f) for f in output.files]

    import csv as _csv
    with summary_path.open() as f:
        rows = list(_csv.DictReader(f))
    # 3 scenarios × 2 item_groups × 1 metric = 6 rows
    assert len(rows) == 6
    for row in rows:
        assert "mean" in row
        assert "ci_low" in row
        assert "ci_half" in row
        assert float(row["n"]) == 3


@pytest.mark.asyncio
async def test_ci_step_warns_on_single_run(tmp_path, caplog):
    import logging
    from mas.library.lab.steps.viz.ci_plot import CIPlotStep

    data_csv = tmp_path / "results.csv"
    # Write only 1 run
    with data_csv.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["scenario", "metric", "value"])
        w.writeheader()
        w.writerow({"scenario": "baseline", "metric": "goal_success_rate", "value": 0.7})

    step = CIPlotStep(
        name="ci-warn",
        config={
            "data": "results.csv",
            "groupby": ["scenario", "metric"],
            "ci_method": "t",
            "ci_level": 0.95,
            "min_runs": 2,
        },
    )
    with caplog.at_level(logging.WARNING, logger="mas.library.lab.steps.viz.ci_plot"):
        await step.execute(FakeExecutionContext(tmp_path))

    assert any("only 1 run" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_ci_step_missing_data_raises(tmp_path):
    from mas.library.lab.steps.viz.ci_plot import CIPlotStep

    step = CIPlotStep(
        name="ci-missing",
        config={"data": "nonexistent.csv", "groupby": ["scenario"]},
    )
    with pytest.raises(FileNotFoundError):
        await step.execute(FakeExecutionContext(tmp_path))


@pytest.mark.asyncio
async def test_ci_step_invalid_metric_filter_raises(tmp_path):
    from mas.library.lab.steps.viz.ci_plot import CIPlotStep

    data_csv = tmp_path / "results.csv"
    with data_csv.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["scenario", "metric", "value"])
        w.writeheader()
        w.writerow({"scenario": "baseline", "metric": "goal_success_rate", "value": 0.7})

    step = CIPlotStep(
        name="ci-bad-filter",
        config={
            "data": "results.csv",
            "metrics": ["nonexistent_metric"],
            "groupby": ["scenario"],
        },
    )
    with pytest.raises(ValueError, match="no rows remain"):
        await step.execute(FakeExecutionContext(tmp_path))
