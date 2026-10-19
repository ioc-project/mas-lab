<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-demo-ui-export(1)

## NAME

`mas-lab demo-ui export` — Write a static site from events.jsonl or a trace directory

## SYNOPSIS

```text
mas-lab demo-ui export [OPTIONS] [TRACE]
```

## DESCRIPTION

Write a static site from native `events.jsonl` or a ui-trace directory.
Repeat `--scenario NAME=TRACE` for a multi-panel compare layout.

## OPTIONS

| Flag | Meaning |
|------|------|
| `TRACE` | Positional argument (required unless `--scenario` is given). |
| `-o`, `--output` | Destination directory. |
| `--layout` | Layout YAML name (default `run-view.yaml`). |
| `--layouts` | Directory to resolve `--layout` from. |
| `--scenario` | Merge-bundle mode: `NAME=TRACE` (repeatable). |
| `--no-build` | Reuse `demo-ui/dist` if present. |
| `--serve` | Serve the export and open a browser. |
| `--port` | HTTP port when `--serve` is set. |

## SEE ALSO

- `mas-lab demo-ui`
- `mas-lab demo-ui replay`
- `mas-lab replay`
- `mas-lab --help`
