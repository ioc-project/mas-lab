#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""CLI commands that are shortcuts onto shipped library pipelines.

The ``mas-lab telemetry`` / ``mas-lab kg`` (and related) commands call the same
library functions as the pipeline steps. This catalog is the contract tests
use to prove those pipelines still resolve after the stream-engine migration.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CliPipelineShortcut:
    command: str
    pipeline_ref: str
    source_path: str
    step_types: tuple[str, ...]
    notes: str = ""
    cli_group: str = ""
    cli_name: str = ""


# Every entry's ``pipeline_ref`` must resolve via ``resolve_path_ref``.
CLI_PIPELINE_SHORTCUTS: tuple[CliPipelineShortcut, ...] = (
    CliPipelineShortcut(
        command="mas-lab telemetry convert",
        pipeline_ref="telemetry:native-to-otel-json",
        source_path="library-telemetry/pipelines/native-to-otel-json.yaml",
        step_types=("events_to_otel", "verify_otel"),
        notes="events.jsonl → otel_sdk_spans_replay.jsonl + SpanSpec verify",
        cli_group="telemetry",
        cli_name="convert",
    ),
    CliPipelineShortcut(
        command="mas-lab telemetry verify",
        pipeline_ref="telemetry:native-to-otel-json",
        source_path="library-telemetry/pipelines/native-to-otel-json.yaml",
        step_types=("verify_otel",),
        notes="Same verify_otel step as the convert pipeline",
        cli_group="telemetry",
        cli_name="verify",
    ),
    CliPipelineShortcut(
        command="mas-lab telemetry push",
        pipeline_ref="telemetry:export-otel-collector",
        source_path="library-telemetry/pipelines/export-otel-collector.yaml",
        step_types=("export_otel",),
        notes="Serialize spans (or native events) to an OTLP collector. Alias: mas-lab otel push",
        cli_group="telemetry",
        cli_name="push",
    ),
    CliPipelineShortcut(
        command="mas-lab telemetry dump",
        pipeline_ref="telemetry:dump-session",
        source_path="library-telemetry/pipelines/dump-session.yaml",
        step_types=("clickhouse_dump",),
        notes="Deserialize otel_traces from ClickHouse to JSONL",
        cli_group="telemetry",
        cli_name="dump",
    ),
    CliPipelineShortcut(
        command="mas-lab telemetry compare",
        pipeline_ref="telemetry:otel-span-parity",
        source_path="library-telemetry/pipelines/otel-span-parity.yaml",
        step_types=("compare_otel_spans",),
        notes="Replay vs live plugin span JSONL",
        cli_group="telemetry",
        cli_name="compare",
    ),
    CliPipelineShortcut(
        command="mas-lab kg normalize",
        pipeline_ref="kg:native-to-kg",
        source_path="library-kg/pipelines/native-to-kg.yaml",
        step_types=("verify_events", "normalize_events", "validate_kg"),
        notes="Native events.jsonl → kg.json",
        cli_group="kg",
        cli_name="normalize",
    ),
    CliPipelineShortcut(
        command="mas-lab kg validate",
        pipeline_ref="kg:native-to-kg",
        source_path="library-kg/pipelines/native-to-kg.yaml",
        step_types=("validate_kg",),
        notes="Same validate_kg step as native-to-kg",
        cli_group="kg",
        cli_name="validate",
    ),
    CliPipelineShortcut(
        command="mas-lab kg verify",
        pipeline_ref="kg:native-to-kg",
        source_path="library-kg/pipelines/native-to-kg.yaml",
        step_types=("verify_events",),
        notes="Same verify_events step as native-to-kg",
        cli_group="kg",
        cli_name="verify",
    ),
    CliPipelineShortcut(
        command="mas-lab kg otel-to-events",
        pipeline_ref="kg:otel-to-kg",
        source_path="library-kg/pipelines/otel-to-kg.yaml",
        step_types=("normalize_otel",),
        notes="OTel spans → clickhouse-shaped JSONL (first stage of otel-to-kg)",
        cli_group="kg",
        cli_name="otel-to-events",
    ),
    CliPipelineShortcut(
        command="mas-lab kg otel-to-kg",
        pipeline_ref="kg:otel-to-kg",
        source_path="library-kg/pipelines/otel-to-kg.yaml",
        step_types=("normalize_otel", "normalize_events", "validate_kg"),
        notes="OTel spans → events → kg.json",
        cli_group="kg",
        cli_name="otel-to-kg",
    ),
    CliPipelineShortcut(
        command="mas-lab kg push",
        pipeline_ref="kg:native-to-neo4j",
        source_path="library-kg/pipelines/native-to-neo4j.yaml",
        step_types=("neo4j_push",),
        notes="Serialize KG to Neo4j",
        cli_group="kg",
        cli_name="push",
    ),
    CliPipelineShortcut(
        command="mas-lab kg neo4j-push",
        pipeline_ref="kg:native-to-neo4j",
        source_path="library-kg/pipelines/native-to-neo4j.yaml",
        step_types=("neo4j_push",),
        notes="Alias of mas-lab kg push",
        cli_group="kg",
        cli_name="neo4j-push",
    ),
    CliPipelineShortcut(
        command="mas-lab kg dump",
        pipeline_ref="kg:neo4j-dump",
        source_path="library-kg/pipelines/neo4j-dump.yaml",
        step_types=("neo4j_dump",),
        notes="Deserialize a session/run KG from Neo4j",
        cli_group="kg",
        cli_name="dump",
    ),
    CliPipelineShortcut(
        command="mas-lab plot multilevel-trajectory",
        pipeline_ref="kg:native-plots",
        source_path="library-kg/pipelines/native-plots.yaml",
        step_types=("plot_multilevel_trajectory", "plot_multilevel_trajectory_kg"),
        notes="Trajectory HTML from native events and from kg.json",
        cli_group="plot",
        cli_name="multilevel-trajectory",
    ),
)

# CLI commands in the same groups that are inspection-only (no pipeline YAML).
CLI_INSPECTION_COMMANDS: tuple[str, ...] = (
    "mas-lab telemetry inspect",
    "mas-lab telemetry kinds",
    "mas-lab telemetry list-apps",
    "mas-lab telemetry show",
    "mas-lab telemetry apps",
    "mas-lab telemetry sessions",
    "mas-lab telemetry traces",
    "mas-lab plot trajectory",
    "mas-lab plot communication-flow",
    "mas-lab plot list",
    "mas-lab eval",
)


def shortcut_pipeline_refs() -> tuple[str, ...]:
    seen: list[str] = []
    for item in CLI_PIPELINE_SHORTCUTS:
        if item.pipeline_ref not in seen:
            seen.append(item.pipeline_ref)
    return tuple(seen)


def library_root_for_shortcut(item: CliPipelineShortcut, repo_root: Path) -> Path:
    """On-disk library root claimed by *item* (first path component of ``source_path``)."""
    return (repo_root / Path(item.source_path).parts[0]).resolve()


def resolve_shortcut_pipeline(item: CliPipelineShortcut, repo_root: Path) -> Path:
    """Resolve *item.pipeline_ref* against the catalogued library root.

    Uses the same candidate rules as :func:`mas.runtime.package_refs.resolve_path_ref`
    (optional ``pipelines/`` prefix and ``.yaml`` suffix) so a renamed or deleted
    YAML fails even when the library scheme is not installed in the environment.
    """
    from mas.runtime.package_refs import _resolve_in_library
    from mas.version_spec import split_library_prefix

    scheme, rel = split_library_prefix(item.pipeline_ref)
    if scheme is None or not rel:
        raise LookupError(f"{item.command}: {item.pipeline_ref!r} is not a library ref")
    path = _resolve_in_library(library_root_for_shortcut(item, repo_root), rel)
    if not path.is_file():
        raise FileNotFoundError(
            f"{item.command}: {item.pipeline_ref} did not resolve under "
            f"{library_root_for_shortcut(item, repo_root)}"
        )
    return path
