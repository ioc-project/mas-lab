<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Tutorial 3 — Experiments, Analysis and Evaluation

> **Packages:** `mas-lab`, `mas-runtime`
> **Time:** ~60 min

## Goal

You run an experiment, analyze its traces, and compare three MAS topologies on the trip planner.
An experiment is a YAML file that runs a MAS over a dataset under several overlays.
Each run writes `events.jsonl`, the machine-readable event log.

## What you need

- [Tutorial 0](../00-environment-setup/README.md) (data paths), [Tutorial 1](../01-building-an-agent/README.md), [Tutorial 2](../02-creating-a-mas/README.md).
- `OPENAI_API_KEY` for the live benchmarks (Parts A and C). Steps marked "offline" need no key.
- `mas-lab config` shows where your machine stores lab output and the trace cache.

A lab is a pack of experiments. A library is reusable YAML and code. See [labs-and-libraries.md](../../labs-and-libraries.md).

| Part | What you do |
|------|-------------|
| A | Analyze traces by hand on a QA agent (ReAct, chain of thought, Reflection variants) |
| B | Turn those steps into a declarative pipeline |
| C | Compare single-agent, linear, and moderator topologies on the trip planner (one-item smoke run) |

---

## Part A — Analyze traces by hand

Run this tutorial's benchmark first. The traces do not come from Tutorial 1.

```bash
# From the repository root. Dry run is offline: 3 scenarios x 2 items x 1 run = 6 executions
mas-lab benchmark run docs/tutorials/03-experiments-and-analysis/experiment.yaml --dry-run
mas-lab benchmark run docs/tutorials/03-experiments-and-analysis/experiment.yaml
```

The dry run prints the experiment name, the scenarios (`cot`, `react`, `reflection`), `Total : 6 executions`, and `✓ Configuration valid`.
The live run makes real LLM calls (6 short runs) and prints `Results: …/results.csv` when it ends.
`--no-progress` turns off the progress bar. The first `benchmark` command starts the local controller daemon.
For offline analysis, use `fixtures/events.jsonl`, a hand-written trace of this tutorial's QA agent
(tool `calculator`, fixed timestamps).

### Where traces are stored

The runner derives the output directory from the experiment's `name:` field (`t3-observability-patterns`).
It lands in `<labs_root>/t3-observability-patterns/`, by default `$XDG_DATA_HOME/mas/labs/`.
Override with `labs_dir` / `cache_dir` in `$XDG_CONFIG_HOME/mas/config.yaml` or the workspace `config.yaml`.
Inside a `.lab` workspace the path is `<labs_root>/<lab_name>/<experiment_name>/`. No config file? See Tutorial 0.

```bash
mas-lab config
export LABS_ROOT="$(python -c 'from mas.lab.paths import labs_root; print(labs_root())')"
export TRACE="$LABS_ROOT/t3-observability-patterns/react/item1/r1/traces/events.jsonl"
```

```
$LABS_ROOT/t3-observability-patterns/
├── metadata.yaml
├── react/                    ← scenario
│   └── item1/r1/             ← dataset item, run number
│       ├── traces/events.jsonl   (*)
│       ├── run_info.json         ← timing, status, config
│       └── .run_ref              ← cache hash pointer
├── cot/ ...
└── reflection/ ...
```

(\*) `traces/` is a symlink to `<trace_cache>/<hash>/traces/`. The runner hashes the deterministic inputs
(resolved config, prompt, model, run index) and stores each trace once. Re-running with the same inputs hits the cache.
`--force` rebuilds the output tree but still serves cache hits. To call the LLM again, set
`bench_emulation.runtime.cache: disabled` in the experiment manifest.

> **The trace cache is not the LLM response cache.** The trace cache deduplicates whole runs.
> Each LLM call has its own response cache (`runtime_refs`, or `--cache-read` / `--cache-write` on `mas-ctl chat`).
> Offline CI replay uses [llm_cache](../../manifests/llm-cache.md). See [runtime-engine.md](../../manifests/runtime-engine.md).

### A.1 — Read the raw event stream

```bash
head -5 "$TRACE"
# offline: head -5 docs/tutorials/03-experiments-and-analysis/fixtures/events.jsonl
```

Each line is one event at a runtime boundary. These are the fixture's first lines (abridged):

```json
{"kind":"user_input","agent_id":"mas","call_id":"t3-mas-001","session_id":"t3-session-001","content":"What is 17 × 23? ...","timestamp":1716200000.0}
{"kind":"mas_call_start","agent_id":"mas","call_id":"t3-mas-001",...}
{"kind":"llm_call_start","agent_id":"qa-agent-t3","call_id":"t3-llm-001","parent_call_id":"t3-exec-001","model":"openai/gpt-4o-mini",...}
{"kind":"llm_call_end","agent_id":"qa-agent-t3","call_id":"t3-llm-001","status":"success",...}
```

| Field | Meaning |
|-------|---------|
| `kind` | Event type: `mas_call_*`, `execution_*`, `llm_call_*`, `tool_call_*`, ... |
| `agent_id` | Agent that emitted the event |
| `call_id` / `parent_call_id` | A call and the call containing it. `*_start`/`*_end` pairs share a `call_id` |
| `session_id` | Links all events of one session |
| `timestamp` | Seconds since the epoch |

### A.2 — Summarize the telemetry

```bash
mas-lab telemetry show "$TRACE"
# offline: mas-lab telemetry show docs/tutorials/03-experiments-and-analysis/fixtures/events.jsonl
mas-lab telemetry show "$TRACE" --filter tool_call
```

You get the event count, agents, time range, and a breakdown by kind. `--filter tool_call` lists only the matching events, one line each.
On the fixture you see a multiply step (`17 * 23`), a modulo check (`391 % 17`), then the final answer.

### A.3 — Score answer quality (MCEv1)

MCEv1 is the first version of the MCE metrics. They are LLM-as-judge scores, so they need a model key. List them offline first:

```bash
mas-lab eval --list-metrics        # ids: answer_relevancy, goal_success_rate, groundedness, ...
```

Then score a trace. Metric ids are snake_case (`goal_success_rate`, `groundedness`, `answer_relevancy`):

```bash
export OPENAI_API_KEY=...          # judge key; --api-key-env names another variable
mas-lab eval docs/tutorials/03-experiments-and-analysis/fixtures/events.jsonl \
  --metric goal_success_rate --metric groundedness --model gpt-4o
```

You get one row per metric: a score from 0 to 1 and the judge's reasoning. `--json` gives machine-readable output.
Scoring calls the judge model, so it needs a working key. An unknown name prints the list of valid ids.
Add `--recursive` to score every trace under a directory.

### A.4 — Plot trajectories

Set `TRACE` as in Part A. Offline, use `docs/tutorials/03-experiments-and-analysis/fixtures/events.jsonl` instead.

```bash
mas-lab plot trajectory "$TRACE" --format svg -o output/trajectory-item1.svg
mas-lab plot multilevel-trajectory "$TRACE" --format html -o output/swimlane-item1.html
```

The SVG is a compact delegation diagram. The HTML swimlane has one lane per agent, tool calls as blocks, and clickable events.
Both run offline on the fixture. Open the HTML in a browser.

### The experiment.yaml hierarchy

A benchmark has four levels. **Total executions = M scenarios × D items × N runs.**

| Level | Defined by | Multiplicity |
|-------|-----------|--------------|
| Experiment | `experiment.yaml` + root `post:`. `application:` binds the MAS under test | 1 |
| Scenario | `scenarios:` list. Each entry is an overlay stack (the independent variable) | M |
| Item | One entry in `dataset:` | D |
| Run | `run.n_runs` repetitions, to measure variance | N |

```yaml
scenarios:
  - id: cot
    overlays: {logic: [cot], control: [], infra: []}
dataset:
  path: "dataset.yaml"          # items: [{id: "item1", prompt: "..."}, ...]
run:
  n_runs: 3
schedule:
  ordering: coverage            # breadth-first across all conditions
```

Older names call the MAS binding "application" and an item a "test". `application:` is still current.
Pin LLMs on the experiment, not in overlays: `experiment.models` has slots `main` (turns), `summarizer`, and `judge` (MCE).
`experiment.model` is shorthand for `models.main`. Agents with `model: any` inherit them
([experiment.md](../../manifests/experiment.md#model-slots-models-model)).

Pipeline steps run after the benchmark, each at one scope. This tutorial keeps the experiment's inline pipelines empty.

| Scope | YAML / CLI | Template variables |
|-------|------------|--------------------|
| `run` | `run.post` / `--depth run` | `{scenario_id}`, `{item_id}`, `{run_dir}`, `{events_jsonl}` |
| `item` | `item.post` / `--depth item` | `{scenario_id}`, `{item_id}` |
| `scenario` | `scenario.post` / `--depth scenario` | `{scenario_id}` |
| `exp` | root `post:` / `--depth exp` | (none; artifact paths are relative to the level folder) |

| Tutorial file | Experiment `name:` | Output directory |
|---------------|-------------------|------------------|
| `experiment.yaml` | `t3-observability-patterns` | `$LABS_ROOT/t3-observability-patterns/` |
| `experiment-topology.yaml` | `t3-topology-comparison` | `$LABS_ROOT/t3-topology-comparison/` |

---

## Part B — Turn the commands into a pipeline

A pipeline declares the Part A steps in YAML with explicit dependencies, so they run after every benchmark.

```yaml
# pipelines/analysis.yaml (abridged)
pipeline:
  name: t3-analysis
  steps:
    - {name: extract, type: extract_trajectories}
    - name: trace-stats
      type: extract_trace_stats
      depends_on: [extract]
      config: {output: "results/trace_stats.csv"}
    - {name: plot-svg,  type: plot_multilevel_trajectory, depends_on: [extract], config: {format: svg,  filename: trajectory}}
    - {name: plot-html, type: plot_multilevel_trajectory, depends_on: [extract], config: {format: html, filename: swimlane}}
```

`extract` runs first. The other three steps are independent and may run in parallel.

| Manual command (Part A) | Step type | Produces |
|-------------------------|-----------|----------|
| gather each run's trajectory | `extract_trajectories` | `trajectories.jsonl`, per-run `trajectory.json` |
| `mas-lab telemetry show` | `extract_trace_stats` | `results/trace_stats.csv` |
| `mas-lab plot multilevel-trajectory` | `plot_multilevel_trajectory` | `trajectory.svg`, `swimlane.html` |

`mas-lab benchmark step-info STEP_TYPE` lists a step's parameters.

Point the pipeline at a completed benchmark's output with `-o`. The benchmark prints it as `Results: …/results.csv`:

```bash
T03=docs/tutorials/03-experiments-and-analysis
LABS_ROOT="$(python -c 'from mas.lab.paths import labs_root; print(labs_root())')"
LAB="$LABS_ROOT/t3-observability-patterns"

mas-lab benchmark pipeline run "$T03/pipelines/analysis.yaml" -o "$LAB"                      # full pipeline
mas-lab benchmark pipeline run "$T03/pipelines/analysis.yaml" -o "$LAB" --only extract       # one step plus dependencies
mas-lab benchmark pipeline run "$T03/pipelines/analysis.yaml" -o "$LAB" --force plot-html    # recompute one step
```

The plots land in `$LAB/trajectories/` and the statistics in `$LAB/results/trace_stats.csv`.
The pipeline definition is [pipelines/analysis.yaml](pipelines/analysis.yaml). Without a completed benchmark, plot steps fail with "no trace discoverable".
Use an inline pipeline (`post:` in the experiment) for simple post-processing. Use a standalone file to re-run analysis without re-running the benchmark.

---

## Part C — Compare trip planner topologies

The [trip planner](https://github.com/ioc-project/mas-lab/tree/main/library-samples/apps/trip-planner) uses a moderator and three specialists.
Is that better than the alternatives? Each topology is an overlay in `topologies/`:

| Topology | Fields | Behaviour |
|----------|--------|-----------|
| Single-agent (`single-agent.yaml`) | One `workflow.entry`, one node | One generalist handles the request |
| Deterministic linear (`linear.yaml`) | `delegates_to` plus entry `design_pattern: deterministic_linear` | Plugin calls specialists in declared order |
| Moderator (`moderator.yaml`) | Entry `delegates_to` targets, `design_pattern: react` | Entry agent's LLM picks peers |

`delegates_to` declares allowed peers, not a sequence. Parallel fan-out uses `dispatch: parallel` on a node.
The schema has no `workflow.type` or `workflow.edges`.

### C.1 — Read the overlays

```yaml
# topologies/single-agent.yaml (patch)
agents:
  $op:
    remove: [moderator, schedule_agent, itinerary_agent, concierge_agent]
    add:
      - id: generalist
        ref: ../../../library-samples/apps/trip-planner/agents/generalist/agent.yaml
workflow: {entry: generalist, nodes: [{id: generalist}]}
```

Files: [single-agent](topologies/single-agent.yaml), [linear](topologies/linear.yaml), [moderator](topologies/moderator.yaml).
`topologies/linear.yaml` is the same overlay as `overlays/linear.yaml` in [Tutorial 2](../02-creating-a-mas/README.md).
`topologies/moderator.yaml` keeps LLM-directed delegation. See the
[trip-planner manifest](https://github.com/ioc-project/mas-lab/blob/main/library-samples/apps/trip-planner/mas.yaml) for the full roster.

### C.2 — Read the dataset and experiment

`dataset-topology.yaml` has one item (larger datasets use the same `inputs.user` / `expectations` shape):

```yaml
spec:
  items:
    - id: item1
      inputs:
        user: Plan a weekend trip from Arborville to Coastport. Include transport options and one restaurant recommendation.
```

```yaml
# experiment-topology.yaml (abridged)
experiment:
  name: "t3-topology-comparison"
  default_flavour: local
  models: {main: gpt-4o-mini}
  application:
    app: trip-planner            # library-samples/apps/trip-planner
    configs_dir: ./topologies    # scenario overlays are looked up by name here
  scenarios:
    - {id: single-agent, overlays: {logic: [single-agent], control: [], infra: []}}
    - {id: linear,       overlays: {logic: [linear],       control: [], infra: []}}
    - {id: moderator,    overlays: {logic: [moderator],    control: [], infra: []}}
  dataset: {path: ./dataset-topology.yaml}
  run: {n_runs: 1}
  post: []
```

### C.3 — Run and inspect

```bash
T03=docs/tutorials/03-experiments-and-analysis
mas-lab benchmark run "$T03/experiment-topology.yaml" --dry-run   # offline: 3 x 1 x 1 = 3 executions
mas-lab benchmark run "$T03/experiment-topology.yaml"             # live
mas-lab benchmark show last -v
```

`benchmark show last -v` prints the latest run's status, scenarios, and per-run details.
The dry run prints:

```text
Experiment : t3-topology-comparison
Scenarios  : ['single-agent', 'linear', 'moderator']
Dataset    : 1 items
Total      : 3 executions
✓ Configuration valid — ready to run
```

The smoke manifest runs no evaluator or plot pipeline. Your run's output is the source of truth, and it depends on flavour, model, and conditions.

### C.4 — Drill into individual runs

```bash
LABS_ROOT="$(python -c 'from mas.lab.paths import labs_root; print(labs_root())')"
mas-lab plot multilevel-trajectory \
  "$LABS_ROOT/t3-topology-comparison/moderator/item1/r1/traces/events.jsonl" \
  --format html -o output/moderator-item1.html
```

Repeat with `single-agent` in the path. The moderator trajectory shows `delegate_to_<agent>` handoffs.
The single-agent trajectory shows one lane with every tool call in a serial chain.

### C.5 — The full sweep and how to read it

`labs/design-space.lab/02-topologies/experiment.yaml` runs five topologies (linear pipeline, parallel, moderator-broker,
supervised, verifier) over the full dataset. Its pipelines score MCEv1 and plot a grouped comparison with confidence intervals.
It has hundreds of live executions. The lab's trace cache makes re-runs cheap.
Regenerate statistics and plots from existing results with `mas-lab benchmark analyze BENCHMARK_ID`.

```bash
mas-lab benchmark run labs/design-space.lab/02-topologies/experiment.yaml
```

Read results along three axes:

- **Quality:** the MCE score per scenario with its confidence interval.
- **Cost:** tokens, tool calls, and latency from `extract_trace_stats`. A moderator adds LLM turns.
- **Behaviour:** which agents were called, in what order, and where the time went.

Treat one number as a hypothesis. Models, prompts, and datasets change rankings. This tutorial ships no result numbers.

---

## Service bundles

A benchmark can start supporting services (a collector, a database) before the runs and stop them after.
A **service bundle** is an `infra/v1` document (`kind: ServiceBundle`) in
`<experiment_dir>/infra/`. Lab `--infra NAME` selects it for `service_start` /
`service_stop`. It is not an LLM `--infra-ref` (Tutorial 0).
This one runs a local HTTP server as a background process, with no Docker:

```yaml
# infra/local-http.yaml
apiVersion: infra/v1
kind: ServiceBundle
metadata:
  name: local-http
spec:
  services:
    local-http:
      backend: process
      command: [python3, -m, http.server, "8765", --bind, 127.0.0.1]
      env: {T3_SERVICE_URL: "http://127.0.0.1:8765"}
      health_check: {url: "http://127.0.0.1:8765/", timeout: 15}
```

`experiment-services.yaml` adds a `service_start` step under `pre:` and a `service_stop` step under `post:`.
It selects the bundle with `default_infra: local-http`; `--infra NAME` overrides it.

```bash
cd docs/tutorials/03-experiments-and-analysis
mas-lab benchmark run experiment-services.yaml --infra local-http --dry-run
```

The dry run prints `Pipeline   : 2 steps (2 experiment)` and `Configuration valid`, and starts nothing.
Drop `--dry-run` to start the server and export `T3_SERVICE_URL` to later steps. `mas-lab benchmark step-info service_start` lists parameters.

---

## Going further

- **Telemetry export:** [Tutorial 8](../08-telemetry/README.md) covers `export_otel` and service bundles end to end.
- **Knowledge graph:** [Tutorial 9](../09-kg-scope/README.md) normalizes traces into a graph.
- **Larger experiments:** `labs/design-space.lab/` (patterns and topologies).
- **CI:** `pytest tests/tutorials/test_scenario_commands.py -v -k tuto-03` replays the offline steps from `demo/scenario.yaml`.

## What you learned

- A benchmark expands Experiment, Scenario, Item, Run. Each level writes its own output and `events.jsonl` traces.
- You can analyze a trace by hand (telemetry, plots, MCE scoring) and turn those steps into a re-runnable pipeline.
- You can compare MAS topologies with overlays, from a smoke run to the design-space lab.

## Next

- [Tutorial 4 — MCP tools](../04-mcp-tools/README.md) and [Tutorial 5 — A2A agents](../05-a2a-agents/README.md) move tools and peers behind protocols.
- [Tutorial 8 — Telemetry](../08-telemetry/README.md) and [Tutorial 9 — KG and SCOPE](../09-kg-scope/README.md) build on these traces.

To build your own experiment, duplicate a `labs/*.lab/experiment.yaml`, point it at a new dataset or overlay, and run `mas-lab benchmark run ...`.
