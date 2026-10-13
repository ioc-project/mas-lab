#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from mas.lab.benchmark.pipeline.core import PipelineStep
from mas.lab.benchmark.pipeline.incremental import folds_batches
from mas.lab.benchmark.pipeline.models import StepOutput


class _Probe(PipelineStep):
    type = "probe"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.events: list[dict] = []

    def on_batch(self, batch, ctx):  # noqa: ANN001
        del ctx
        self.events.extend(batch)

    async def execute(self, ctx) -> StepOutput:
        return StepOutput(data={"n": len(self.events)})


def test_on_batch_folds_records() -> None:
    step = _Probe(name="p", config={})
    assert folds_batches(step)
    step.on_batch([{"kind": "tool"}], ctx=None)
    assert step.events[0]["kind"] == "tool"


def test_from_dict_does_not_require_streaming_key() -> None:
    from mas.lab.benchmark.pipeline.core import register_step_type

    register_step_type("probe_stream", _Probe)
    step = PipelineStep.from_dict({"name": "p", "type": "probe_stream"})
    assert folds_batches(step)
