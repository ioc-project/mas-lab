<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-eval(1)

## NAME

`mas-lab eval` — Score traces with MCE LLM-as-judge metrics

## SYNOPSIS

```text
mas-lab eval [OPTIONS] [SOURCE]
```

## DESCRIPTION

Library CLI component (entry point `mas.lab.cli.components`). Calls the same
MCE runner as the `eval_mce` pipeline step (`mas.library.eval.mce.runner`).

Metric ids are snake_case (`goal_success_rate`). `--list-metrics` prints the
available ids. When `--model` is omitted, the workspace infra default is used,
else `gpt-4o`.

## OPTIONS

| Flag | Meaning |
|------|------|
| `SOURCE` | Positional argument. |
| `--fixture` | Path to a fixture YAML that provides ground truth. |
| `--metric` | MCE metric(s) to compute (repeatable). Snake_case ids only (goal_success_rate). Defaults to ['goal_success_rate'] when omitted. Use --list-metrics to show the available metrics. |
| `--response-agent` | agent_id whose last execution_end is the final response. Auto-detected when omitted. |
| `--model` | Judge model. Default: workspace infra default, else gpt-4o. |
| `--list-metrics` | List the available metrics and exit. |
| `--json` | Output results as JSON instead of a human-readable table. |
| `--overlay` | Label for display (e.g. inject-C7-backend). |
| `--engine` | Metric computation engine: mce-v1. |
| `--api-key-env` | Name of the environment variable holding the API key (default: OPENAI_API_KEY). |
| `--recursive` | Score every events.jsonl under SOURCE when SOURCE is a directory. |

## SEE ALSO

- `mas-lab --help`
- [library-eval README](../../README.md)
