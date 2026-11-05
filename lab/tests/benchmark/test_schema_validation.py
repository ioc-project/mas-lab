#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for pipeline schema validation (artifacts and step streams)."""

from __future__ import annotations

from pathlib import Path

import pytest
from mas.lab.benchmark.pipeline import Pipeline, PipelineConfig, PipelineStep, StepOutput
from mas.lab.benchmark.pipeline.executor import PipelineExecutor
from mas.lab.benchmark.pipeline.resources import Artifact


class _ProducerOk(PipelineStep):
    type = "producer_ok"

    async def execute(self, ctx) -> StepOutput:  # noqa: ANN001
        return StepOutput(data={"value": 3})


class _ProducerBad(PipelineStep):
    type = "producer_bad"

    async def execute(self, ctx) -> StepOutput:  # noqa: ANN001
        return StepOutput(data={"value": "oops"})


class _Consumer(PipelineStep):
    type = "consumer"

    async def execute(self, ctx) -> StepOutput:  # noqa: ANN001
        dep = ctx.get_dependency_output("producer")
        return StepOutput(data={"accepted": isinstance(dep.get("value"), int)})


@pytest.mark.asyncio
async def test_step_input_and_output_schema_validation_passes(tmp_path: Path):
    pipeline = Pipeline(
        config=PipelineConfig(name="schema-pass"),
        steps=[
            _ProducerOk(name="producer", config={}),
            _Consumer(
                name="consumer",
                depends_on=["producer"],
                config={
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "producer": {
                                "type": "object",
                                "properties": {"value": {"type": "integer"}},
                                "required": ["value"],
                            }
                        },
                        "required": ["producer"],
                    },
                    "output_schema": {
                        "type": "object",
                        "properties": {"accepted": {"const": True}},
                        "required": ["accepted"],
                    },
                },
            ),
        ],
    )
    result = await PipelineExecutor(pipeline, output_dir=tmp_path).run()
    assert result.success is True
    assert "consumer" in result.step_outputs


@pytest.mark.asyncio
async def test_step_input_schema_validation_fails(tmp_path: Path):
    pipeline = Pipeline(
        config=PipelineConfig(name="schema-fail"),
        steps=[
            _ProducerBad(name="producer", config={}),
            _Consumer(
                name="consumer",
                depends_on=["producer"],
                config={
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "producer": {
                                "type": "object",
                                "properties": {"value": {"type": "integer"}},
                                "required": ["value"],
                            }
                        },
                        "required": ["producer"],
                    }
                },
            ),
        ],
    )
    result = await PipelineExecutor(pipeline, output_dir=tmp_path).run()
    assert result.success is False
    assert "consumer" in result.failed_steps


def test_artifact_schema_validation_on_dump_and_load(tmp_path: Path):
    schema_path = tmp_path / "record.schema.json"
    schema_path.write_text(
        """
{
  "type": "object",
  "properties": {
    "run_id": {"type": "string"},
    "score": {"type": "number"}
  },
  "required": ["run_id", "score"]
}
""".strip(),
        encoding="utf-8",
    )

    artifact = Artifact(
        name="metrics",
        format="json",
        schema=str(schema_path),
    )
    path = tmp_path / "metrics.json"

    artifact.dump({"run_id": "r1", "score": 0.75}, path)
    loaded = artifact.load(path)
    assert loaded["run_id"] == "r1"

    with pytest.raises(ValueError, match="schema validation failed"):
        artifact.dump({"run_id": "r2", "score": "bad"}, path)
