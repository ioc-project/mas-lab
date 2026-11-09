#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Benchmark must fail when post-pipeline steps fail."""

from __future__ import annotations

import contextlib
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SMOKE_EXP = REPO_ROOT / "tests/fixtures/lab-smoke/experiment.yaml"


@pytest.mark.asyncio
async def test_finalize_batch_fails_when_post_pipeline_raises(tmp_path) -> None:
    from mas.lab.benchmark.schedule.pipeline import PipelineExecutionError
    from mas.lab.benchmark.schedule.run_batch.execute import ExecutionResult
    from mas.lab.benchmark.schedule.run_batch.finalize import finalize_batch
    from mas.lab.benchmark.schedule.run_batch.load import load_experiment
    from mas.lab.benchmark.schedule.run_batch.prepare import prepare_batch

    loaded = load_experiment(SMOKE_EXP, single_run=True, max_runs=1)
    assert loaded is not None
    prepared = await prepare_batch(loaded, output_dir=tmp_path, force=True)
    execution = ExecutionResult(
        results_rows=[],
        total_ok=1,
        total_fail=0,
        exit_stack=contextlib.ExitStack(),
    )

    with patch(
        "mas.lab.benchmark.schedule.pipeline.run_pipeline_phase",
        new_callable=AsyncMock,
        side_effect=PipelineExecutionError("plot step failed"),
    ):
        ok = await finalize_batch(loaded, prepared, execution, progress=False)
    assert ok is False

    meta_path = prepared.output_dir / "metadata.yaml"
    assert meta_path.is_file()
    meta_text = meta_path.read_text(encoding="utf-8")
    assert "failed" in meta_text
