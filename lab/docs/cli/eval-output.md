<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-eval-output(1)

## NAME

`mas-lab eval-output` — Score MAS output quality (MCE)

## SYNOPSIS

```text
mas-lab eval-output [OPTIONS]
```

## DESCRIPTION

Score MAS output quality (MCE).

## OPTIONS

| Flag | Meaning |
|------|------|
| `SOURCE` | Positional argument. |
| `--fixture` | Path to a fixture YAML that provides ground truth. |
| `--metric` | MCE metric(s) to compute (repeatable). Defaults to ['goal_success_rate'] when omitted. Use --list-metrics to show available metrics per engine. |
| `--response-agent` | agent_id whose last execution_end is the final response. Auto-detected when omitted. |
| `--model` | Override model name (infra manifest default is used when omitted). |
| `--json` | Output results as JSON instead of a human-readable table. |
| `--overlay` | Label for display (e.g. inject-C7-backend). |
| `--engine` | Metric computation engine: mce-v1. |
| `--api-key-env` | Name of the environment variable holding the API key (default: OPENAI_API_KEY). |

## SEE ALSO

- `mas-lab`
- `mas-lab init`
- `mas-lab config`
- `mas-lab check`
- `mas-lab check-config`
- `mas-lab validate`
- `mas-lab --help`
