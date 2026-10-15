#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Built-in artifact types for the pipeline engine."""

from __future__ import annotations

from mas.lab.benchmark.pipeline.artifact import ArtifactType, register_artifact_type

EVENTS = register_artifact_type(
    ArtifactType(
        name="events",
        shape="multi",
        mode="append",
        version=1,
        persist="always",
    )
)

TRACE_STATS = register_artifact_type(
    ArtifactType(
        name="trace_stats",
        shape="value",
        mode="snapshot",
        version=1,
        persist="never",
    )
)

DATA = register_artifact_type(
    ArtifactType(
        name="data",
        shape="records",
        mode="append",
        version=1,
        persist="always",
        columns=[
            {"name": "scenario", "type": "string"},
            {"name": "metric", "type": "string"},
            {"name": "value", "type": "number"},
        ],
        key=["scenario", "test", "run", "metric"],
    )
)

METRICS = register_artifact_type(
    ArtifactType(
        name="metrics",
        shape="value",
        mode="append",
        version=1,
        persist="always",
    )
)

KG = register_artifact_type(
    ArtifactType(
        name="kg",
        shape="multi",
        mode="append",
        version=1,
        records={
            "node": {"key": ["id"]},
            "edge": {"key": ["src", "dst", "rel"]},
        },
        invariants=["edges_reference_nodes"],
        persist="always",
    )
)
