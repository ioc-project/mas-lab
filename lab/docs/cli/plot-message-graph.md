<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-plot-message-graph(1)

## NAME

`mas-lab plot message-graph` — Message swimlane from kg.json

## SYNOPSIS

```text
mas-lab plot message-graph SOURCE [OPTIONS]
```

## DESCRIPTION

Render a message swimlane (SVG or HTML) from a knowledge graph.

## OPTIONS

| Flag | Meaning |
|------|------|
| `SOURCE` | Positional argument. |
| `--format` | Output format. |
| `--theme` | Colour theme: auto (follow OS), light, or dark. |
| `--hover`, `--no-hover` | Attach hover tooltips (HTML mode) / native SVG title elements. |
| `--time-axis`, `--no-time-axis` | Draw a horizontal time axis below the diagram lanes. |
| `--respect-timing`, `--no-respect-timing` | Scale box widths proportionally to LLM call duration. |
| `--title` | Diagram title (default: run_id from kg metadata). |
| `--output`, `-o` | Write output to FILE (default: <source-dir>/message-graph.<ext>). |

## SEE ALSO

- `mas-lab plot`
- `mas-lab plot kg`
- `mas-lab plot context-provenance`
