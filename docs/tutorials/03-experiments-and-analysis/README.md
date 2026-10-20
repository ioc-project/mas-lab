<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Tutorial 3 — Experiments, Analysis & Evaluation

> **Packages:** `mas-lab`, `mas-runtime`
> **Time:** ~60 min hands-on
> **Prerequisite:** [Tutorial 0](../00-environment-setup/) (data paths),
> [Tutorial 1](../01-building-an-agent/), and [Tutorial 2](../02-creating-a-mas/).
> Run `mas-lab config` to see where this machine stores labs output and trace cache.

---

## Overview

In Tutorials 1 and 2 you built an agent and composed a MAS.
Now you'll learn how to **run experiments and analyze the output**: define
benchmarks with `experiment.yaml`, inspect raw **`events.jsonl`** traces,
evaluate answer quality with MCE, and generate plots — first
manually, then via reusable pipelines, and finally in a full experiment
comparing three MAS topologies on the trip planner.

A **lab** is this experiment pack. A **library** is reusable YAML and code
(`samples:tools/calc.tool.yaml`). When to create each:
[labs-and-libraries.md](../../labs-and-libraries.md).

1. **Part A** — Manual trace analysis on a QA agent (CLI commands one by one)
2. **Part B** — Turning those manual steps into a declarative pipeline
3. **Part C** — Trip planner experiment: single-agent vs. linear vs. moderator, evaluated with MCEv1 `AnswerRelevancyMetric` and compared in a grouped ggplot2 plot

---

## Part A — Manual Trace Analysis

Part A uses the same Q&A agent from Tutorial 1, running three design
pattern variants (ReAct, CoT, Reflection) so you can inspect the
differences in raw traces. **You must run this tutorial's benchmark first**
— the traces don't come from Tutorial 1's output.

```bash
# From repository root (or cd into the tutorial directory)
mas-lab benchmark run docs/tutorials/03-experiments-and-analysis/experiment.yaml --progress
```

Offline analysis (no LLM) uses the bundled fixture trace — see `demo/scenario.yaml` and:

```bash
pytest tests/tutorials/test_scenario_commands.py -v -k tuto-03
```

### Where traces are stored

The runner **auto-derives** the output directory from the experiment's
`name:` field.  No `output_dir:` is needed in `experiment.yaml`.

**Check your machine first** (paths depend on `--8<-- "includes/mas-paths.md:xdg-user-config"`):

```bash
mas-lab config
```

| Config | Labs root | Trace cache |
|--------|-----------|---------------|
| `--8<-- "includes/mas-paths.md:xdg-user-config"` with `labs_dir` / `cache_dir` | `--8<-- "includes/mas-paths.md:xdg-labs-dir"` | `--8<-- "includes/mas-paths.md:xdg-trace-cache"` |

Create `--8<-- "includes/mas-paths.md:xdg-user-config"` if it does not exist — see [Tutorial 0](../00-environment-setup/README.md) or run `mas-lab config` to inspect paths.

Tutorial 3's experiment (`name: "t3-observability-patterns"`) writes to:

```
<labs_root>/t3-observability-patterns/
```

For example, with default user config: `--8<-- "includes/mas-paths.md:xdg-labs-dir"`/t3-observability-patterns/.

> **Override:** `labs_dir` / `cache_dir` in `--8<-- "includes/mas-paths.md:xdg-user-config"`
> or workspace `config.yaml`.  Inside a `.lab`
> workspace the path becomes `<labs_root>/<lab_name>/<experiment_name>/`.

Resolve paths once per shell session (values depend on your `--8<-- "includes/mas-paths.md:xdg-user-config"`):

```bash
export LABS_ROOT="$(python -c 'from mas.lab.paths import labs_root; print(labs_root())')"
export TRACE="$LABS_ROOT/t3-observability-patterns/react/item1/r1/traces/events.jsonl"
```

The runner creates a directory tree mirroring the experiment hierarchy
(`scenario / item / run`):

```
$LABS_ROOT/t3-observability-patterns/                  ← auto-derived from name
├── metadata.yaml                                      ← experiment metadata
├── react/                                             ← scenario
│   ├── item1/                                         ← dataset item
│   │   └── r1/                                        ← run number
│   │       ├── traces/                                ← trace artifacts (*)
│   │       │   └── events.jsonl                       ← raw event stream
│   │       ├── run_info.json                          ← timing, status, config
│   │       └── .run_ref                               ← cache hash pointer
│   └── item2/
│       └── r1/
│           └── ...
├── cot/
│   └── ...
└── reflection/
    └── ...
```

**(\*) `traces/` is a symlink.** The actual `events.jsonl` lives in a
content-addressed **trace cache** (see `mas-lab config` for the path, typically
`--8<-- "includes/mas-paths.md:xdg-trace-cache"`/<hash>/traces/).
The runner hashes deterministic inputs (resolved config, prompt, model, run
index) and stores the trace once. The symlink in the output dir points to the
cache entry:

```
traces/ → <trace_cache>/<hash>/traces/
```

This deduplication means: if you re-run the same experiment with the same
inputs, the cache is hit and no new trace is produced. To force a fresh run,
delete the `.run_ref` file (breaks the link) and re-execute, or set
`bench_emulation.runtime.cache: disabled` in the experiment manifest.

> **Not to be confused with the LLM response cache.** This trace cache
> deduplicates whole *experiment runs*. Each individual LLM call within a run
> has its own separate **LLM response cache** (policy from workspace
> `runtime_refs` / `standard:runtime-default`, or `--cache-read` /
> `--cache-write` on `mas-ctl chat`). Offline CI replay uses
> [llm_cache](../../manifests/llm-cache.md) (`raise_on_miss`) recorded against
> a live provider. Tutorial 0 `config.yaml` inherits user `default_infra`.
> See [runtime-engine.md](../../manifests/runtime-engine.md) and
> [llm-cache.md](../../manifests/llm-cache.md).

### A.1 — The raw event stream

Every agent run produces an `events.jsonl` — one JSON object per line, one
event per runtime boundary crossing (LLM call, tool call, session lifecycle):

```bash
head -5 "$TRACE"
# offline: head -5 docs/tutorials/03-experiments-and-analysis/fixtures/events.jsonl
```

```json
{"event": "session_start", "agent": "qa-agent", "timestamp": "2026-04-19T10:23:01Z", "trace_id": "a1b2c3"}
{"event": "llm_call_start", "agent": "qa-agent", "model": "gpt-4o-mini", "dp_state": {"phase": "think", "iteration": 1}}
{"event": "llm_call_end", "agent": "qa-agent", "tokens": {"prompt": 312, "completion": 87}, "duration_ms": 1240}
{"event": "tool_call_start", "agent": "qa-agent", "tool": "calculator", "args": {"expression": "17 * 23"}}
{"event": "tool_call_end", "agent": "qa-agent", "tool": "calculator", "result": {"value": 391}, "duration_ms": 2}
```

Key fields:

| Field | Description |
|-------|-------------|
| `event` | Event type — `session_start`, `llm_call_start/end`, `tool_call_start/end`, `session_end` |
| `agent` | Which agent emitted this event |
| `dp_state` | Design pattern state — phase (`think`/`act`/`observe`), iteration number |
| `trace_id` | Correlation ID linking all events in the same session |

### A.2 — Telemetry summary

```bash
mas-lab telemetry show "$TRACE"
# offline: mas-lab telemetry show docs/tutorials/03-experiments-and-analysis/fixtures/events.jsonl
```

Output: event count, agents involved, time range, token usage, event
breakdown by type. This is the quickest way to sanity-check a run.

Inspect tool calls directly:

```bash
grep '"tool_call_start"' "$TRACE" \
  | python3 -c "
import sys, json
for line in sys.stdin:
    e = json.loads(line)
    args = e.get('arguments') or e.get('args') or {}
    print(f\"  {e.get('tool_name') or e.get('tool')}({args})\")
"
```

On the bundled ReAct trace you should see a multiply step (`17 * 23`) followed
by a modulo check (`391 % 17`) before the final answer.

### A.3 — Evaluation (MCEv1)

Evaluation measures **answer quality**. The `answer_relevancy` metric uses an
LLM-as-judge to score how well the agent's response addresses the input:

```bash
mas-lab eval "$TRACE" \
  --metric answer_relevancy \
  --model gpt-4o \
  --api-key-env OPENAI_API_KEY
```

Output: a JSON object with `score` (0–1), `reason` (explanation from the judge),
and metadata:

```json
{
  "metric": "answer_relevancy",
  "score": 0.92,
  "reason": "The response correctly computes 17*23=391 and accurately verifies primality.",
  "model": "gpt-4o",
  "session_id": "a1b2c3"
}
```

### A.4 — Trajectory plots

#### Compact delegation diagram (SVG)

```bash
mas-lab plot trajectory "$TRACE" \
  --format svg -o output/trajectory-item1.svg
```

For a single agent this is a linear sequence; for a MAS (Tutorial 2) it
shows delegation arrows between agents.

#### Interactive swimlane (HTML)

```bash
mas-lab plot multilevel-trajectory "$TRACE" \
  --format html -o output/swimlane-item1.html
```

The HTML view has:

- Horizontal lanes per agent
- Tool calls as blocks within lanes
- `dp_state` annotations on each block (ReAct iteration, think/act/observe)
- Clickable events for full detail

---

## The experiment.yaml hierarchy

Before diving into pipelines and full experiments, let's understand the
**four-level hierarchy** that structures every benchmark:

```
EXPERIMENT ─── the top-level unit of work (experiment-level post: --depth exp)
├── APPLICATION ── MAS binding (application.app / manifest + configs_dir)
├── SCENARIO ───── overlay column (scenarios: + scenario.post)
│   └── TEST / ITEM ── one dataset item (dataset: + item.post)
│       └── RUN ────── one execution (run.n_runs + run.post)
└── EXECUTION ──── batch orchestration only (last in the file)
```

| Level | Defined by | Multiplicity |
|-------|-----------|--------------|
| **Experiment** | `experiment.yaml` + root `post:` | 1 |
| **Application** | `application:` (`app` or `manifest`, `configs_dir`) | 1 |
| **Scenario** | `scenarios:` list — each entry is an overlay stack | M scenarios |
| **Test / item** | one item in `dataset:` (`item:` hooks, CLI `--item`) | D items per dataset |
| **Run** | `run.n_runs` | N repetitions per item |

**Total executions = M × D × N**

Pin LLMs on the experiment, not in overlays. `experiment.models` is a
**slot map**: `main` (turns), `summarizer` (history summary), `judge`
(MCE). Scalar `experiment.model` is shorthand for `models.main`. Agents
that say `model: any` inherit these slots. See
[experiment.md](../../manifests/experiment.md#model-slots-models--model).

The output directory mirrors this structure:

```
$LABS_ROOT/<experiment_name>/
└── <scenario_id>/
    └── <item_id>/
        └── r<N>/
            └── traces/events.jsonl
```

### What lives at each level

**Scenarios** define the **independent variable** — the configuration change
you're testing. Each scenario explicitly declares its **overlays** — configuration
patches applied on top of the base MAS manifest:

```yaml
scenarios:
  - id: baseline
    overlays: {logic: [], control: [], infra: []}
    tags: [reference]
  - id: cot
    overlays: {logic: [cot], control: [], infra: []}
    tags: [cot]
```

**Tests** come from the dataset, which is **app-specific** — a Q&A agent
gets prompts, a trip planner gets trip requests (destination, dates,
budget):

```yaml
dataset:
  path: "dataset.yaml"          # items: [{id: "item1", prompt: "..."}, ...]
```

**Runs** repeat each test to measure variance:

```yaml
run:
  n_runs: 3                     # 3 repetitions per (scenario × item) — design
schedule:
  ordering: coverage            # breadth-first: one round across all conditions
```

### Named resources and pipelines

Pipeline steps run after the benchmark and operate at a specific
**scope** in the hierarchy. For the OSS release profile, Tutorial 3 keeps
inline pipelines empty and uses a standalone, re-runnable analysis pipeline.

```yaml
post:
  - name: extract
    type: extract_trajectories
    config:
      runs_dir: runs
      csv_file: mas_benchmark_*.csv
```

| Scope | YAML / CLI | Template variables available |
|-------|------|-----------------------------|
| `run` | `run.post` / `--depth run` | `{scenario_id}`, `{item_id}`, `{run_dir}`, `{events_jsonl}` |
| `item` | `item.post` / `--depth item` | `{scenario_id}`, `{item_id}` |
| `scenario` | `scenario.post` / `--depth scenario` | `{scenario_id}` |
| `exp` | root `post:` / `--depth exp` | (none; artifact paths are relative to the level folder) |

### Tutorial artifacts vs. design-space lab

This tutorial is **self-contained** under `docs/tutorials/03-experiments-and-analysis/`.
For larger sweeps (100-item QA dataset, trip-planner topologies), see
`labs/design-space.lab/` in the repository root.

| Tutorial file | Experiment `name:` | Output directory |
|---------------|-------------------|------------------|
| `experiment.yaml` | `t3-observability-patterns` | `$LABS_ROOT/t3-observability-patterns/` |
| `experiment-topology.yaml` | `t3-topology-comparison` | `$LABS_ROOT/t3-topology-comparison/` |

The experiment `name:` field drives the auto-derived output directory under
`labs_dir` from `--8<-- "includes/mas-paths.md:xdg-user-config"`.

---

## Part B — From Manual Commands to Pipelines

Part A used manual CLI commands. A **pipeline** declares the same steps in
YAML, with explicit dependencies, so they run automatically after every
benchmark.

### B.1 — Pipeline structure

```yaml
# pipelines/analysis.yaml
api_version: "pipeline/v1"
kind: Pipeline

metadata:
  name: t3-observability-patterns
  description: "Release-safe analysis: extract trajectories"

spec:
  steps:
    - name: extract
      type: extract_trajectories
      config:
        runs_dir: runs
        csv_file: mas_benchmark_*.csv
```

### B.2 — One step per manual command

| Manual command (Part A) | Pipeline step type | Produces |
|------------------------|-------------------|----------|
| benchmark post-processing | `extract_trajectories` | `trajectories.jsonl` + per-run `trajectory.json` |

### B.3 — Dependencies

```
extract
```

The standalone pipeline is intentionally minimal for release validation and
clean-environment reproducibility.

### B.4 — Running the pipeline

Point the pipeline at a completed benchmark's output with `-o` so its steps can
read that run's `results.csv` and traces (the benchmark prints the lab dir as
`Results: …/results.csv` when it finishes):

```bash
T03=docs/tutorials/03-experiments-and-analysis
LAB=~/.local/share/mas/labs/t3-observability-patterns   # from the benchmark output

# Full pipeline (extract → trace-stats → plot-svg → plot-html)
mas-lab benchmark pipeline run "$T03/pipelines/analysis.yaml" -o "$LAB"

# Single step (re-run one step only — steps: extract, trace-stats, plot-svg, plot-html)
mas-lab benchmark pipeline run "$T03/pipelines/analysis.yaml" -o "$LAB" --only extract

# Dry run (no -o needed — nothing is read or written)
mas-lab benchmark pipeline run "$T03/pipelines/analysis.yaml" --dry-run
```

The plots land under `$LAB/trajectories/` (`trajectory.svg`, `swimlane.html`).

### B.5 — Inline vs. standalone pipelines

Pipelines can also be **inlined** in the experiment YAML (`post:` at
experiment level, plus `scenario.post` / `item.post` / `run.post`). Use inline for simple post-processing;
use standalone `.yaml` files when you want to re-run analysis without
re-executing the benchmark.

---

## Part C — Trip Planner Topology Comparison

Now for a real experiment. The trip planner MAS
([`library-samples/apps/trip-planner/`](../../../library-samples/apps/trip-planner/)) uses a moderator + 3 specialists. But is
that topology actually better? Let's compare three approaches:

| Topology | Schema-backed fields | Description | Config |
|----------|----------------------|-------------|--------|
| **Single-agent** | One `workflow.entry` and one node | One agent handles the request | `topologies/single-agent.yaml` |
| **Delegation chain** | `nodes[].delegates_to` | Each node names its allowed next peer; this alone does not enforce execution order | `topologies/linear.yaml` |
| **Moderator** | Entry node with multiple `delegates_to` targets | The entry agent chooses which peer tools to call | `topologies/moderator.yaml` |

The MAS schema has no `workflow.type` or embedded `workflow.edges` field.
`delegates_to` declares allowed peers; it does not itself guarantee a sequence.
Parallel fan-out is expressed with node `dispatch: parallel`; deterministic
ordering depends on the entry agent's `design_pattern`.

### C.1 — Workflow shapes

#### Single-agent overlay

The overlay replaces the participant list with one generalist and updates the
entry node:

```yaml
# topologies/single-agent.yaml
apiVersion: mas/v1
kind: Overlay
metadata:
  name: single-agent
spec:
  target: {kind: MAS}
  patch:
    agents:
      $op:
        remove: [moderator, schedule_agent, itinerary_agent, concierge_agent]
        add:
          - id: generalist
            ref: agents/generalist/agent.yaml
    workflow: {entry: generalist, nodes: [{id: generalist}]}
```

#### Deterministic-linear overlay

The overlay keeps the roster, declares its peer targets, and selects the entry
agent's deterministic-linear design pattern:

```yaml
# topologies/linear.yaml
apiVersion: mas/v1
kind: Overlay
metadata:
  name: linear
spec:
  target: {kind: MAS}
  patch:
    workflow:
      entry: moderator
      nodes:
        - id: moderator
          delegates_to: [schedule_agent, itinerary_agent, concierge_agent]
        - id: schedule_agent
        - id: itinerary_agent
        - id: concierge_agent
    agents:
      moderator:
        design_pattern: deterministic_linear
```

#### Moderator overlay

The moderator overlay preserves LLM-directed delegation: the embedded MAS
workflow schema has no strategy selector. It declares the entry agent and peers
it may call; the entry agent's LLM chooses which peer tools to invoke. See the
current [trip-planner MAS manifest](../../../library-samples/apps/trip-planner/mas.yaml)
for the complete participant roster.

### C.2 — The dataset

The shipped smoke dataset uses the canonical Dataset envelope. Larger
trip-planning datasets use the same `inputs.user` + `expectations` shape:

```yaml
apiVersion: lab/v1
kind: Dataset
metadata:
  name: trip-planner-topology
spec:
  items:
    - id: item1
      inputs:
        user: Plan a weekend trip from Arborville to Coastport. Include transport options and one restaurant recommendation.
```

### C.3 — The experiment manifest

> The shipped `experiment-topology.yaml` is a runnable one-item smoke comparison
> using the trip-planner MAS and these overlays. The larger topology experiment
> lives in `labs/design-space.lab/02-topologies/`.

```yaml
# experiment-topology.yaml
experiment:
  name: "t3-topology-comparison"
  description: Compare the trip-planner topology overlays on one smoke item.

  default_flavour: local
  application:
    app: trip-planner
    configs_dir: ./topologies

  scenarios:
    - id: single-agent
      description: "One agent with all tools — no delegation"
      overlays: {logic: [single-agent], control: [], infra: []}
      tags: [single, baseline]
    - id: linear
      description: "Fixed chain: schedule → itinerary → concierge"
      overlays: {logic: [linear], control: [], infra: []}
      tags: [linear, sequential]
    - id: moderator
      description: "Dynamic delegation to specialists"
      overlays: {logic: [moderator], control: [], infra: []}
      tags: [moderator, broker]

  dataset:
    path: ./dataset-topology.yaml

  run:
    n_runs: 1
  schedule:
    ordering: coverage
```

### C.4 — Running the experiment

```bash
T03=docs/tutorials/03-experiments-and-analysis

# Run the one-item topology smoke comparison
mas-lab benchmark run "$T03/experiment-topology.yaml" --dry-run

# Full trip-planner topology sweep (larger dataset) — design-space lab:
mas-lab benchmark run labs/design-space.lab/02-topologies/experiment.yaml --progress
```

Expected output:

```
Running MAS benchmark 't3-topology-comparison'
  3 scenarios × 1 item × 1 run(s) = 3 executions

  ✅ [single-agent] item=item1 run=1 (...)
  ✅ [linear] item=item1 run=1 (...)
  ✅ [moderator] item=item1 run=1 (...)

MAS BENCHMARK COMPLETE — t3-topology-comparison
  Total: 3   OK: 3   Errors: 0
```

### C.5 — Inspecting the smoke run

The smoke manifest does not run an evaluator or plotting pipeline. Inspect
the run summary and traces:

```bash
mas-lab benchmark show last -v
```

Use the generated benchmark output as the source of truth for your run; values
depend on flavour, model, and runtime conditions.

### C.6 — Full topology experiment

For the larger topology matrix, use the design-space lab command above; it
includes the experiment pipelines for evaluation and plots.

```
Answer Relevancy by Topology (MCEv1, n=3 runs)

  1.0 ┤
      │                                          ┌───────┐
  0.9 ┤                              ┌───────┐   │       │
      │                              │       │   │  0.89 │
  0.8 ┤              ┌───────┐       │  0.78 │   │   ±   │
      │              │       │       │   ±   │   │ 0.03  │
  0.7 ┤  ┌───────┐   │  0.78 │       │ 0.05  │   │       │
      │  │       │   │   ±   │       │       │   │       │
  0.6 ┤  │  0.71 │   │ 0.04  │       │       │   │       │
      │  │   ±   │   │       │       │       │   │       │
  0.5 ┤  │ 0.08  │   │       │       │       │   │       │
      │  │       │   │       │       │       │   │       │
  0.0 ┼──┴───────┴───┴───────┴───────┴───────┴───┴───────┴──
      single-agent    linear          moderator
```

The ggplot2 code behind this (generated by `mas-lab benchmark analyze`):

```python
from plotnine import *
import pandas as pd

df = pd.read_csv("results.csv")

plot = (
    ggplot(df, aes(x='scenario', y='answer_relevancy', fill='scenario'))
    + geom_boxplot(alpha=0.7)
    + stat_summary(fun_y=np.mean, geom='point', shape='D', size=3)
    + labs(
        title="Answer Relevancy by Topology (MCEv1, n=3 runs)",
        x="Topology",
        y="Answer Relevancy Score"
    )
    + scale_fill_brewer(type='qual', palette='Set2')
    + theme_minimal()
    + theme(
        axis_text_x=element_text(angle=0, hjust=0.5),
        legend_position='none'
    )
)
plot.save("plots/answer_relevancy_by_scenario.svg", width=10, height=6, dpi=150)
```

### C.7 — Interpreting the results

The plot tells a clear story:

- **Single-agent** (0.71 ± 0.08): handles simple queries well, but drops quality
  on complex multi-step trips — one LLM with all tools lacks the focused
  instructions that specialized agents carry.
- **Sequential** (0.78 ± 0.04): the automaton-driven pipeline preserves
  information better (each agent adds to the context), but the fixed
  ordering means the concierge sometimes receives incomplete context from
  earlier stages — no opportunity to loop back.
- **Moderator** (0.89 ± 0.03): the LLM-driven moderator routes each sub-task
  to the most appropriate specialist dynamically. Higher latency (the
  moderator adds a coordination turn), but measurably better answer quality.

The variance (error bars) is also informative: the moderator has the tightest
confidence interval — its quality is more consistent across runs.

### C.8 — Drilling into individual runs

When the aggregate plot reveals a difference, you can drill down:

```bash
# Compare trajectory of same item across topologies (after a benchmark run)
mas-lab plot multilevel-trajectory \
  "$LABS_ROOT/t3-topology-comparison/single-agent/item1/r1/traces/events.jsonl" \
  --format html -o output/single-item1.html

mas-lab plot multilevel-trajectory \
  "$LABS_ROOT/t3-topology-comparison/moderator/item1/r1/traces/events.jsonl" \
  --format html -o output/moderator-item1.html
```

Item 11 (a complex 3-day trip) is where the moderator shines: the trajectory
shows parallel delegation to all 3 specialists, while the single-agent
trajectory shows a long serial chain of tool calls with no specialization.

---

## Key takeaways

1. **`events.jsonl` is the universal artifact**: every run produces one; all analysis starts here
2. **Manual analysis steps (OSS)**: telemetry show → plot trajectory → eval mce
3. **Pipelines make analysis reproducible**: same steps in YAML, with dependencies, run automatically
4. **MCEv1 `AnswerRelevancyMetric`**: LLM-as-judge scores answer quality (0–1 per session)
5. **Grouped plots reveal topology differences**: boxplot by scenario with confidence intervals
6. **Aggregate → drill-down**: start with the comparison plot, then inspect individual trajectories

---

## Infra bundles — declarative service provisioning

An **infra bundle** is a YAML file (analogous to a flavour) that declares the
infrastructure services an experiment needs — OTel collectors, databases,
port-forwards.  Services are started before the benchmark loop and stopped after
via the `service_start` / `service_stop` pipeline steps.

### Anatomy of a bundle

```
labs/my-experiment.lab/infra/
├── local-test.yaml     # Docker-based fake OTel collector
└── staging.yaml        # team staging infra (port-forward or direct)
```

```yaml
# infra/local-test.yaml
services:
  otel-collector:
    description: "Fake OTel collector for local testing"
    backend: docker-compose
    compose_file: "../../../infra/docker-compose.otel.yml"
    env:
      OTEL_EXPORTER_OTLP_ENDPOINT: "http://localhost:4318"
      OTEL_COLLECTOR_DIR: "/private/tmp/mas-otel"
    health_check:
      url: "http://localhost:13133/"
      timeout: 30
```

### Wiring bundle → pipeline

Declare lifecycle steps under experiment-level `pre:` / `post:` and
per-run hooks under `run.post`:

```yaml
experiment:
  default_infra: local-test    # selected when --infra is omitted

  pre:
    - type: service_start
      name: start-otel
      config:
        service: otel-collector
        health_timeout: 30
  post:
    - type: service_stop
      name: stop-otel
      config:
        service: otel-collector

  run:
    post:
      - type: export_otel
        config:
          events_jsonl: "{events_jsonl}"
          destination: otlp    # reads OTEL_EXPORTER_OTLP_ENDPOINT from env
          service_name: "my-experiment"
          overwrite: true
```

### Selecting the bundle

`--infra` is the CLI flag orthogonal to `--flavour`:

| Flag | Selects |
|------|---------|
| `--flavour local` | LLM / model configuration |
| `--infra local-test` | Infrastructure services (OTel, storage, …) |

```bash
# Local run — Docker collector auto-provisioned
mas-lab benchmark run experiment.yaml --infra local-test --single-run

# to the production collector endpoint)
mas-lab benchmark run experiment.yaml --infra staging

# No infra (destination: json — file output only, no network)
mas-lab benchmark run experiment.yaml
```

### How env vars flow

1. `service_start` calls `ServiceManager.start()` → runs `docker compose up -d`
2. The bundle's `env:` block is injected into the **current process** environment
3. `export_otel` with `destination: otlp` reads `OTEL_EXPORTER_OTLP_ENDPOINT`
   automatically from `os.environ`
4. `service_stop` runs `docker compose down` after all runs complete

> The same mechanism works for **port-forward backends** (k8s services, remote gateways,
> port-forward) — the bundle sets `OTEL_EXPORTER_OTLP_ENDPOINT` to the
> tunnelled address; no change to the experiment YAML is needed.

### Smoke test

The full end-to-end smoke test lives at
`labs/otel-smoke-test.lab/experiment.yaml` and demonstrates all four modes
(JSON file, managed Docker, dry-run, external backend).  Run it with:

```bash
# Mode 2: Docker auto-provisioned
mas-lab benchmark run labs/otel-smoke-test.lab/experiment.yaml \
    --infra local-test --single-run --force

# Inspect spans
cat /private/tmp/mas-otel/traces.jsonl | python3 -m json.tool | head -40
```

---

## Running the experiments

### QA agent analysis (Part A/B)

```bash
T03=docs/tutorials/03-experiments-and-analysis

# Run the QA benchmark (3 scenarios × 2 items × 1 run = 6 executions)
mas-lab benchmark run "$T03/experiment.yaml" --progress

# Run the analysis pipeline
mas-lab benchmark pipeline run "$T03/pipelines/analysis.yaml"
```

### Topology comparison (Part C)

```bash
# Tutorial scaffold (dry-run / small dataset)
mas-lab benchmark run "$T03/experiment-topology.yaml" --dry-run

# Full trip-planner comparison — design-space lab
mas-lab benchmark run labs/design-space.lab/02-topologies/experiment.yaml --progress

# Inspect benchmark metadata and outputs
mas-lab benchmark show last -v
```

---

## File structure

Tutorial artifacts live beside this README:

```
docs/tutorials/03-experiments-and-analysis/
├── README.md
├── agent.yaml                       # Q&A agent (Tutorial 1 style)
├── mas.yaml                         # single-agent MAS wrapper
├── dataset.yaml                     # 2 QA items
├── experiment.yaml                  # Part A/B — pattern sweep
├── experiment-topology.yaml         # Part C scaffold
├── pipelines/analysis.yaml          # post-benchmark analysis
├── overlays/                        # CoT, ReAct, Reflection
├── fixtures/events.jsonl            # offline trace for demo/scenario.yaml
└── demo/scenario.yaml               # CI command replay + expected exit codes
```

Larger experiments: `labs/design-space.lab/` (patterns + topologies).

---

## Next

[Tutorial 8 — Telemetry](../08-telemetry/) converts these native traces to
OTel (live plugin or replay). [Tutorial 9 — KG & OXP](../09-kg-oxp/)
normalizes OTel or native events into a knowledge graph and checks the two
KG paths match.

Use the labs as your base to create additional experiments:

- duplicate an existing `labs/*.lab/experiment.yaml`
- point it to a new dataset or topology overlay
- run `mas-lab benchmark run ...` and compare outputs
