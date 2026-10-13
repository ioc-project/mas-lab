#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations
"""ExportOtelStep — per-run pipeline step that converts a single run trace to OTel
spans and optionally pushes them to an OTLP collector.

This step runs **once per benchmark run** (scope: ``run``), immediately after the
run completes.  It is placed in the ``levels.run.pipeline`` section of the
experiment YAML:

.. code-block:: yaml

    levels:
      run:
        pipeline:
          - type: export_otel
            config:
              events_jsonl: "{events_jsonl}"   # resolved by the framework
              destination: json                # json | mock | otlp

The executor resolves template variables in ``config`` before calling
``execute()``, so ``events_jsonl`` receives the absolute path to the run's trace.

Template variables available at the ``run`` level::

    {run_dir}       absolute path to the run's root directory
    {events_jsonl}  absolute path to {run_dir}/traces/events.jsonl
    {run_idx}       run index within the test (1-based)
    {item_id}       dataset item id
    {scenario_id}   scenario name

Config keys::

    events_jsonl  str   path to the events.jsonl file.  Normally set to
                        ``{events_jsonl}`` (template var resolved at runtime).
                        Relative paths are resolved against ctx.output_dir.
    destination   str   json | otlp | mock (default: "json")
    endpoint      str   OTLP HTTP endpoint; required for destination=otlp;
                        defaults to http://localhost:4318 for destination=mock
    service_name  str   OTel service.name resource attribute (default: "mas-runtime")
    dry_run       bool  convert but do not push, for destination=otlp/mock
    overwrite     bool  re-export even if output already exists (default: false)
    batch_size    int   spans per HTTP request for destination=otlp/mock (default: 200)

Destinations
------------
* ``json``  — Writes ``otel_spans.jsonl`` next to ``events.jsonl`` (same
  ``traces/`` dir).  No network access required.

* ``otlp``  — Pushes spans to the configured OTel collector (e.g. the example
  OTel server).  Requires ``endpoint`` in config or
  ``OTEL_EXPORTER_OTLP_ENDPOINT`` env var.

* ``mock``  — Same as ``otlp`` but targets the local Docker collector at
  ``http://localhost:4318``.  Start the collector first::

      docker compose -p mas-benchmark-otel \\
          -f infra/docker-compose.otel.yml up -d --wait
"""

import logging
import os
from pathlib import Path
from typing import Any

from mas.lab.benchmark.pipeline import PipelineStep, StepOutput, ConfigParam
from mas.lab.benchmark.pipeline.executor import ExecutionContext

logger = logging.getLogger(__name__)

_MOCK_ENDPOINT = "http://localhost:4318"
_OTLP_ENDPOINT_ENV = "OTEL_EXPORTER_OTLP_ENDPOINT"


class ExportOtelStep(PipelineStep):
    """Convert a single run's events.jsonl to OTel spans and route to destination.

    Executed once per benchmark run at the ``run`` level.  The ``events_jsonl``
    config key is resolved from the ``{events_jsonl}`` template variable before
    the step is called.
    """

    type = "export_otel"

    PARAMS = [
        ConfigParam("events_jsonl", str,
                    description="Path to events.jsonl. Use '{events_jsonl}' template var (resolved at runtime). "
                                "Relative paths resolved against ctx.output_dir."),
        ConfigParam("destination", str, default="json",
                    description="Output destination: json | otlp | mock."),
        ConfigParam("endpoint", str, default=None,
                    description="OTLP HTTP endpoint. Required for destination=otlp. "
                                "Defaults to http://localhost:4318 for destination=mock. "
                                "Also read from OTEL_EXPORTER_OTLP_ENDPOINT env var."),
        ConfigParam("service_name", str, default="mas-runtime",
                    description="OTel service.name resource attribute."),
        ConfigParam("app_name", str, default=None,
                    description="Override for service.name — takes precedence over service_name when set. "
                                "Useful to tag a re-exported trace, e.g. 'mas-lab-import-test-001'."),
        ConfigParam("dry_run", bool, default=False,
                    description="Convert but do not push (for destination=otlp/mock)."),
        ConfigParam("overwrite", bool, default=False,
                    description="Re-export even if the output file already exists."),
        ConfigParam("batch_size", int, default=200,
                    description="Number of spans per HTTP request (destination=otlp/mock)."),
    ]

    async def execute(self, ctx: ExecutionContext) -> StepOutput:
        from mas.lab.telemetry.otlp_push import convert_to_jsonl, push_file

        events_file = _resolve_events_file(self.config, ctx)
        if events_file is None or not events_file.exists():
            logger.warning("export_otel: events file not found: %s — skipping", events_file)
            return StepOutput(metadata={"status": "skipped", "reason": "events file not found"})

        destination = self.config.get("destination", "json").lower()
        service_name = self.config.get("service_name", "mas-runtime")
        dry_run = bool(self.config.get("dry_run", False))
        overwrite = bool(self.config.get("overwrite", False))
        batch_size = int(self.config.get("batch_size", 200))
        endpoint = _resolve_endpoint(destination, self.config)

        if destination == "json":
            out_file = events_file.parent / "otel_spans.jsonl"
            if out_file.exists() and not overwrite:
                logger.debug("export_otel[json]: already exists, skipping %s", out_file)
                return StepOutput(metadata={"status": "skipped", "output": str(out_file)})
            result = convert_to_jsonl(events_file, out_file, service_name)
            if result["status"] == "ok":
                logger.info("export_otel[json]: %d spans → %s", result["spans"], out_file)
                return StepOutput(
                    files=[out_file],
                    metadata={"status": "ok", "spans": result["spans"], "output": str(out_file)},
                )
            logger.error("export_otel[json] failed: %s", result.get("detail"))
            return StepOutput(metadata={"status": "error", "detail": result.get("detail")})

        # destination = otlp or mock — push to OTLP endpoint
        result = push_file(
            path=events_file,
            endpoint=endpoint,
            service_name=service_name,
            dry_run=dry_run,
            batch_size=batch_size,
        )
        status = result["status"]
        if status in ("ok", "dry-run"):
            logger.info(
                "export_otel[%s]: %d spans → %s (%s)",
                destination, result["spans"], endpoint, status,
            )
        else:
            logger.error("export_otel[%s] failed: %s", destination, result.get("detail"))
        return StepOutput(metadata={
            "status": status,
            "destination": destination,
            "endpoint": endpoint,
            "spans": result["spans"],
            "dry_run": dry_run,
        })


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_events_file(config: dict[str, Any], ctx: ExecutionContext) -> Path | None:
    """Resolve the path to events.jsonl from config or context."""
    raw = config.get("events_jsonl", "")
    if raw:
        p = Path(raw)
        return p if p.is_absolute() else ctx.output_dir / p
    # Fallback: derive from run_dir template var when events_jsonl is absent
    run_dir_str = ctx.template_vars.get("run_dir", "")
    if run_dir_str:
        return Path(run_dir_str) / "traces" / "events.jsonl"
    return None


def _resolve_endpoint(destination: str, config: dict[str, Any]) -> str:
    """Return the OTLP endpoint URL for the given destination."""
    if destination == "json":
        return ""
    if destination == "mock":
        return config.get("endpoint", _MOCK_ENDPOINT)
    # destination == "otlp"
    endpoint = config.get("endpoint", "")
    if not endpoint:
        endpoint = os.environ.get(_OTLP_ENDPOINT_ENV, "")
    if not endpoint:
        raise ValueError(
            "export_otel: destination=otlp requires 'endpoint' in config "
            f"or the {_OTLP_ENDPOINT_ENV} environment variable."
        )
    return endpoint

