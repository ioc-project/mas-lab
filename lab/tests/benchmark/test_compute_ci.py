#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for ComputeCIStep and data_source utilities.

Run with:
    cd lab && uv run pytest tests/benchmark/test_compute_ci.py -v
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
import pytest

from mas.lab.benchmark.pipeline import StepOutput
from mas.library.lab.steps.eval.compute_ci import ComputeCIStep, compute_ci
from mas.lab.benchmark.pipeline.lib.data_source import resolve_dataframe, write_dataframe


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tidy_df() -> pd.DataFrame:
    """3 runs × 3 scenarios × 2 item_groups × 2 metrics = 36 rows."""
    rows = []
    for run in range(3):
        for scenario in ["baseline", "vector", "letta"]:
            for item_group in ["recall", "neutral"]:
                for metric in ["goal_success_rate", "response_completeness"]:
                    rows.append(
                        {
                            "scenario": scenario,
                            "item_group": item_group,
                            "metric": metric,
                            "value": 0.6 + run * 0.05 + (0.1 if scenario != "baseline" else 0.0),
                            "run_idx": run,
                        }
                    )
    return pd.DataFrame(rows)


@dataclass
class FakeStepOutput:
    data: Dict[str, Any] = field(default_factory=dict)
    files: list = field(default_factory=list)


@dataclass
class FakeExecutionContext:
    step_outputs: Dict[str, FakeStepOutput] = field(default_factory=dict)
    output_dir: Optional[Path] = None


# ---------------------------------------------------------------------------
# Unit tests: standalone compute_ci()
# ---------------------------------------------------------------------------

VALUES = [0.8, 0.85, 0.82, 0.79, 0.83]


class TestComputeCi:
    def test_t_finite(self):
        mean, std, n, lo, hi, half = compute_ci(VALUES, "t", 0.95)
        assert math.isfinite(mean) and math.isfinite(lo) and math.isfinite(hi)
        assert lo < mean < hi
        assert half > 0

    def test_t_symmetric(self):
        mean, _, _, lo, hi, _ = compute_ci(VALUES, "t", 0.95)
        assert abs((mean - lo) - (hi - mean)) < 1e-9

    def test_normal_narrower_than_t_small_n(self):
        _, _, _, _, _, t_half = compute_ci(VALUES, "t", 0.95)
        _, _, _, _, _, z_half = compute_ci(VALUES, "normal", 0.95)
        assert t_half > z_half

    def test_bootstrap_reasonable(self):
        _, _, n, lo, hi, half = compute_ci(VALUES, "bootstrap", 0.95)
        assert n == len(VALUES)
        assert lo < hi
        assert half > 0

    def test_single_value_zero_half(self):
        _, _, _, lo, hi, half = compute_ci([0.9], "t", 0.95)
        assert half == 0.0
        assert lo == hi

    def test_empty_nan(self):
        mean, _, n, lo, hi, half = compute_ci([], "t", 0.95)
        assert math.isnan(mean)
        assert math.isnan(lo)
        assert n == 0

    def test_wider_at_higher_level(self):
        _, _, _, _, _, h90 = compute_ci(VALUES, "t", 0.90)
        _, _, _, _, _, h99 = compute_ci(VALUES, "t", 0.99)
        assert h99 > h90


# ---------------------------------------------------------------------------
# Unit tests: resolve_dataframe
# ---------------------------------------------------------------------------

class TestResolveDataframe:
    def test_reads_csv_file(self, tmp_path):
        df = _make_tidy_df()
        p = tmp_path / "data.csv"
        df.to_csv(p, index=False)
        result = resolve_dataframe(str(p))
        assert len(result) == len(df)

    def test_reads_parquet_file(self, tmp_path):
        pytest.importorskip("pyarrow", reason="pyarrow required for parquet support")
        df = _make_tidy_df()
        p = tmp_path / "data.parquet"
        df.to_parquet(p, index=False)
        result = resolve_dataframe(str(p))
        assert len(result) == len(df)

    def test_reads_json_file(self, tmp_path):
        df = _make_tidy_df()
        p = tmp_path / "data.json"
        df.to_json(p, orient="records")
        result = resolve_dataframe(str(p))
        assert len(result) == len(df)

    def test_in_memory_df_key(self):
        df = _make_tidy_df()
        ctx = FakeExecutionContext(
            step_outputs={"prior": FakeStepOutput(data={"df": df})}
        )
        result = resolve_dataframe("@prior", ctx)
        assert result is df

    def test_in_memory_custom_field(self):
        df = _make_tidy_df()
        ctx = FakeExecutionContext(
            step_outputs={"prior": FakeStepOutput(data={"my_df": df})}
        )
        result = resolve_dataframe("@prior:my_df", ctx)
        assert result is df

    def test_csv_path_field_requires_explicit_ref(self, tmp_path):
        """csv_path alone is not auto-resolved; steps must expose df or use @step:csv_path."""
        df = _make_tidy_df()
        p = tmp_path / "results.csv"
        df.to_csv(p, index=False)
        ctx = FakeExecutionContext(
            step_outputs={"prior": FakeStepOutput(data={"csv_path": str(p)})}
        )
        with pytest.raises(ValueError, match="no data field 'df'"):
            resolve_dataframe("@prior", ctx)
        result = resolve_dataframe(f"@prior:csv_path", ctx)
        assert len(result) == len(df)

    def test_missing_step_raises(self):
        ctx = FakeExecutionContext(step_outputs={})
        with pytest.raises(ValueError, match="not found in ctx.step_outputs"):
            resolve_dataframe("@missing", ctx)

    def test_missing_field_raises(self):
        ctx = FakeExecutionContext(
            step_outputs={"prior": FakeStepOutput(data={"other": "x"})}
        )
        with pytest.raises(ValueError, match="no data field"):
            resolve_dataframe("@prior:nonexistent", ctx)

    def test_empty_source_raises(self):
        with pytest.raises(ValueError, match="empty"):
            resolve_dataframe("")

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            resolve_dataframe(str(tmp_path / "ghost.csv"))

    def test_no_ctx_with_ref_raises(self):
        with pytest.raises(ValueError, match="requires an ExecutionContext"):
            resolve_dataframe("@some-step")


class TestWriteDataframe:
    def test_writes_csv(self, tmp_path):
        df = _make_tidy_df()
        p = write_dataframe(df, tmp_path / "out.csv")
        assert p.exists()
        result = pd.read_csv(p)
        assert len(result) == len(df)

    def test_writes_parquet(self, tmp_path):
        df = _make_tidy_df()
        p = write_dataframe(df, tmp_path / "out.parquet")
        assert p.exists()
        result = pd.read_parquet(p)
        assert len(result) == len(df)

    def test_creates_parent_dirs(self, tmp_path):
        df = _make_tidy_df()
        p = write_dataframe(df, tmp_path / "a" / "b" / "c.csv")
        assert p.exists()

    def test_fmt_override(self, tmp_path):
        df = _make_tidy_df()
        # File extension is .csv but fmt forces parquet
        p = tmp_path / "weird.csv"
        write_dataframe(df, p, fmt="parquet")
        result = pd.read_parquet(p)
        assert len(result) == len(df)


# ---------------------------------------------------------------------------
# Integration tests: ComputeCIStep.execute()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_compute_ci_step_from_csv_file(tmp_path):
    df = _make_tidy_df()
    csv_path = tmp_path / "results.csv"
    df.to_csv(csv_path, index=False)

    step = ComputeCIStep(
        name="compute-ci",
        config={
            "data": "results.csv",
            "metrics": ["goal_success_rate"],
            "groupby": ["scenario", "item_group", "metric"],
            "ci_method": "t",
            "ci_level": 0.95,
            "output": "ci_summary.csv",
        },
    )
    out = await step.execute(FakeExecutionContext(output_dir=tmp_path))

    assert "df" in out.data
    summary = out.data["df"]
    assert isinstance(summary, pd.DataFrame)
    # 3 scenarios × 2 item_groups × 1 metric = 6 groups
    assert len(summary) == 6
    assert {"mean", "ci_low", "ci_high", "ci_half", "n"}.issubset(summary.columns)
    assert (summary["n"] == 3).all()

    # Persistence file written
    assert len(out.files) == 1
    assert out.files[0].exists()


@pytest.mark.asyncio
async def test_compute_ci_step_from_in_memory(tmp_path):
    """data: @prior — reads DataFrame directly from a prior step's output."""
    df = _make_tidy_df()
    ctx = FakeExecutionContext(
        step_outputs={"collect-metrics": FakeStepOutput(data={"df": df})},
        output_dir=tmp_path,
    )

    step = ComputeCIStep(
        name="compute-ci",
        config={
            "data": "@collect-metrics",
            "groupby": ["scenario", "item_group", "metric"],
            "ci_method": "t",
            "ci_level": 0.95,
            # No output — in-memory only
        },
    )
    out = await step.execute(ctx)

    assert "df" in out.data
    assert isinstance(out.data["df"], pd.DataFrame)
    assert len(out.files) == 0  # no persistence


@pytest.mark.asyncio
async def test_compute_ci_step_prior_csv_path_ref(tmp_path):
    """data: @prior:csv_path reads a path field explicitly."""
    df = _make_tidy_df()
    csv_path = tmp_path / "results.csv"
    df.to_csv(csv_path, index=False)

    ctx = FakeExecutionContext(
        step_outputs={
            "collect-metrics": FakeStepOutput(data={"csv_path": str(csv_path)})
        },
        output_dir=tmp_path,
    )

    step = ComputeCIStep(
        name="compute-ci",
        config={
            "data": "@collect-metrics:csv_path",
            "groupby": ["scenario", "item_group", "metric"],
            "ci_method": "t",
            "ci_level": 0.95,
        },
    )
    out = await step.execute(ctx)
    assert isinstance(out.data["df"], pd.DataFrame)


@pytest.mark.asyncio
async def test_compute_ci_step_no_data_raises():
    step = ComputeCIStep(name="ci", config={})
    with pytest.raises(ValueError, match="'data' required"):
        await step.execute(FakeExecutionContext())


@pytest.mark.asyncio
async def test_compute_ci_step_missing_file_raises(tmp_path):
    step = ComputeCIStep(
        name="ci",
        config={"data": "ghost.csv", "groupby": ["scenario"]},
    )
    with pytest.raises(FileNotFoundError):
        await step.execute(FakeExecutionContext(output_dir=tmp_path))
