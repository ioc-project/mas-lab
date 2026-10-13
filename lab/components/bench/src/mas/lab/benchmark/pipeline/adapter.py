#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Wrap a PipelineStep so the scheduler can run it unchanged."""

from __future__ import annotations

from typing import Any

from mas.lab.benchmark.pipeline.models import StepOutput
from mas.lab.benchmark.pipeline.artifact import ArtifactContext, wrap_step_output
from mas.lab.benchmark.pipeline.incremental import step_version
from mas.lab.benchmark.pipeline.keys import RunKey
from mas.lab.benchmark.pipeline.store import ArtifactStore, StoreManifest, compute_node_key


class LegacyStepAdapter:
    def __init__(self, step: Any, *, store: ArtifactStore | None = None) -> None:
        self.step = step
        self.store = store
        try:
            self.resource = str(step.resource or "cpu") or "cpu"
        except AttributeError:
            self.resource = "cpu"
        self.VERSION = step_version(step)

    async def execute(self, ctx: Any, *, key: RunKey, input_digests: list[str] | None = None) -> StepOutput:
        input_digests = list(input_digests or [])
        config = dict(self.step.config or {})
        nkey = compute_node_key(str(self.step.type or type(self.step).__name__), self.VERSION, config, *input_digests)
        if self.store is not None and self.store.exists(nkey):
            hit = self.store.get(nkey)
            return StepOutput(data={"cache": "hit", "key": nkey}, metadata={"node_key": nkey, "hit": True, "manifest": hit})
        result: StepOutput = await self.step.execute(ctx)
        wrap_step_output(result.data, result.files, key, name=str(self.step.type or "value"))
        if self.store is not None:
            files = {path.name: path for path in (result.files or []) if path.name}
            self.store.put(
                StoreManifest(
                    key=nkey,
                    step_type=str(self.step.type or ""),
                    version=self.VERSION,
                    config=config,
                    input_digests=input_digests,
                    extra={"step": self.step.name},
                ),
                files=files,
            )
        result.metadata = {**(result.metadata or {}), "node_key": nkey, "hit": False}
        return result

    def bind_artifacts(self, arts: ArtifactContext) -> None:
        self.step._stream_artifacts = arts
