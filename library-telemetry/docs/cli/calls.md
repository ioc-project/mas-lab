<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-telemetry-calls(1)

## NAME

`mas-lab telemetry calls` — Calls to named tools with their inputs

## SYNOPSIS

```text
mas-lab telemetry calls [OPTIONS] SPANS_FILE
```

## DESCRIPTION

List the calls to named tools (`--tool NAME`, repeatable) with their inputs.
`--json` emits JSON instead of text.

## OPTIONS

| Flag | Meaning |
|------|------|
| `SPANS_FILE` | Positional argument. |
| `--tool` | Tool name to list (repeatable). |
| `--json` | Emit JSON instead of text. |

## SEE ALSO

- `mas-lab telemetry`
- `mas-lab telemetry inspect`
- `mas-lab telemetry errors`
- `mas-lab telemetry conversation`
- `mas-lab --help`
