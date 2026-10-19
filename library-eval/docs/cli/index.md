<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-eval(1)

## NAME

`mas-lab eval` — Score traces with MCE LLM-as-judge metrics

## SYNOPSIS

```text
mas-lab eval [OPTIONS]
```

## DESCRIPTION

Score one trace or a directory of traces with MCE metrics (`--list-metrics` to list). Registered via `mas.lab.cli.components`.

## OPTIONS

| Flag | Meaning |
|------|------|
| `SOURCE` | Positional argument. |
| `--metric` | Metric(s) to compute (repeatable). Use --list-metrics to show available metrics. |
| `--model` | LLM model name for judge metrics |
| `--api-base` | API base URL for LLM proxy |
| `--api-key-env` | Environment variable holding the API key |
| `--json` | Output results as JSON |
| `--list-metrics` | List available metrics and exit |
| `--recursive` | Recursively score all traces in SOURCE directory |

## SEE ALSO

- `mas-lab --help`
