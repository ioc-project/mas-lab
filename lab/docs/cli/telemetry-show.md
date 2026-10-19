<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-telemetry-show(1)

## NAME

`mas-lab telemetry show` — Summarize a native events.jsonl file

## SYNOPSIS

```text
mas-lab telemetry show [OPTIONS]
```

## DESCRIPTION

Summarize a native events.jsonl file.

## OPTIONS

| Flag | Meaning |
|------|------|
| `TRACE_FILE` | Positional argument. |
| `--filter`, `-f` | Filter events by kind prefix (e.g. 'tool_call', 'memory_', 'llm_call'). |
| `--format` | Output format. |
| `--limit`, `-n` | Show at most N events. |

## SEE ALSO

- `mas-lab`
- `mas-lab init`
- `mas-lab config`
- `mas-lab check`
- `mas-lab check-config`
- `mas-lab validate`
- `mas-lab --help`
