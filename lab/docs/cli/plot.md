<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-plot(1)

## NAME

`mas-lab plot` — trajectory and communication diagrams

## SYNOPSIS

```text
mas-lab plot [COMMAND] SOURCE [OPTIONS]
```

`SOURCE` may be a file path (`events.jsonl` / `kg.json`), a lab shorthand, or a run id.

## COMMANDS

| Command | Summary | Page |
|---------|---------|------|
| `trajectory` | Delegation-flow diagram | [trajectory](plot-trajectory.md) |
| `multilevel-trajectory` | Multilevel swimlane | [multilevel-trajectory](plot-multilevel-trajectory.md) |
| `communication-flow` | Agent-to-agent routing graph | [communication-flow](plot-communication-flow.md) |
| `kg` | Interactive knowledge-graph HTML | [kg](plot-kg.md) |
| `message-graph` | Message swimlane from kg.json | [message-graph](plot-message-graph.md) |
| `context-provenance` | Context-provenance diagram | [context-provenance](plot-context-provenance.md) |
| `list` | Discover generated plot artefacts | [list](plot-list.md) |

`multilevel-trajectory` is also a pipeline shortcut onto `kg:native-plots`.

## OPTIONS

This command takes no extra flags. See `--help` for inherited group options.

## SEE ALSO

[cli-pipelines.md](../cli-pipelines.md)
