<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab CLI index

Hierarchical index of `mas-lab` commands. **Man pages live with the owning
package** — this page is pointers only. Flags: `mas-lab COMMAND --help`.

`mas-ctl` is documented separately: [mas-ctl.md](mas-ctl.md).

| Group | Owner | Index |
|-------|-------|--------|
| `mas-lab` core | lab | [lab/docs/cli/index.md](../../lab/docs/cli/index.md) |
| `mas-lab telemetry` / `otel` | library-telemetry | [library-telemetry/docs/cli/index.md](../../library-telemetry/docs/cli/index.md) |
| `mas-lab kg` | library-kg | [library-kg/docs/cli/index.md](../../library-kg/docs/cli/index.md) |
| `mas-lab eval` | library-eval | [library-eval/docs/cli/index.md](../../library-eval/docs/cli/index.md) |
| `mas-lab plot` | lab | [lab/docs/cli/plot.md](../../lab/docs/cli/plot.md) |
| `mas-lab benchmark` | lab | [lab/docs/cli/benchmark.md](../../lab/docs/cli/benchmark.md) |
| `mas-lab run` / `pipe` | lab | [run](../../lab/docs/cli/run.md) · [pipe](../../lab/docs/cli/pipe.md) |
| `mas-lab control` / `worker` | lab controller | [control](../../lab/docs/cli/control.md) · [worker](../../lab/docs/cli/worker.md) |
| `mas-lab demo-ui` / `replay` | lab controller | [demo-ui](../../lab/docs/cli/demo-ui.md) · [replay](../../lab/docs/cli/replay.md) |

Pipeline shortcuts (which YAML each command wraps):
[lab/docs/cli-pipelines.md](../../lab/docs/cli-pipelines.md).

## telemetry

| Command | Summary |
|---------|---------|
| [`telemetry convert`](../../library-telemetry/docs/cli/convert.md) | events.jsonl → OTel SDK JSONL |
| [`telemetry verify`](../../library-telemetry/docs/cli/verify.md) | SpanSpec verification |
| [`telemetry push`](../../library-telemetry/docs/cli/push.md) | OTLP collector serialize |
| [`telemetry dump`](../../library-telemetry/docs/cli/dump.md) | ClickHouse deserialize |
| [`telemetry compare`](../../library-telemetry/docs/cli/compare.md) | Span-file parity |
| [`telemetry inspect`](../../library-telemetry/docs/cli/inspect.md) / [`kinds`](../../library-telemetry/docs/cli/kinds.md) / [`list-apps`](../../library-telemetry/docs/cli/list-apps.md) | Inspection |
| [`telemetry show`](../../lab/docs/cli/telemetry-show.md) | Native events.jsonl summary |

## kg

| Command | Summary |
|---------|---------|
| [`kg normalize`](../../library-kg/docs/cli/normalize.md) | events or OTel → kg.json |
| [`kg validate`](../../library-kg/docs/cli/validate.md) / [`verify`](../../library-kg/docs/cli/verify.md) | Checks |
| [`kg otel-to-kg`](../../library-kg/docs/cli/otel-to-kg.md) | OTel → KG |
| [`kg push`](../../library-kg/docs/cli/push.md) / [`dump`](../../library-kg/docs/cli/dump.md) | Neo4j serialize / deserialize |

## eval

| Command | Summary |
|---------|---------|
| [`eval`](../../library-eval/docs/cli/index.md) | Score a trace (same runner as the `eval_mce` step) |

## benchmark (short list)

Full table: [lab/docs/cli/benchmark.md](../../lab/docs/cli/benchmark.md).

| Command | Summary |
|---------|---------|
| [`benchmark run`](../../lab/docs/cli/benchmark-run.md) | Execute an experiment |
| [`benchmark pipeline run`](../../lab/docs/cli/benchmark-pipeline-run.md) | Standalone pipeline YAML |
| [`benchmark show`](../../lab/docs/cli/benchmark-show.md) / [`list`](../../lab/docs/cli/benchmark-list.md) | Inspect runs |
