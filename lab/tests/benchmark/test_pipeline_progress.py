#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from pathlib import Path

import pytest

from mas.lab.benchmark.pipeline import Pipeline, PipelineConfig, PipelineStep, StepOutput
from mas.lab.benchmark.pipeline.executor import PipelineExecutor


class _WriteFile(PipelineStep):
    type = "write_file"

    async def execute(self, ctx) -> StepOutput:  # noqa: ANN001
        name = str(self.config.get("filename") or "out.txt")
        path = ctx.output_dir / name
        path.write_text("ok", encoding="utf-8")
        return StepOutput(data={"wrote": name}, files=[path])


@pytest.mark.asyncio
async def test_progress_rewrites_line_and_logs_artifacts(tmp_path: Path, capsys) -> None:
    pipeline = Pipeline(
        config=PipelineConfig(name="progress"),
        steps=[
            *[
                _WriteFile(name=f"write-{i}", config={"filename": "out.txt"})
                for i in range(1, 5)
            ],
            _WriteFile(
                name="figure",
                config={"filename": "figure.png"},
            ),
        ],
    )
    result = await PipelineExecutor(
        pipeline, output_dir=tmp_path, progress=True
    ).run()
    captured = capsys.readouterr().out
    assert result.success
    assert "100/500 200/500" not in captured
    assert "out.txt ×4" in captured
    assert "figure.png ×1" in captured
    assert "Time:" in result.summary()
    assert "Artifacts:" in result.summary()
    assert result.artifacts["out.txt"] == 4
    assert result.artifacts["figure.png"] == 1
    assert any(typ == "write_file" for typ, _ms, _n in result.step_timings)
