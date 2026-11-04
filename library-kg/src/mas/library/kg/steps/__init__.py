#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Standalone pipeline step functions for the knowledge-graph library.

These are pure Python functions — no dependency on ``mas.lab.benchmark.pipeline``
or any other internal package.  The ``[bench]`` extra wraps them in ``PipelineStep``
adapters for use in bench pipelines.

Steps accept :class:`~mas.library.kg.artifact.KGArtifact` objects as input and
return :class:`~mas.library.kg.artifact.KGArtifact` (or a report dict for
comparison / validation steps) as output.  File paths (``str`` / ``Path``) are
also accepted everywhere a ``KGArtifact`` is expected and are loaded on demand.

Core steps (no extra deps)
--------------------------
run_normalize         events.jsonl → KGArtifact
run_normalize_otel    OTel spans → events.jsonl → KGArtifact
run_validate_kg       KG structural + SHACL validation → dict
run_verify_events     events.jsonl schema + spec validation → dict
run_annotate          generic KG node annotation → KGArtifact
run_compare_kg        compare two KGs for structural parity → dict

Neo4j steps (require this library's ``neo4j`` extra)
---------------------------------------------------------------------------
run_neo4j_push        KGArtifact → Neo4j (MERGE semantics, idempotent) → dict
run_neo4j_dump        Neo4j → KGArtifact (fetch by session_id or run_id)
"""

from mas.library.kg.steps.annotate import run_annotate
from mas.library.kg.steps.compare_kg import run_compare_kg
from mas.library.kg.steps.neo4j_dump import run_neo4j_dump
from mas.library.kg.steps.neo4j_push import run_neo4j_push
from mas.library.kg.steps.normalize import run_normalize
from mas.library.kg.steps.normalize_otel import run_normalize_otel
from mas.library.kg.steps.validate_kg import run_validate_kg
from mas.library.kg.steps.verify_events import run_verify_events

__all__ = [
    "run_normalize",
    "run_normalize_otel",
    "run_validate_kg",
    "run_verify_events",
    "run_annotate",
    "run_compare_kg",
    "run_neo4j_push",
    "run_neo4j_dump",
]
