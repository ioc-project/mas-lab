<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-telemetry-conversation(1)

## NAME

`mas-lab telemetry conversation` — LLM replies and tool calls in order

## SYNOPSIS

```text
mas-lab telemetry conversation [OPTIONS] SPANS_FILE
```

## DESCRIPTION

Print what each agent said and which tools it called, in span order. `--no-tools`
shows LLM replies only. `--width` clips each line (`0` = no clip).

## OPTIONS

See `mas-lab telemetry conversation --help` for the current flag list (Click help is the source of truth).

## SEE ALSO

- `mas-lab telemetry`
- `mas-lab telemetry inspect`
- `mas-lab telemetry errors`
- `mas-lab telemetry calls`
- `mas-lab --help`
