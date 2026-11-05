#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from pathlib import Path

import pytest
from mas.lab.benchmark.reproducibility import OutputSchema
from mas.library.lab.steps.eval.validate_outputs import ValidateOutputsStep


def test_output_schema_validate_passes_when_files_and_columns_present(tmp_path: Path) -> None:
    (tmp_path / "results").mkdir()
    (tmp_path / "results" / "ci_summary.csv").write_text("scenario,metric,mean\nbaseline,gsr,0.8\n", encoding="utf-8")
    schema = OutputSchema(
        required_files=["results/ci_summary.csv"],
        required_columns={"results/ci_summary.csv": ["scenario", "metric", "mean"]},
    )
    assert OutputSchema.validate(tmp_path, schema) is True


def test_output_schema_validate_reports_missing_file(tmp_path: Path) -> None:
    schema = OutputSchema(required_files=["results/ci_summary.csv"])
    assert OutputSchema.validate(tmp_path, schema, warn_only=True) is False
    with pytest.raises(ValueError, match="Missing required file"):
        OutputSchema.validate(tmp_path, schema, warn_only=False)


def test_output_schema_validate_reports_missing_column(tmp_path: Path) -> None:
    (tmp_path / "data.csv").write_text("scenario,metric\nbaseline,gsr\n", encoding="utf-8")
    schema = OutputSchema(required_columns={"data.csv": ["scenario", "metric", "mean"]})
    with pytest.raises(ValueError, match="Missing columns"):
        OutputSchema.validate(tmp_path, schema, warn_only=False)


@pytest.mark.asyncio
async def test_validate_outputs_step_skips_without_schema(tmp_path: Path) -> None:
    step = ValidateOutputsStep(name="validate", config={})

    class _Ctx:
        output_dir = tmp_path

    out = await step.execute(_Ctx())  # type: ignore[arg-type]
    assert out.data["validation_passed"] is True


@pytest.mark.asyncio
async def test_validate_outputs_step_warn_only_reports_failure(tmp_path: Path) -> None:
    step = ValidateOutputsStep(
        name="validate",
        config={
            "schema": {"required_files": ["results/ci_summary.csv"]},
            "warn_only": True,
        },
    )

    class _Ctx:
        output_dir = tmp_path

    out = await step.execute(_Ctx())  # type: ignore[arg-type]
    assert out.data["validation_passed"] is False


@pytest.mark.asyncio
async def test_validate_outputs_step_raises_when_not_warn_only(tmp_path: Path) -> None:
    step = ValidateOutputsStep(
        name="validate",
        config={
            "schema": {"required_files": ["results/ci_summary.csv"]},
            "warn_only": False,
        },
    )

    class _Ctx:
        output_dir = tmp_path

    with pytest.raises(ValueError, match="Missing required file"):
        await step.execute(_Ctx())  # type: ignore[arg-type]
