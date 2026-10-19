<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# CLI commands and their pipelines

Several `mas-lab` commands are **shortcuts** onto library pipeline YAML. They
call the same step functions the pipeline would. After the stream-engine
migration they still resolve, and serialize/deserialize stream JSONL (or a
live channel) when they can, falling back to the batch path otherwise.

Attach the same pipeline on a benchmark with `--pipeline`:

```bash
mas-lab benchmark run experiment.yaml --pipeline run:telemetry:native-to-otel-json
```

Refs use the library scheme (`telemetry:…`, `kg:…`). The `pipelines/` prefix
and `.yaml` suffix are optional. See [pipeline.md](pipeline.md).

The Python catalog (`mas.lab.cli.pipeline_shortcuts.CLI_PIPELINE_SHORTCUTS`)
is the source of truth. Tests resolve every ref against the on-disk library
root so a removed YAML fails CI.

Man pages (one page per command, grouped by owner):
[docs/cli/mas-lab.md](../../docs/cli/mas-lab.md).

## Catalog

| CLI command | Pipeline ref | YAML | Step types |
|-------------|--------------|------|------------|
| `mas-lab telemetry convert` | `telemetry:native-to-otel-json` | `library-telemetry/pipelines/native-to-otel-json.yaml` | `events_to_otel`, `verify_otel` |
| `mas-lab telemetry verify` | `telemetry:native-to-otel-json` | same | `verify_otel` |
| `mas-lab telemetry push` | `telemetry:export-otel-collector` | `library-telemetry/pipelines/export-otel-collector.yaml` | `export_otel` |
| `mas-lab telemetry dump` | `telemetry:dump-session` | `library-telemetry/pipelines/dump-session.yaml` | `clickhouse_dump` |
| `mas-lab telemetry compare` | `telemetry:otel-span-parity` | `library-telemetry/pipelines/otel-span-parity.yaml` | `compare_otel_spans` |
| `mas-lab kg normalize` | `kg:native-to-kg` | `library-kg/pipelines/native-to-kg.yaml` | `verify_events`, `normalize_events`, `validate_kg` |
| `mas-lab kg validate` | `kg:native-to-kg` | same | `validate_kg` |
| `mas-lab kg verify` | `kg:native-to-kg` | same | `verify_events` |
| `mas-lab kg otel-to-events` | `kg:otel-to-kg` | `library-kg/pipelines/otel-to-kg.yaml` | `normalize_otel` |
| `mas-lab kg otel-to-kg` | `kg:otel-to-kg` | same | `normalize_otel`, `normalize_events`, `validate_kg` |
| `mas-lab kg push` / `neo4j-push` | `kg:native-to-neo4j` | `library-kg/pipelines/native-to-neo4j.yaml` | `neo4j_push` |
| `mas-lab kg dump` | `kg:neo4j-dump` | `library-kg/pipelines/neo4j-dump.yaml` | `neo4j_dump` |
| `mas-lab plot multilevel-trajectory` | `kg:native-plots` | `library-kg/pipelines/native-plots.yaml` | `plot_multilevel_trajectory`, `plot_multilevel_trajectory_kg` |

`mas-lab otel …` is an alias of `mas-lab telemetry …` (same Click group).

Standalone pipeline run:

```bash
mas-lab benchmark pipeline run library-telemetry/pipelines/export-otel-collector.yaml -o OUT
```

## Inspection-only (no pipeline YAML)

These stay in the same CLI groups but do not wrap a `kind: Pipeline` file:

| Command | What it does |
|---------|----------------|
| `mas-lab telemetry inspect` | Span-file summary (no validation) |
| `mas-lab telemetry kinds` | Native event kinds with a registered span handler |
| `mas-lab telemetry list-apps` | Apps in ClickHouse `otel_traces` |
| `mas-lab telemetry show` / `apps` / `sessions` / `traces` | Native trace inspection (core stub; kept when the library group replaces `push`/`dump`) |
| `mas-lab plot trajectory` | Delegation-flow diagram from `events.jsonl` |
| `mas-lab plot communication-flow` | Agent-to-agent routing graph |
| `mas-lab plot list` | Discover generated plot artefacts |

## Serialize and deserialize

`serialize` / `deserialize` (and the push/dump shortcuts above) accept:

1. **Realtime** — a live telemetry-channel stream id, or a JSONL path read
   line-by-line in batches (the large-file path; never `read_text` of the
   whole file).
2. **Fallback** — the previous whole-artifact `codec.encode` / `codec.decode`
   when the input is not a stream or JSONL (for example a KG document).

`mas-lab telemetry push` on OTel SDK JSONL uses the streaming path. Native
`events.jsonl` still converts through the stateful replay helper, then pushes.

## Engine

Post-phase and standalone `mas-lab benchmark pipeline run` / `mas-lab run
pipeline step` execute the DAG through the stream-engine scheduler. Cache,
fingerprints, `--only` / `--force` / `--dry-run` stay on the executor; the
scheduler owns ordering, resource caps, and skip-on-failed-dep.
