#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from mas.lab.benchmark.pipeline.resources import ScopeContext
from mas.library.lab.steps.data.gather_level import GatherLevelStep


@pytest.mark.asyncio
async def test_gather_level_copies_scope_columns(tmp_path: Path) -> None:
    csv = tmp_path / "child" / "data.csv"
    csv.parent.mkdir()
    pd.DataFrame([{"metric": "gsr", "value": 1.0}]).to_csv(csv, index=False)

    step = GatherLevelStep(
        name="gather",
        config={
            "output_dir": str(tmp_path),
            "output": "data.csv",
            "artifact_paths": [str(csv)],
        },
    )
    ctx = SimpleNamespace(
        output_dir=tmp_path,
        step_outputs={},
        pipeline=None,
        scope_context=ScopeContext(experiment="e1", scenario="s1", test="t1", run="r0"),
    )
    out = await step.execute(ctx)
    frame = out.data["df"]
    assert list(frame["scenario"]) == ["s1"]
    assert list(frame["run"]) == ["r0"]


@pytest.mark.asyncio
async def test_gather_level_empty_paths_raise(tmp_path: Path) -> None:
    step = GatherLevelStep(name="gather", config={"output_dir": str(tmp_path), "artifact_paths": []})
    ctx = SimpleNamespace(output_dir=tmp_path, step_outputs={}, pipeline=None, scope_context=None)
    with pytest.raises(RuntimeError, match="fan-in"):
        await step.execute(ctx)
