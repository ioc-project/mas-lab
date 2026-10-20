#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations
"""ProcessorStep — wraps any :class:`~mas.lab.processor.Processor` as a pipeline step.

YAML syntax
-----------

**Inline input** (no explicit dependency)::

    - name: plot-baseline
      type: processor
      processor: trajectory_plotter
      config:
        input: "20260224-140201-baseline-e60feafd"   # run_id or file path
        format: html
        highlights: ["f19445b6"]
        output: "trajectories/baseline.html"

**Chained from previous step** (artifact flows via step output)::

    - name: load
      type: processor
      processor: trajectory_loader
      config:
        input: "20260224-140201-baseline-e60feafd"

    - name: annotate
      type: processor
      processor: trajectory_annotator
      depends_on: [load]
      config:
        highlights: ["f19445b6", "3"]
        notes: ["1:Slow telemetry fetch"]

    - name: render
      type: processor
      processor: trajectory_plotter
      depends_on: [annotate]
      config:
        format: html
        output: "trajectories/baseline.html"

Artifact chaining
-----------------
When a ProcessorStep depends on another ProcessorStep, it takes the artifact
from ``ctx.step_outputs[dep_name].data["artifact"]``.
If the processor's ``input_kind`` is ``"path"``, the step first wraps the
``config.input`` value in the appropriate artifact.
"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from mas.lab.benchmark.pipeline.artifact_paths import resolve_step_artifact_path
from mas.lab.benchmark.pipeline import PipelineStep, StepOutput

if TYPE_CHECKING:
    from mas.lab.benchmark.pipeline.executor import ExecutionContext

logger = logging.getLogger(__name__)


class ProcessorStep(PipelineStep):
    """Pipeline step that delegates execution to a registered :class:`Processor`.

    Config keys
    -----------
    input : str | Path, optional
        Inline input: a run_id string, a JSONL file path, or another
        path-like value.  Used when there is no upstream ProcessorStep.
    output : str | Path, optional
        Relative destination path for file-backed output artifacts, under the
        level folder.
    Any additional key is forwarded verbatim to ``Processor.process()``.
    """

    type = "processor"

    def __init__(
        self,
        name: str,
        config: Dict[str, Any],
        depends_on: Optional[List[str]] = None,
        phase: str = "post",
    ):
        super().__init__(name, config, depends_on, phase=phase)
        self._processor_name: str = config.get("processor", "")
        if not self._processor_name:
            raise ValueError(
                f"ProcessorStep '{name}': 'processor' key is required in config"
            )

    async def execute(self, ctx: ExecutionContext) -> StepOutput:
        from mas.lab.processor import get_processor
        from mas.lab.artifacts import Artifact, Trajectory

        # ── 1. Resolve input artifact ──────────────────────────────────────
        artifact = self._resolve_input(ctx)

        # ── 2. Instantiate processor ───────────────────────────────────────
        proc_cls = get_processor(self._processor_name)
        processor = proc_cls()
        logger.info(
            "Step '%s': processor=%s  input=%r",
            self.name, self._processor_name, artifact,
        )

        # ── 3. Build kwargs from config (exclude reserved keys) ────────────
        reserved = {"processor", "input", "output"}
        proc_kwargs = {k: v for k, v in self.config.items() if k not in reserved}

        # Handle output path resolution
        output_path: Optional[Path] = self._resolve_output(ctx)
        if output_path is not None:
            proc_kwargs["output"] = output_path

        # ── 4. Call process() ──────────────────────────────────────────────
        result = processor.process(artifact, **proc_kwargs)

        logger.info(
            "Step '%s': produced %r",
            self.name, result,
        )

        # ── 5. Wrap in StepOutput ──────────────────────────────────────────
        files = [result.path] if (result.path and result.path.exists()) else []
        return StepOutput(
            data={"artifact": result, "artifact_kind": result.kind},
            files=files,
            metadata={
                "processor": self._processor_name,
                "input_kind": getattr(artifact, "kind", "?"),
                "output_kind": result.kind,
                "output_path": str(result.path) if result.path else None,
            },
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_input(self, ctx: ExecutionContext) -> Any:
        """Return the input artifact for this step.

        Priority:
        1. Artifact from first dependency step's ``StepOutput.data["artifact"]``
        2. ``kg_path`` from a dependency (structured graph, preferred)
        3. ``trace_path`` from a dependency (raw trace fallback)
        4. ``config["input"]`` — wrapped in a minimal Trajectory(path=…)
        """
        from mas.lab.artifacts import Trajectory, Artifact

        # 1. Artifact object from upstream ProcessorStep
        for dep in (self.depends_on or []):
            dep_out = ctx.step_outputs.get(dep)
            if dep_out and "artifact" in dep_out.data:
                return dep_out.data["artifact"]

        # 2+3. Path keys from any upstream step (prefer kg_path)
        for dep in (self.depends_on or []):
            dep_out = ctx.step_outputs.get(dep)
            if dep_out:
                for key in ("kg_path", "normalized_trace_path", "trace_path"):
                    if key in dep_out.data:
                        p = Path(dep_out.data[key])
                        logger.debug(
                            "Step '%s': wrapping %s from '%s' as Trajectory",
                            self.name, key, dep,
                        )
                        return Trajectory(path=p)

        # 4. Inline input
        raw = self.config.get("input")
        if raw is not None:
            from mas.lab.processor import get_processor
            proc_cls = get_processor(self._processor_name)
            if proc_cls.input_kind == "path":
                return raw
            return Trajectory(path=Path(str(raw)), run_id=str(raw))

        raise ValueError(
            f"ProcessorStep '{self.name}': no input found.  "
            "Set config.input or add a depends_on referencing an upstream step "
            "that emits 'artifact', 'kg_path', or 'trace_path'."
        )

    def _resolve_output(self, ctx: ExecutionContext) -> Optional[Path]:
        """Return the resolved output path, or None if not configured."""
        raw = self.config.get("output")
        if not raw:
            return None
        p = resolve_step_artifact_path(ctx, None, self.name, "processor_output", self.config, mkdir=True)
        return p
