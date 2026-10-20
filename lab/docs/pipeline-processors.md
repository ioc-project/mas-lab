<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Pipeline processors

A **processor** is a Python class that transforms one artifact into another
(for example JSONL trace → SVG plot). Pipeline steps of `type: processor` wrap
processors via `ProcessorStep`.

**Prerequisite:** [pipeline.md](pipeline.md) explains step DAGs; [pipeline-steps.md](pipeline-steps.md)
lists step types including `processor`.

## Built-in processor directory

Registered when `mas.lab.processors` is imported (automatic during benchmark run):

| Name | Input | Output | Role |
|------|-------|--------|------|
| `trajectory_loader` | JSONL path or run id | `Trajectory` | Parse `events.jsonl` |
| `trajectory_annotator` | `Trajectory` | `AnnotatedTrajectory` | Highlights and notes |
| `trajectory_plotter` | `Trajectory` | `PlotFile` | Mermaid + Playwright HTML/SVG |
| `trajectory_plotter_native` | `Trajectory` | `PlotFile` | Hand-drawn SVG (no Playwright) |
| `multilevel_trajectory_plotter` | JSONL | `PlotFile` | Session / MAS / agent / call lanes |
| `communication-flow-plotter` | JSONL | `PlotFile` | Agent message routing graph |

Source: [`../components/bench/src/mas/lab/processors/`](../components/bench/src/mas/lab/processors/).

## Use in pipeline YAML

```yaml
pipeline:
  - name: plot-trajectory
    type: processor
    config:
      processor: trajectory_plotter_native
      input: "{output_dir}/scenario/item0/r0/traces/events.jsonl"
      output: results/trajectory.svg
      format: svg
```

Run standalone:

```bash
mas-lab run processor trajectory_plotter_native --help
```

## Multilevel trajectory plot

`multilevel_trajectory_plotter` renders Session → MAS → Agent → Call →
Thinking swim lanes from an `events.jsonl` trace, plus a Governance lane
(decisions, HITL exchanges, blocked-action ghost markers, retry chains) that
the HTML viewer's "Gov" button toggles on — hidden by default, present only
when the trace has governance events.

```yaml
pipeline:
  - name: plot-multilevel
    type: processor
    config:
      processor: multilevel_trajectory_plotter
      input: "{output_dir}/scenario/item0/r0/traces/events.jsonl"
      output: results/trajectory_multilevel.html
      format: html
```

In the HTML output: a call with a governance decision attached shows a
colored badge (grey = ALLOW/LOG, amber = HITL/RETRY/SKIP/MODIFY, red =
BLOCK/TERMINATE/BLACKLIST); a blocked action that never produced an engine
call still appears as a ghost marker on the nearest state; retried calls are
linked and numbered (attempt 1, 2, …); click any bar or badge for the full
decision/reason/policy in the side panel.

## Custom processors

1. Subclass `mas.lab.processor.Processor` and decorate with `@register`.
2. Import your module from a lab library so registration runs.
3. Reference `processor: my_processor` in a `type: processor` step.

Example pattern: [labs-going-further.md](labs-going-further.md).

## Related

- [pipeline-steps.md](pipeline-steps.md) — `plot_trajectory`, `plot_communication_flow` step types
- [components/bench/README.md § plot](../components/bench/README.md) — CLI `mas-lab plot`
