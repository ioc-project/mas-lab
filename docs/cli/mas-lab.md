<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# `mas-lab`

Index of `mas-lab` commands. **Man pages live with the owning package** — this
page is pointers only. Flags: `mas-lab COMMAND --help`.

Site map of every CLI and UI: [Command line](index.md). `mas-ctl` is separate:
[mas-ctl.md](mas-ctl.md).

| Group | Owner | Index |
|-------|-------|--------|
| `mas-lab` core | lab | [lab/docs/cli/index.md](https://github.com/ioc-project/mas-lab/blob/main/lab/docs/cli/index.md) |
| `mas-lab telemetry` / `otel` | library-telemetry | [library-telemetry/docs/cli/index.md](https://github.com/ioc-project/mas-lab/blob/main/library-telemetry/docs/cli/index.md) |
| `mas-lab kg` | library-kg | [library-kg/docs/cli/index.md](https://github.com/ioc-project/mas-lab/blob/main/library-kg/docs/cli/index.md) |
| `mas-lab eval` | library-eval | [library-eval/docs/cli/index.md](https://github.com/ioc-project/mas-lab/blob/main/library-eval/docs/cli/index.md) |
| `mas-lab plot` | lab | [lab/docs/cli/plot.md](https://github.com/ioc-project/mas-lab/blob/main/lab/docs/cli/plot.md) |
| `mas-lab benchmark` | lab | [lab/docs/cli/benchmark.md](https://github.com/ioc-project/mas-lab/blob/main/lab/docs/cli/benchmark.md) |
| `mas-lab run` / `pipe` | lab | [run](https://github.com/ioc-project/mas-lab/blob/main/lab/docs/cli/run.md) · [pipe](https://github.com/ioc-project/mas-lab/blob/main/lab/docs/cli/pipe.md) |
| `mas-lab control` / `worker` | lab controller | [control](https://github.com/ioc-project/mas-lab/blob/main/lab/docs/cli/control.md) · [worker](https://github.com/ioc-project/mas-lab/blob/main/lab/docs/cli/worker.md) |
| `mas-lab demo-ui` / `replay` | lab controller | [demo-ui](https://github.com/ioc-project/mas-lab/blob/main/lab/docs/cli/demo-ui.md) · [replay](https://github.com/ioc-project/mas-lab/blob/main/lab/docs/cli/replay.md) |

Pipeline shortcuts (which YAML each command wraps):
[lab/docs/cli-pipelines.md](https://github.com/ioc-project/mas-lab/blob/main/lab/docs/cli-pipelines.md).

## telemetry

| Command | Summary |
|---------|---------|
| [`telemetry convert`](https://github.com/ioc-project/mas-lab/blob/main/library-telemetry/docs/cli/convert.md) | events.jsonl → OTel SDK JSONL |
| [`telemetry verify`](https://github.com/ioc-project/mas-lab/blob/main/library-telemetry/docs/cli/verify.md) | SpanSpec verification |
| [`telemetry push`](https://github.com/ioc-project/mas-lab/blob/main/library-telemetry/docs/cli/push.md) | OTLP collector serialize |
| [`telemetry dump`](https://github.com/ioc-project/mas-lab/blob/main/library-telemetry/docs/cli/dump.md) | ClickHouse deserialize |
| [`telemetry compare`](https://github.com/ioc-project/mas-lab/blob/main/library-telemetry/docs/cli/compare.md) | Span-file parity |
| [`telemetry inspect`](https://github.com/ioc-project/mas-lab/blob/main/library-telemetry/docs/cli/inspect.md) / [`errors`](https://github.com/ioc-project/mas-lab/blob/main/library-telemetry/docs/cli/errors.md) / [`conversation`](https://github.com/ioc-project/mas-lab/blob/main/library-telemetry/docs/cli/conversation.md) / [`calls`](https://github.com/ioc-project/mas-lab/blob/main/library-telemetry/docs/cli/calls.md) / [`kinds`](https://github.com/ioc-project/mas-lab/blob/main/library-telemetry/docs/cli/kinds.md) / [`list-apps`](https://github.com/ioc-project/mas-lab/blob/main/library-telemetry/docs/cli/list-apps.md) | Inspection |
| [`telemetry show`](https://github.com/ioc-project/mas-lab/blob/main/lab/docs/cli/telemetry-show.md) | Native events.jsonl summary |

## kg

| Command | Summary |
|---------|---------|
| [`kg normalize`](https://github.com/ioc-project/mas-lab/blob/main/library-kg/docs/cli/normalize.md) | events or OTel → kg.json |
| [`kg validate`](https://github.com/ioc-project/mas-lab/blob/main/library-kg/docs/cli/validate.md) / [`verify`](https://github.com/ioc-project/mas-lab/blob/main/library-kg/docs/cli/verify.md) | Checks |
| [`kg otel-to-kg`](https://github.com/ioc-project/mas-lab/blob/main/library-kg/docs/cli/otel-to-kg.md) | OTel → KG |
| [`kg push`](https://github.com/ioc-project/mas-lab/blob/main/library-kg/docs/cli/push.md) / [`dump`](https://github.com/ioc-project/mas-lab/blob/main/library-kg/docs/cli/dump.md) | Neo4j serialize / deserialize |

## eval

| Command | Summary |
|---------|---------|
| [`eval`](https://github.com/ioc-project/mas-lab/blob/main/library-eval/docs/cli/index.md) | Score a trace (same runner as the `eval_mce` step) |

## benchmark (short list)

Full table: [lab/docs/cli/benchmark.md](https://github.com/ioc-project/mas-lab/blob/main/lab/docs/cli/benchmark.md).

| Command | Summary |
|---------|---------|
| [`benchmark run`](https://github.com/ioc-project/mas-lab/blob/main/lab/docs/cli/benchmark-run.md) | Execute an experiment |
| [`benchmark pipeline run`](https://github.com/ioc-project/mas-lab/blob/main/lab/docs/cli/benchmark-pipeline-run.md) | Standalone pipeline YAML |
| [`benchmark show`](https://github.com/ioc-project/mas-lab/blob/main/lab/docs/cli/benchmark-show.md) / [`list`](https://github.com/ioc-project/mas-lab/blob/main/lab/docs/cli/benchmark-list.md) | Inspect runs |
