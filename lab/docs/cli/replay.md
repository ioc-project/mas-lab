<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-replay(1)

## NAME

`mas-lab replay` — Replay native events.jsonl or a ui-trace into the live dashboard

## SYNOPSIS

```text
mas-lab replay [OPTIONS] TARGET
```

## DESCRIPTION

Replay native `events.jsonl` (default) or a recorded ui-trace directory into
the live dashboard ingest API. Does not run the MAS or experiment pipelines.

## OPTIONS

| Flag | Meaning |
|------|------|
| `TARGET` | Positional argument: jsonl file, run directory, or ui-trace dir. |
| `--speed` | Speedup: 1 = recorded gaps, 8 = 8×, 0 = dump. |
| `--replay-time` | Target wall seconds (default 15 when `--speed` is unset). |
| `--info` | Print duration, event count, and speedup; do not ingest. |
| `--json` | JSON for `--info`. |
| `--url` | Ingest URL. |
| `--scenario` | Override the scenario stamp on a single-run replay. |
| `--library` | Library id stamp. |
| `--experiment` | Experiment id stamp. |
| `--overlay` | Overlay id stamp. |
| `--ref` | Extra JSON object merged into each ingest ref. |
| `--only` | Comma-separated scenario names to replay. |
| `--align` | `time` (shared clock) or `gap` (per-file). |
| `--extra` | Sidecar file as `SCENARIO=PATH` (repeatable). |
| `--wait` / `--detach` | Wait until the replay job finishes (default wait). |
| `--recompute` | ui-trace only: re-fold missing plot artefacts. |
| `--trees` | ui-trace only: comma-separated trees (`runtime`, `pipeline`). |
| `--record` | Write a ui-trace directory after the job. |

## SEE ALSO

- `mas-lab demo-ui`
- `mas-lab demo-ui export`
- `mas-lab demo-ui replay`
- `mas-lab serve`
- `mas-lab --help`
