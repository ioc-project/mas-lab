#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from pathlib import Path

from mas.lab.benchmark.pipeline.models import StepOutput
from mas.lab.benchmark.pipeline.adapter import LegacyStepAdapter
from mas.lab.benchmark.pipeline.artifact import Artifact
from mas.lab.benchmark.pipeline.gather import gather_artifacts, gather_records
from mas.lab.benchmark.pipeline.keys import RunKey
from mas.lab.benchmark.pipeline.kinds import CONTROL_KINDS, is_control_kind, is_native_export_kind
from mas.lab.benchmark.pipeline.types import DATA


def test_control_kinds_reserved() -> None:
    assert "control.structure" in CONTROL_KINDS
    assert is_control_kind("control.structure")
    assert not is_native_export_kind("control.structure")
    assert not is_native_export_kind("run.terminal")
    assert is_native_export_kind("llm_call_end")


def test_gather_builtin_equals_gather_level(tmp_path: Path) -> None:
    import asyncio

    import pandas as pd

    from mas.library.lab.steps.data.gather_level import GatherLevelStep

    rows_a = [{"scenario": "a", "metric": "x", "value": 1}]
    rows_b = [{"scenario": "b", "metric": "x", "value": 2}]
    gathered = gather_records([rows_a, rows_b], annotate={"level": "item"})
    assert [r["value"] for r in gathered] == [1, 2]
    assert all(r["level"] == "item" for r in gathered)
    arts = []
    for i, rows in enumerate((rows_a, rows_b)):
        art = Artifact(name="data", key=RunKey("e", "s", "item1", f"r{i+1}"), spec=DATA)
        art.append(rows)
        art.seal()
        arts.append(art)
    bundled = gather_artifacts(arts)
    assert bundled.sealed
    assert len(bundled.records()) == 2

    p1 = tmp_path / "a.csv"
    p2 = tmp_path / "b.csv"
    pd.DataFrame(rows_a).to_csv(p1, index=False)
    pd.DataFrame(rows_b).to_csv(p2, index=False)
    step = GatherLevelStep(
        name="gather",
        config={
            "artifact_paths": [str(p1), str(p2)],
            "annotate": {"level": "item"},
            "output_dir": str(tmp_path),
            "output": "out.csv",
        },
    )
    ctx = type("C", (), {"output_dir": tmp_path, "step_outputs": {}})()
    out = asyncio.run(step.execute(ctx))
    frame = out.data["df"]
    from_step = frame.to_dict(orient="records")
    assert [r["value"] for r in from_step] == [r["value"] for r in gathered]
    assert all(r["level"] == "item" for r in from_step)


def test_native_export_kind_matches_jsonl_filter() -> None:
    from mas.library.standard.lib.observability.emit.jsonl import envelope_only_kind

    for kind in (
        "llm_call_end",
        "metric",
        "control.structure",
        "run.started",
        "artifact.append",
        "scope.progress",
        "",
    ):
        native = is_native_export_kind(kind)
        envelope = envelope_only_kind(kind)
        if not kind:
            assert native is False
        else:
            assert native is (not envelope)


def test_legacy_adapter_wraps_stepoutput() -> None:
    class _Step:
        type = "extract_trace_stats"
        config = {}
        name = "stats"

        async def execute(self, ctx):
            return StepOutput(data={"n_llm_calls": 2}, files=[])

    adapter = LegacyStepAdapter(_Step())
    import asyncio

    out = asyncio.run(adapter.execute(None, key=RunKey("e", "s", "i", "r1")))
    assert out.data["n_llm_calls"] == 2
    assert out.metadata.get("hit") is False
