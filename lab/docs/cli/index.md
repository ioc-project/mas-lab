<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab(1)

## NAME

`mas-lab` — evaluation, experimentation, and benchmarking tools

## SYNOPSIS

```text
mas-lab [-v] COMMAND [ARGS]...
```

## DESCRIPTION

Core CLI for MAS Lab. Library packages add groups via
`mas.lab.cli.components` (`telemetry`, `kg`, `eval`, `control`, `worker`).

Site index (pointers only): [docs/cli/mas-lab.md](../../docs/cli/mas-lab.md).

## COMMANDS

| Command | Summary | Page |
|---------|---------|------|
| `init` | User config + optional LLM infra | [init](init.md) |
| `config` | Resolved data / cache paths | [config](config.md) |
| `check` / `check-config` | Validate MAS / workspace config | [check](check.md) |
| `validate` | Validate YAML manifests | [validate](validate.md) |
| `serve` | Benchmark UI server | [serve](serve.md) |
| `eval-output` | MCE scoring (core) | [eval-output](eval-output.md) |
| `plot` | Trajectory diagrams | [plot](plot.md) |
| `run` | Processors / pipeline steps | [run](run.md) |
| `pipe` | GStreamer-style processor pipeline | [pipe](pipe.md) |
| `benchmark` | Experiments | [benchmark](benchmark.md) |
| `telemetry` | OTel + native traces | [library-telemetry CLI](../../library-telemetry/docs/cli/index.md) |
| `kg` | Knowledge graphs | [library-kg CLI](../../library-kg/docs/cli/index.md) |
| `eval` | MCE CLI component | [library-eval CLI](../../library-eval/docs/cli/index.md) |
| `control` / `worker` | Controller daemon | [control](control.md) |
| `demo-ui` / `replay` | Live-dashboard export / replay | [demo-ui](demo-ui.md) · [replay](replay.md) |

Pipeline shortcuts: [cli-pipelines.md](../cli-pipelines.md).

## OPTIONS

| Flag | Meaning |
|------|------|
| `-v`, `--verbose` | Verbosity: -v exchange log, -vv debug, -vvv full |

## SEE ALSO

[docs/cli/index.md](../../docs/cli/index.md), [mas-ctl](../../docs/cli/mas-ctl.md)
