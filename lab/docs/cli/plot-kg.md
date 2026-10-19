<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-plot-kg(1)

## NAME

`mas-lab plot kg` — Interactive knowledge-graph HTML

## SYNOPSIS

```text
mas-lab plot kg SOURCE [OPTIONS]
```

## DESCRIPTION

Render an interactive HTML view of a `kg.json` document. SOURCE is a file
path, lab shorthand, or run id.

## OPTIONS

| Flag | Meaning |
|------|------|
| `SOURCE` | Positional argument. |
| `--output`, `-o` | Write HTML to FILE instead of opening the browser. |
| `--title` | Widget title shown in the toolbar. |
| `--layout` | Cytoscape.js layout algorithm. |
| `--panel` | Initial state of the left filter panel. |
| `--max-nodes` | Maximum number of nodes to render. |
| `--no-open` | Write HTML to --output without opening the browser. |

## SEE ALSO

- `mas-lab plot`
- `mas-lab plot message-graph`
- `mas-lab kg`
