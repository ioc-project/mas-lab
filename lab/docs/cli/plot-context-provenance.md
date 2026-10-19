<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-plot-context-provenance(1)

## NAME

`mas-lab plot context-provenance` — Context-provenance diagram from kg.json

## SYNOPSIS

```text
mas-lab plot context-provenance SOURCE [OPTIONS]
```

## DESCRIPTION

Render a multilevel context-provenance diagram from a knowledge graph.

## OPTIONS

| Flag | Meaning |
|------|------|
| `SOURCE` | Positional argument. |
| `--format` | Output format. |
| `--theme` | Colour theme. |
| `--hover`, `--no-hover` | Attach hover tooltips to SVG elements. |
| `--title` | Diagram title (default: run_id from kg metadata). |
| `--output`, `-o` | Write output to FILE (default: <source-dir>/context-provenance.<ext>). |

## SEE ALSO

- `mas-lab plot`
- `mas-lab plot message-graph`
- `mas-lab plot kg`
