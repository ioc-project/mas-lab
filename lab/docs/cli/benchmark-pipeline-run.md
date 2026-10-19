<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-benchmark-pipeline-run(1)

## NAME

`mas-lab benchmark pipeline run` — Execute a pipeline YAML

## SYNOPSIS

```text
mas-lab benchmark pipeline run [OPTIONS]
```

## DESCRIPTION

Execute a pipeline YAML.

## OPTIONS

| Flag | Meaning |
|------|------|
| `PIPELINE_YAML` | Positional argument. |
| `--only` | Run only these steps (dependencies included automatically). |
| `--force` | Force-rerun these steps (ignore cache). |
| `--var` | Template variable(s) used in step config (e.g. --var events_jsonl=/path/events.jsonl). |
| `--dry-run` | Propagate dry_run=True to every step: execute each step but skip real I/O. |
| `--parallel`, `--no-parallel` | Run independent steps concurrently (cap max_active). Default on. Pass --no-parallel to serialize for debugging. Incremental fork consumers fold records as they arrive. |
| `-o`, `--output-dir` | Override the pipeline's base output directory. |

## SEE ALSO

- `mas-lab benchmark`
- `mas-lab benchmark run`
- `mas-lab benchmark list`
- `mas-lab benchmark show`
- `mas-lab benchmark update`
- `mas-lab benchmark rename`
- `mas-lab --help`
