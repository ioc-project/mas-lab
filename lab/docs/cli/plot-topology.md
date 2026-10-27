<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-plot-topology(1)

## NAME

`mas-lab plot topology` — Agents, skills, tools and delegation of a MAS

## SYNOPSIS

```text
mas-lab plot topology [OPTIONS] MAS_YAML
```

## DESCRIPTION

Draws the agents of a MAS from its specs (`mas.yaml` and the agent files it references), left to
right from the entry agent. Each agent shows its skills (pills) and tools (square chips);
delegation is a solid arrow. Agents that talk as equals, declared with `peers:` on a workflow
node, are joined by a dashed arrow with a head at both ends. Any delegation graph works: trees,
diamonds, pipelines and cycles. No trace is needed.

Python: `from mas.lab.plots.topology import plot_topology`.

## OPTIONS

| Flag | Meaning |
|------|------|
| `MAS_YAML` | Positional argument: the `mas.yaml` to draw. |
| `--format` | `svg` (default) or `html` (adds hover highlighting). |
| `--title` | Diagram title (default: the MAS name; empty for none). |
| `--entry-label` | Word shown next to the entry marker, for example `incident`. |
| `--output`, `-o` | Write output to FILE (default: <mas.yaml dir>/topology.<ext>). |

## SEE ALSO

- `mas-lab plot`
- `mas-lab plot communication-flow`
- `mas-lab plot trajectory`
- `mas-lab --help`
