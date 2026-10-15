#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from mas.lab.benchmark.pipeline.incremental import folds_batches
from mas.lab.benchmark.schedule.pipeline import _step_dict
from mas.lab.lab.config import PipelineStepSpec


class _Fold:
    def on_batch(self, batch, ctx):  # noqa: ANN001
        del batch, ctx


class _Batch:
    async def execute(self, ctx):  # noqa: ANN001
        del ctx


def test_folds_batches_from_class_on_batch() -> None:
    assert folds_batches(_Fold())
    assert not folds_batches(_Batch())


def test_step_spec_ignores_streaming_flags() -> None:
    spec = PipelineStepSpec.from_dict(
        {"name": "file-tail", "type": "file_tail", "realtime": True, "streaming": True}
    )
    assert "streaming" not in spec.__dataclass_fields__
    out = _step_dict(spec, spec.name or spec.type, {}, [])
    assert "realtime" not in out
    assert "streaming" not in out
    batch = PipelineStepSpec.from_dict({"name": "eval", "type": "eval_mce"})
    assert _step_dict(batch, batch.name or batch.type, {}, [])["resource"] == "cpu"


def test_start_pipeline_scheduler_noop_without_specs() -> None:
    from mas.lab.benchmark.schedule.pipeline import start_pipeline_scheduler

    assert (
        start_pipeline_scheduler(
            exp=type("E", (), {"name": "demo"})(),
            experiment_yaml=__import__("pathlib").Path("experiment.yaml"),
            output_dir=__import__("pathlib").Path("."),
            specs=[],
            keys=[],
            scenario_ids=[],
        )
        is None
    )
