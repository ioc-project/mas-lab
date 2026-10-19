<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-benchmark(1)

## NAME

`mas-lab benchmark` — run, list, show, and manage experiments

## SYNOPSIS

```text
mas-lab benchmark [COMMAND] [ARGS]...
```

## COMMANDS

| Command | Summary | Page |
|---------|---------|------|
| `run` | Execute an experiment YAML | [run](benchmark-run.md) |
| `list` | List runs | [list](benchmark-list.md) |
| `show` | Run details | [show](benchmark-show.md) |
| `follow` | Progress while a run is active | [follow](benchmark-follow.md) |
| `analyze` | Regenerate plots | [analyze](benchmark-analyze.md) |
| `clean` | Remove run data | [clean](benchmark-clean.md) |
| `export` / `import` | Archive | [export](benchmark-export.md) |
| `pipeline` | Standalone pipeline YAML | [pipeline](benchmark-pipeline.md) |
| `step` | List / show / restart steps | [step](benchmark-step.md) |
| `artifact` | Experiment artifacts | [artifact](benchmark-artifact.md) |
| `daemon` | Background UI server | [daemon](benchmark-daemon.md) |

`benchmark run` flags: [cli-reference.md](../cli-reference.md).

## OPTIONS

This command takes no extra flags. See `--help` for inherited group options.

## SEE ALSO

[benchmark.md](../benchmark.md), [pipeline.md](../pipeline.md)
