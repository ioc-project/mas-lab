#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Expose manifest schemas from package dependencies (no local copies in controller)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from mas.lab.schemas.paths import bench_schema_dir, editor_schema_dir, runtime_schema_dir

SchemaFormat = Literal["yaml", "json"]
SchemaSource = Literal["runtime", "bench", "core", "controller"]


@dataclass(frozen=True)
class SchemaEntry:
    id: str
    title: str
    filename: str
    format: SchemaFormat
    source: SchemaSource
    description: str = ""

    def resolve_path(self) -> Path:
        if self.source == "runtime":
            return runtime_schema_dir() / self.filename
        if self.source == "bench":
            return bench_schema_dir() / self.filename
        if self.source == "core":
            return editor_schema_dir() / self.filename
        if self.source == "controller":
            return _controller_schema_dir() / self.filename
        raise KeyError(self.source)


def _controller_schema_dir() -> Path:
    return Path(__file__).resolve().parent / "schemas"


# Registry mirrored by GET /api/schemas — ids align with mas-lab-ui consumption.
SCHEMA_REGISTRY: tuple[SchemaEntry, ...] = (
    # Runtime (runtime/) — execution truth
    SchemaEntry("agent", "Agent manifest", "agent.schema.yaml", "yaml", "runtime",
                "kind: Agent — full runtime validation schema"),
    SchemaEntry("mas", "MAS manifest", "mas.schema.yaml", "yaml", "runtime",
                "kind: MAS — topology and agent references"),
    SchemaEntry("overlay", "Overlay manifest", "overlay.schema.yaml", "yaml", "runtime",
                "kind: Overlay — benchmark and run-mas overlays"),
    SchemaEntry("infra", "Infra manifest", "infra.schema.yaml", "yaml", "runtime",
                "apiVersion: infra/v1 — LLMProxy, InfraBundle, ToolRegistry, …"),
    SchemaEntry("workflow", "Workflow topology", "workflow.schema.yaml", "yaml", "runtime",
                "workflow/v1 topology (nodes + edges)"),
    SchemaEntry("flavour", "Flavour", "flavour.schema.yaml", "yaml", "runtime",
                "Runtime flavour / model routing"),
    SchemaEntry("tool", "Tool", "tool.schema.yaml", "yaml", "runtime", "kind: Tool"),
    SchemaEntry("tool-bundle", "Tool bundle", "tool_bundle.schema.yaml", "yaml", "runtime",
                "kind: ToolBundle"),
    SchemaEntry("prompt-bundle", "Prompt bundle", "prompt_bundle.schema.yaml", "yaml", "runtime",
                "kind: PromptBundle"),
    # Bench (lab/components/bench/) — lab library manifests
    SchemaEntry("experiment", "Experiment", "experiment.schema.yaml", "yaml", "bench",
                "Benchmark experiment.yaml"),
    SchemaEntry("dataset", "Dataset", "dataset.schema.yaml", "yaml", "bench",
                "Benchmark dataset files"),
    SchemaEntry("lab-config", "Lab config", "lab-config.schema.yaml", "yaml", "bench",
                "Library lab-config.yaml"),
    SchemaEntry("pipeline-post", "Post-processing pipeline", "pipeline.schema.yaml", "yaml",
                "bench", "pipeline: root — bench executor format"),
    SchemaEntry("pipeline", "Pipeline manifest", "pipeline-manifest.schema.json", "json",
                "bench", "kind: Pipeline — UI / library pipelines"),
    # Controller-local registry (step type catalog for PipelineBuilder)
    SchemaEntry("pipeline-step-types-pre", "Pipeline step types (pre)", "pipeline-step-types-pre.json",
                "json", "controller", "Pre-benchmark pipeline steps"),
    SchemaEntry("pipeline-step-types-post", "Pipeline step types (post)",
                "pipeline-step-types-post.json", "json", "controller",
                "Post-benchmark pipeline steps"),
)

_REGISTRY_BY_ID = {entry.id: entry for entry in SCHEMA_REGISTRY}


def list_schemas() -> list[dict]:
    return [
        {
            "id": e.id,
            "title": e.title,
            "format": e.format,
            "source": e.source,
            "filename": e.filename,
            "description": e.description,
        }
        for e in SCHEMA_REGISTRY
    ]


def get_schema_entry(schema_id: str) -> SchemaEntry:
    try:
        return _REGISTRY_BY_ID[schema_id]
    except KeyError as exc:
        raise KeyError(f"Unknown schema id: {schema_id!r}") from exc


def read_schema_text(schema_id: str) -> tuple[SchemaEntry, str]:
    entry = get_schema_entry(schema_id)
    path = entry.resolve_path()
    if not path.is_file():
        raise FileNotFoundError(f"Schema file missing for {schema_id!r}: {path}")
    return entry, path.read_text(encoding="utf-8")
