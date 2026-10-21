<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-telemetry-errors(1)

## NAME

`mas-lab telemetry errors` — Count spans by status; `--fail` on ERROR

## SYNOPSIS

```text
mas-lab telemetry errors [OPTIONS] SPANS_FILE
```

## DESCRIPTION

Read a converted span file and print span count, status counts, and how many
spans have status ERROR. With `--fail`, exit 1 when any ERROR span is present.

## OPTIONS

See `mas-lab telemetry errors --help` for the current flag list (Click help is the source of truth).

## SEE ALSO

- `mas-lab telemetry`
- `mas-lab telemetry inspect`
- `mas-lab telemetry conversation`
- `mas-lab telemetry calls`
- `mas-lab --help`
