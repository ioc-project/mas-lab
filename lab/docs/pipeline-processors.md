<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Pipeline processors

This page lists the built-in processors and shows how to run them. Read it to plot a trace or write your own processor.

A **processor** is a Python class that transforms one artifact into another, for example a JSONL trace into an SVG plot. A pipeline step of `type: processor` wraps a processor through `ProcessorStep`.

Smallest working command:

```bash
mas-lab run processor trajectory_plotter_native trace=path/to/events.jsonl plot=traj.svg plot.format=svg
```

Prerequisites: [pipeline.md](pipeline.md) explains step DAGs. [pipeline-steps.md](pipeline-steps.md) lists step types, including `processor`.

## Built-in processor directory

The registry fills when `mas.lab.processors` is imported. That happens automatically during a benchmark run and for `mas-lab run processor`.

| Name | Input | Output | Role |
|------|-------|--------|------|
| `trajectory_loader` | JSONL path or run id | `Trajectory` | Parse `events.jsonl` |
| `trajectory_annotator` | `Trajectory` | `AnnotatedTrajectory` | Highlights and notes |
| `trajectory_plotter` | `Trajectory` | `PlotFile` | Mermaid + Playwright HTML/SVG |
| `trajectory_plotter_native` | `Trajectory` | `PlotFile` | Hand-drawn SVG (no Playwright) |
| `multilevel-trajectory-plotter` | `Trajectory` | `PlotFile` | Session / MAS / agent / call lanes (HTML or SVG) |
| `communication-flow-plotter` | `Trajectory` | `PlotFile` | Agent message routing graph |
| `message-graph-plotter` | `Trajectory` | `PlotFile` | Whole-run agent message-flow swimlane (SVG/HTML) |

Names are exact. Some use underscores and some use hyphens. List the registered names with `mas-lab run processor --list`.

Sources: [`../components/bench/src/mas/lab/processors/`](../components/bench/src/mas/lab/processors/)
and [`../components/bench/src/mas/lab/plots/`](../components/bench/src/mas/lab/plots/).

## Use in pipeline YAML

```yaml
pipeline:
  - name: plot-trajectory
    type: processor
    config:
      processor: trajectory_plotter_native
      input: "{output_dir}/baseline/item1/r1/traces/events.jsonl"   # <scenario>/item<id>/r<n>
      output: results/trajectory.svg
      format: svg
```

Run standalone:

```bash
mas-lab run processor --list
mas-lab run processor trajectory_plotter_native \
  trace=path/to/events.jsonl plot=traj.svg plot.format=svg
```

Arguments are `NAME=VALUE` pairs:

- `trace=` binds the input slot (a run id or an `events.jsonl` path).
- `plot=` sets the output path.
- `plot.format=` overrides an attribute of the output.

`--list` shows each processor's input and output kinds.

## Multilevel trajectory plot

`multilevel-trajectory-plotter` renders Session, MAS, Agent, Call, and Thinking swim lanes from an `events.jsonl` trace.

It adds a Governance lane when the trace has governance events. The lane shows decisions, human-in-the-loop (HITL) exchanges, blocked-action ghost markers, and retry chains. The HTML viewer hides it by default. The "Gov" button toggles it.

```yaml
pipeline:
  - name: plot-multilevel
    type: processor
    config:
      processor: multilevel-trajectory-plotter
      input: "{output_dir}/baseline/item1/r1/traces/events.jsonl"
      output: results/trajectory_multilevel.html
      format: html
```

The HTML output works as follows:

- A call with a governance decision shows a colored badge: grey for ALLOW/LOG, amber for HITL/RETRY/SKIP/MODIFY, red for BLOCK/TERMINATE/BLACKLIST.
- A blocked action that never produced an engine call appears as a ghost marker on the nearest state.
- Retried calls are linked and numbered (attempt 1, 2, ...).
- Click any bar or badge to see the decision, reason, and policy in the side panel.

## Custom processors

1. Subclass `mas.lab.processor.Processor` and decorate with `@register`.
2. Import your module from a lab library so registration runs.
3. Reference `processor: my_processor` in a `type: processor` step.

Example pattern: [labs-going-further.md](labs-going-further.md).

## Related

- [pipeline-steps.md](pipeline-steps.md): `plot_trajectory`, `plot_communication_flow` step types
- [components/bench/README.md § plot](../components/bench/README.md): CLI `mas-lab plot`
