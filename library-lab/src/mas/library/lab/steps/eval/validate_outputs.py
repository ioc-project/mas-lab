#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations
"""Output validation step for fail-fast detection of missing/malformed results.

Optional step that validates experiment outputs against a schema (required files,
columns). Useful as a quality gate at the end of a pipeline (typically
``application: post:``, after the final gather/figure steps).
"""

import logging
from pathlib import Path
from typing import Any, Dict

from mas.lab.benchmark.pipeline.artifact_paths import resolve_step_root
from mas.lab.benchmark.pipeline import PipelineStep, StepOutput
from mas.lab.benchmark.reproducibility import OutputSchema

logger = logging.getLogger(__name__)


class ValidateOutputsStep(PipelineStep):
    """Validates experiment outputs against a schema (``required_files``/``required_columns``).

    Config keys
    -----------
    schema : dict
        ``{"required_files": [...], "required_columns": {path: [...]}}``.
        Typically the experiment manifest's own ``output_schema:`` block.
    warn_only : bool
        If ``True`` (default), log and report failures without raising;
        if ``False``, raise ``ValueError`` (fails this step, and — since the
        pipeline reports overall failure on any step failure — the batch).
    """

    type = "validate_outputs"

    async def execute(self, ctx: Any) -> StepOutput:
        config: Dict[str, Any] = self.config
        output_dir = resolve_step_root(ctx, config, step=self.name)
        warn_only = bool(config.get("warn_only", True))

        schema_dict = config.get("schema")
        if not schema_dict:
            logger.info("validate_outputs '%s': no schema configured; skipping", self.name)
            return StepOutput(data={"validation_passed": True, "validation_errors": []})

        schema = OutputSchema(
            required_files=list(schema_dict.get("required_files", [])),
            required_columns=dict(schema_dict.get("required_columns", {})),
        )
        passed = OutputSchema.validate(output_dir, schema, warn_only=warn_only)
        return StepOutput(data={"validation_passed": passed, "validation_errors": []})
