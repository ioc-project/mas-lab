<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Dataset manifest (`kind: Dataset`)

**Package:** `mas-lab-bench` · **Schema:** `dataset.schema.yaml` · **apiVersion:** `lab/v1`

A **dataset** manifest lists the inputs of a benchmark.
Each **item** is one case. It holds a user prompt and, optionally, human-in-the-loop (HITL) replies, memory seeds, tool fixtures, and expected results.
Write a dataset when an **experiment** needs a fixed set of inputs to run every **scenario** against.
The experiment pairs the dataset with its scenarios and `run.n_runs`.

Every item uses the **dataset envelope**: an `inputs` block (what the run receives) and an `expectations` block (what you score against).
An older flat item (`prompt:`, top-level `ground_truth`) still loads with a warning. [dataset-migration.md](dataset-migration.md) shows how to convert it.

Smallest dataset:

```yaml
apiVersion: lab/v1
kind: Dataset
metadata:
  name: my-dataset
spec:
  items:
    - id: "001"
      inputs:
        user: Plan a three-day trip to Paris.
```

Terms: [glossary.md](../glossary.md). Experiment wiring: [experiment.md](experiment.md).

A **run** is identified by this tuple:

```text
run = (application, flavour, memory_state, user_query)
```

The dataset declares `memory_state` and `user_query` per item.
The experiment YAML supplies `application` and `default_flavour`.
An item has no chat-log `turns:` field.

## Contents

- [Fields you always set](#fields-you-always-set): [1. Manifest format](#1-manifest-format), [2. Item fields](#2-item-fields-reference), [3. Path references](#3-path-references)
- [Fields you sometimes set](#fields-you-sometimes-set): [4. Memory seeds](#4-memory-seeds-the-memory-state-slot), [5. HITL](#5-hitl-optional), [6. Session continuity](#6-session-continuity), [7. Experiment-level seeds](#7-experiment-level-seeds-and-merge-order), [8. Tool fixtures](#8-tool-fixtures), [9. Schemas for free-form parts](#9-schemas-for-the-free-form-parts)
- [Full example](#full-example)
- [Advanced fields](#advanced-fields): [10. App-specific datasets](#10-app-specific-and-general-purpose-datasets), [11. `spec.source`](#11-specsource-meta-datasets-mmlu-pro)
- [Common mistakes](#common-mistakes)

---

## Fields you always set

### 1. Manifest format

A dataset file is a YAML document. Two formats load.

**Declarative manifest** (preferred):

```yaml
apiVersion: lab/v1
kind: Dataset
metadata:
  name: my-dataset
  version: "1.0"
  description: "Trip planning evaluation items."
spec:
  app: my-app@>=v1,<v3   # omit for general-purpose datasets
  items:
    - id: "001"
      inputs:
        user: Plan a three-day trip to Paris.
```

**Flat dict** (shorthand, no `kind` or `apiVersion`):

```yaml
name: my-dataset
items:
  - id: "001"
    inputs:
      user: Plan a three-day trip to Paris.
```

The loader accepts the flat form and takes `name` from the file stem when it is omitted.
The flat form is not schema-valid: `mas-lab validate` and `mas-ctl validate` recognise only the declarative form.
Use the declarative form for anything you check in.

Check a Dataset file with `mas-lab validate datasets/my-dataset.yaml` (it detects the kind from `kind: Dataset`) or with `mas-ctl validate`.

#### Document fields

| Field | Required | Meaning |
|---|---|---|
| `apiVersion` | yes | `lab/v1`. |
| `kind` | yes | `Dataset`. |
| `metadata.name` | yes | Dataset name (the catalog id when catalogued). |
| `metadata.version` | no | Version tag (`"1.0"`, `v1`). |
| `metadata.display_name`, `description`, `tags` | no | Labels for reports and UIs. |
| `spec.items` | one of `items` / `path` / `source` | Inline items (see [section 2](#2-item-fields-reference)). |
| `spec.path` | | Sidecar file of already-shaped items (YAML, JSONL, or CSV) next to this file. `spec.format` (`yaml`, `jsonl`, or `csv`; default `yaml`) is a format hint. |
| `spec.source` | | External corpus plus column map; see [section 11](#11-specsource-meta-datasets-mmlu-pro). |
| `spec.app` | no | Compatible app range: `[library:]name[@tag-or-range]` (`my-app@v1`, `my-app@^v1`, `my-app@>=v1,<v3`) or `{name, library, version}`. Omit for general-purpose datasets. |
| `spec.schemas` | no | JSON Schemas for the free-form item parts; see [section 9](#9-schemas-for-the-free-form-parts). |
| `spec.groups` | no | Declared item groups `[{id, description, count}]`. Documentation only: the loader does not read it. Items select a group with their own `group` key. |

### 2. Item fields reference

Each item follows `run-input.schema.yaml`.

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string | yes | Unique identifier within the dataset. Used by coupled experiments, output paths, and the run hash. |
| `inputs.user` | string, string list, or `{ref:}` | yes | The user prompt. A string is the text, never a path. A list of strings is sequential user messages. `{ref: file}` loads a sibling file. |
| `inputs.hitl` | string, string list, or `{ref:}` | no | Operator replies after the prompt, in order. |
| `inputs.memory_seeds` | list, `{ref:}`, or bare path | no | Initial memory state. See [section 4](#4-memory-seeds-the-memory-state-slot). |
| `inputs.tool_fixtures` | `{ref:}`, bare path, or mapping | no | Mock-tool payloads. See [section 8](#8-tool-fixtures). |
| `inputs.session_id` | string | no | Fixed conversation identifier. See [section 6](#6-session-continuity). |
| `inputs.checkpoint.load` | path, `{ref: path}`, or `null` | no | Checkpoint to resume this item's run from. An experiment-level `checkpoints` axis overrides it when declared. See [checkpoint.md](checkpoint.md). |
| `inputs.checkpoint.save` | boolean or string | no (`false`) | `true` (or a string) saves checkpoints under the run's `checkpoints/` folder after each turn. |
| `expectations.ground_truth` | any | no | Reference answer for generic metrics. |
| `expectations.metrics` | string list | no | Metrics this item is scored on. |
| `expectations.details` | object | no | Free-form app- or tool-specific ground truth. The schema rejects any other top-level `expectations` key. |
| `category` / `group` / `type` / `tags` | string / list | no | Filtering metadata (`experiment.dataset.filter`). Coupled mode uses `id` only. |
| `metadata` | object | no | Free-form item metadata, carried into results. |

`expectations` may also be `{ref: file}`, which loads the whole block from a file.

Which slots the agent sees:

| Slot | Agent sees it |
|---|---|
| `inputs.user` | Yes, as the prompt. |
| `inputs.hitl` | As HITL replies. |
| `inputs.memory_seeds` | Through memory. |
| `inputs.tool_fixtures` | Through tools. |
| `inputs.checkpoint.load` | Through the restored session. |
| `inputs.checkpoint.save`, `expectations` | No. |

Smallest item with ground truth:

```yaml
- id: order-42-refund
  inputs:
    user: >
      Customer asks for a refund on order 42...
    tool_fixtures: {ref: tool_fixtures/order-42.yaml}
  expectations:
    ground_truth: refund approved
    details:              # owned by the app's evaluators
      order_id: 42
      action: refund
```

A long prompt can live in a file. It uses the same `ref` syntax:

```yaml
inputs:
  user: {ref: prompts/order-42-refund.txt}
  tool_fixtures: {ref: tool_fixtures/order-42.yaml}
```

On `tool_fixtures` and `memory_seeds`, a bare path means the same as `{ref: path}`.
Ground truth stays on the same item, under `expectations`.

### 3. Path references

The loader resolves every relative path in a dataset file from the dataset file itself.
It does not use the experiment YAML or the working directory.
Dataset files stay portable: you can move an experiment directory without breaking seed paths.

```text
labs/my-experiment/
  experiment.yaml
  datasets/
    trip_queries.yaml          <- relative paths resolve from here
    seeds/
      user_alice.yaml
      user_bob.yaml
```

In `trip_queries.yaml`:

```yaml
spec:
  items:
    - id: "alice"
      inputs:
        user: Plan my trip.
        memory_seeds: {ref: seeds/user_alice.yaml}
```

Paths in `experiment.yaml` itself (`dataset.path`, checkpoint paths) resolve from the experiment YAML.

---

## Fields you sometimes set

### 4. Memory seeds: the memory state slot

A **seed** is a piece of text made available to the agents before the first prompt.
It models the prior state of the world from the agents' perspective: what they already know before the conversation starts.
It is the second slot of the run input: `run_input = (user_query, memory_state)`.

Without seeds, every run starts with no prior knowledge. That is the default for reproducible benchmarks.
Seeds let you test agents against specific prior knowledge as a versioned, reproducible input.
Examples: a user's history, a shared catalogue, a background fact.

The seed list is part of the run input, so it is part of the run hash.
Two runs that differ only in their seeds use separate trace-cache entries.

**How the native runner delivers seeds.**
`mas-lab benchmark run` with the default `native` runner adds each seed to the run's assembled context as a line `[memory:<source>] <content>`.
It does not index dataset seeds into a memory backend.
Every seed reaches the run as shared context.
To pre-index documents into an agent's semantic memory, use `spec.memory_seed` on the [Agent manifest](agent.md), or an overlay that appends to it.

!!! note "Not used yet"
    The native runner does not act on `target_agent` or `metadata` on a seed.

#### Seed fields

Each seed is a mapping (schema `fragments/memory-seed-entry.schema.yaml`):

| Field | Type | Required | Description |
|---|---|---|---|
| `content` | string | yes | The seed text. |
| `source` | string | no | Provenance label for the text (for example `"user_history"`, `"policy"`). Shown as the `[memory:<source>]` prefix. `key` is accepted as an alias (MemorySeed documents use `key`). |
| `target_agent` | string | no | Agent the seed is meant for. Kept in the run input. The native runner does not route on it. |
| `metadata` | mapping | no | Free-form key/value pairs kept with the seed. The native runner does not use them. |

`source` is a human-readable provenance label, not a file path.
The label prints next to the content, so traces show which seed a piece of context came from.

```yaml
source: "user_history"       # knowledge from a user activity log
source: "product_catalog"    # knowledge from the product database
source: "travel_policy"      # company travel rules
source: "operator_briefing"  # pre-run context injected by an operator
```

A seed may name one agent, or serve every agent:

```yaml
memory_seeds:
  # Intended for one agent (kept in the run input; see the note above)
  - source: "user_preferences"
    content: "Alice prefers window seats and vegetarian meals."
    target_agent: "concierge_agent"

  # Shared knowledge for every agent
  - source: "shared_policy"
    content: "Company travel policy: economy class for flights under 3h."
```

#### Inline list or path reference

Declare seeds inline, as `{ref: seeds/user_alice.yaml}`, or as a bare path (shorthand on this slot only):

```yaml
# Inline
inputs:
  memory_seeds:
    - source: "user_history"
      content: "Alice visited Paris in March."

# Canonical ref
inputs:
  memory_seeds: {ref: seeds/user_alice.yaml}

# Shorthand (same meaning as {ref:} on this slot)
inputs:
  memory_seeds: seeds/user_alice.yaml
```

The seed file may be one of three shapes:

- a bare list;
- a mapping with a `seeds`, `items`, `memory_seeds`, or `entries` list;
- a `kind: MemorySeed` document (`entries:` with `key` / `content`; schema `memory-seed.schema.yaml`).

```yaml
# seeds/user_alice.yaml (bare list)
- source: "user_history"
  content: "Alice visited Paris in March."
- source: "user_preferences"
  content: "Alice prefers boutique hotels."
```

Use a path reference when:

- several dataset items share a large seed file;
- a pipeline step generates the seeds and writes them to a file;
- the seed corpus is large enough to deserve its own version history.

### 5. HITL (optional)

Most items are a single `inputs.user` string.
To inject operator replies after that prompt, list them as strings under `inputs.hitl`:

```yaml
- id: approval
  inputs:
    user: Book a flight to Tokyo.
    hitl:
      - "Operator: budget approved up to €3,000."   # quote it: a bare `Operator: ...` is a YAML mapping
```

The public YAML has no `turns` list and no `{role, content}` objects.
For sequential user messages (rare), give `user` a list of strings:

```yaml
- id: memory-conversation
  inputs:
    user:
      - My name is Alex and I work at Acme Corp.
      - What is my name and where do I work?
```

The runner tags messages internally for traces. That tag is not an authoring format.

**Memory plus HITL.**
The runner applies memory seeds before the initial `inputs.user` prompt.
It then sends the user messages and HITL replies in order.
Memory writes made during the conversation accumulate on top of the seeds, which is the behaviour you want when testing stateful agents.

### 6. Session continuity

`inputs.session_id` pins the conversation identifier for an item.
Use it to replay a known conversation in the same session slot, or to test session-keyed memory with deterministic inputs.
The loader passes it to the lab runner as the run's `session_id`.

```yaml
- id: "session-replay-001"
  inputs:
    user: Continue our last conversation.
    session_id: "abc-1234-deterministic"
```

`session_id` is part of the run input. Changing it changes the run hash, so it creates a new trace-cache entry.

!!! note "Not used yet"
    The default `native` runner creates a fresh UUID session for every run and ignores `inputs.session_id`.
    To start a run from a saved session, use `inputs.checkpoint.load` or the experiment `checkpoints` axis.

### 7. Experiment-level seeds and merge order

`experiment.yaml` has no `memory_seeds` key. The experiment schema rejects it.
For knowledge every run should see, use one of two options:

- a shared seed file referenced from each item (`memory_seeds: {ref: seeds/shared.yaml}`);
- `spec.memory_seed` on the [Agent manifest](agent.md), or an [overlay](overlay.md) that appends to it for one scenario.

The agent loads its seeds when it starts. Overlay entries are appended to the agent's existing list.

Each item carries one seed list (`inputs.memory_seeds`), so there is nothing to merge at the experiment level.
Agent-level `spec.memory_seed` is configured on the agent and is independent of the dataset's seeds.
The loader does not deduplicate by `source`: if two seeds share a label, the run applies both.

### 8. Tool fixtures

`inputs.tool_fixtures` gives mock tools their payloads.
Both `inputs.tool_fixtures` and `expectations` have two layers.
mas-lab reads the generic layer. The app owns the free-form layer. Do not mix them.

```yaml
# tool_fixtures: generic mapping of which tool gets which payload.
tool_fixtures: tool_fixtures/order-42.yaml      # every tool (same as {ref: ...})
tool_fixtures:
  by_tool:
    "*": tool_fixtures/order-42.yaml            # every tool without its own entry
    query_db: tool_fixtures/db-rows.yaml
    get_weather: {city: Paris, temp_c: 18}     # inline payload
tool_fixtures:
  - tool: get_metrics
    ref: tool_fixtures/metrics.yaml
# The payload (file body or inline value) is free-form: mas-lab does not
# schema-check it, the tool does.

# expectations: generic keys + free-form details.
expectations:
  ground_truth: ...      # compared by generic metrics
  metrics: [exact_match] # metrics this item is scored on
  details: {...}         # app- or tool-specific ground truth
```

The loader rejects an inline payload placed directly under `tool_fixtures` without `by_tool`.
Keep ground truth out of tool-fixture payloads.

Tools receive their payload through the call context, not through files or environment variables:

```python
from mas.runtime.contracts import ToolContract, tool_fixture

class GetMetrics(ToolContract):
    def on_execute_tool(self, tool_name, arguments, *, ctx=None, **_):
        payload = tool_fixture(ctx, tool_name)  # by_tool[name], else by_tool["*"]
        ...
```

Overlay `spec.patch.params` reach tools the same way, as `ctx.runtime_params`.

A dataset version is a folder next to the agents and tools of the app version it evaluates:

```text
apps/<app>/v1/datasets/<name>/
  dataset.yaml                 # items: inputs + expectations
  tool_fixtures/*.yaml         # payloads listed from items
```

### 9. Schemas for the free-form parts

mas-lab does not interpret payloads or `details`.
A Dataset can still declare JSON Schemas for them in `spec.schemas`.
Keys are dotted envelope paths, the same vocabulary as [`spec.source.map`](#112-map).
Only free-form roots are allowed, because the run-input schema already checks the generic layer.

```yaml
spec:
  schemas:
    expectations.details: {ref: schemas/booking-truth.schema.yaml}
    inputs.tool_fixtures.by_tool.get_fares: {ref: my-library:tools/get_fares.fixture.schema.yaml}
    inputs.tool_fixtures.by_tool.*.routes:            # inline schema
      type: array
  items: [...]
```

Rules:

- A tool key checks the payload that tool receives: `by_tool[tool]`, else `by_tool["*"]`. This is the same rule as `tool_fixture()`.
- Items that do not provide a part are skipped. Parts without a schema are not checked.
- Violations, unknown keys, and missing schema files fail `Dataset.from_yaml` and `mas-lab validate`.
- The loader checks `spec.source` rows when it materializes them.
- A tool can ship its fixture schema next to its code. Datasets reference it by catalog path.

---

## Full example

```yaml
# labs/trip-planner-eval/datasets/trip_queries.yaml
apiVersion: lab/v1
kind: Dataset
metadata:
  name: trip-planner-queries
  version: "1.0"
  description: Trip planner items with prompt strings, optional files, and ground truth.
spec:
  items:

    - id: "cold-start-001"
      category: cold-start
      inputs:
        user: What are the cheapest flights from Paris to London this week?
      expectations:
        ground_truth: economy

    - id: "warm-alice-001"
      category: personalised
      inputs:
        user: Book my usual route.
        memory_seeds:
          - source: "user_preferences"
            content: "Alice always travels Paris→London, prefers 08:00 departure."
            target_agent: "concierge_agent"
          - source: "loyalty_status"
            content: "Alice: Gold tier, eligible for lounge access."

    - id: "warm-bob-001"
      category: personalised
      inputs:
        user: Book my usual route.
        memory_seeds: {ref: seeds/user_bob.yaml}

    - id: "follow-up-001"
      category: sequential-user
      inputs:
        user:
          - I need to go to Tokyo next month.
          - Make it business class.
          - What is the total cost?

    - id: "hitl-approval-001"
      category: hitl
      inputs:
        user: Book a flight to Singapore for the team offsite.
        hitl:
          - "Operator: budget exception approved — up to €800 per person."
        memory_seeds: {ref: seeds/team-offsite.yaml}
        session_id: "offsite-2026-q3"

    - id: "session-recall-001"
      category: session-memory
      inputs:
        user: What did we discuss last time?
        session_id: "test-session-recall-fixed"
```

Companion experiment YAML:

```yaml
# labs/trip-planner-eval/experiment.yaml
experiment:
  name: trip-planner-eval
  application:
    app: trip-planner                # resolves mas.yaml via the app catalog
    configs_dir: ./overlays
  dataset:
    path: ./datasets/trip_queries.yaml
  scenarios:
    - id: baseline
      overlays: {logic: [baseline], control: [], infra: []}
    - id: with-memory
      overlays: {logic: [with-memory], control: [], infra: []}
  run:
    n_runs: 3
```

Put shared background knowledge (schedules, fares) in a seed file that each item references, or in agent `spec.memory_seed`. See [section 7](#7-experiment-level-seeds-and-merge-order).

`spec.path` (without `items` or `source`) is a sidecar of already-shaped items: YAML, JSONL, or CSV next to the Dataset YAML.
Prefer `spec.source` when the file is a third-party table that still needs a `map`.
JSON Dataset files are not supported. Inline the items in `spec.items` instead.
The 250-item trip-planner benchmark lives in `benchmark.yaml`.

---

## Advanced fields

### 10. App-specific and general-purpose datasets

An app-specific dataset lives under the app version it evaluates.
The catalog id and `spec.app` use that same version tag:

```yaml
metadata:
  name: my-app-scenarios
  version: v1
spec:
  app: my-app@^v1    # or my-app@v1
  items: [...]
```

| Kind | On disk | Catalog id |
|------|---------|------------|
| **App-specific** | `apps/<app>/v<N>/datasets/<dataset-name>/dataset.yaml` | `<dataset-name>@vN` |
| **Unversioned app** | `apps/<app>/datasets/<dataset-name>/dataset.yaml` | `<dataset-name>` or `@v1` in `library.yaml` |
| **General-purpose** | `datasets/<name>.yaml` or `datasets/<domain>/...` | file or metadata name |

A bare `spec.app: <app>` means any version of that app.
Omit `spec.app` for generic datasets.

`library-samples` ships one complete trip-planner Dataset (`trip-planner-benchmark`).
Cap rows in the experiment with `dataset.limit`, not with a reduced sidecar file.

### 11. `spec.source`: meta-datasets (MMLU-Pro)

A **meta-dataset** is a Dataset document whose `spec.source` tells mas-lab where a third-party corpus lives.
It also says how each row becomes one envelope item.
The YAML is the mapping.
The corpus stays where it is: the HuggingFace hub, a jsonl export, or a pickle from another bench.
`Dataset.from_yaml` materializes the rows into envelope items.

After load, every item has the same shape as a hand-written one: `id`, `inputs.user`, `expectations.*`.
Experiments point at the Dataset catalog id. Apps do not know the rows came from MMLU-Pro.

Worked copies:

- HuggingFace MMLU-Pro (no rows in git): [`docs/schemas/examples/datasets/mmlu-pro.yaml`](../schemas/examples/datasets/mmlu-pro.yaml)
- Offline jsonl slice (same `map`, tiny fixture): [`docs/schemas/examples/datasets/mmlu-pro-jsonl.yaml`](../schemas/examples/datasets/mmlu-pro-jsonl.yaml)

#### 11.1 Fields

| Field | Required | Meaning |
|---|---|---|
| `kind` | yes | `huggingface`, `jsonl`, `csv`, `glob`, or `pickle`. |
| `id` | huggingface | Hub id, for example `TIGER-Lab/MMLU-Pro`. |
| `split` | no | HuggingFace split (default `validation`). Slice syntax passes through (`validation[:8]`). |
| `config` / `name` | no | HuggingFace subset name. |
| `path` | jsonl, csv, pickle, glob | File or glob, relative to the Dataset folder. |
| `map` | no | Column to envelope path. If omitted, `question`/`prompt`/`text`/`user` map to `inputs.user`, and `answer_index`/`answer`/`ground_truth`/`label` map to `expectations.ground_truth`. |
| `limit` | no | Keep only the first N rows (CI, smoke tests). |

You may omit `spec.items` when `source` is set.
If both are present, sourced rows load first and inline items are appended as local extras.

Declare `source` on the Dataset manifest. A catalog id then wraps the map.
`experiment.dataset.source` has the same shape. The loader merges it over the Dataset's `spec.source`.
Use it to override `split`, `limit`, or `map` for one experiment without forking the Dataset file.

#### 11.2 `map`

Each key is a dotted envelope path. Each value is one of:

- a **column name** (`question_id`, `answer_index`);
- a **template string** with `{column}` placeholders;
- `{template: "..."}` or `{column: name}` (the object form of the same two).

The loader synthesizes one field that is not in the raw row:

| Name | From |
|---|---|
| `options_text` | The `options` list formatted as `A. ...`, `B. ...`. |

MMLU-Pro columns used here: `question_id`, `question`, `options` (list of strings), and `answer_index` (int).
The hub schema may also expose `answer` (letter) and `category`. Map those if you need them as `group` or `category` metadata.

```yaml
map:
  id: question_id
  inputs.user: "{question}\n{options_text}"
  expectations.ground_truth: answer_index
  category: category
```

#### 11.3 Examples

**MMLU-Pro from HuggingFace** (needs the `datasets` package at load time):

```yaml
apiVersion: lab/v1
kind: Dataset
metadata:
  name: mmlu-pro
  version: v1
  description: Meta-dataset that maps TIGER-Lab/MMLU-Pro; it does not vendor the rows.
spec:
  source:
    kind: huggingface
    id: TIGER-Lab/MMLU-Pro
    split: validation
    limit: 50
    map:
      id: question_id
      inputs.user: "{question}\n{options_text}"
      expectations.ground_truth: answer_index
```

Experiment:

```yaml
dataset:
  name: mmlu-pro@v1
# cartesian: one MAS overlay x N questions. Do not couple 12k ids.
```

**Same map, local jsonl** (no hub, no `datasets` extra):

```yaml
spec:
  source:
    kind: jsonl
    path: mmlu-pro-slice.jsonl
    map:
      id: question_id
      inputs.user: "{question}\n{options_text}"
      expectations.ground_truth: answer_index
```

**CSV:**

```yaml
spec:
  source:
    kind: csv
    path: questions.csv
    map:
      id: id
      inputs.user: question
      expectations.ground_truth: answer
```

**Glob of YAML or JSONL files:**

```yaml
spec:
  source:
    kind: glob
    path: shards/*.jsonl
    map:
      id: question_id
      inputs.user: question
      expectations.ground_truth: answer_index
```

**Pickle** (a list of dicts, or of objects with `__dict__`).
Loading a pickle runs Python code from the file. Use only pickles you produced or trust:

```yaml
spec:
  source:
    kind: pickle
    path: mmlu_pro_val.pkl
    map:
      id: question_id
      inputs.user: "{question}\n{options_text}"
      expectations.ground_truth: gt_answer_index
```

#### 11.4 What this is not

- It is not a copy of MMLU-Pro into `spec.items`.
- It is not a way to split information across agents. A benchmark that gives each agent a private shard of the facts needs its own dataset. MMLU-Pro rows have no per-agent split to map from.
- The repo does not cache the HuggingFace load as YAML. Offline CI should use `kind: jsonl` plus a tiny checked-in slice.

---

## Common mistakes

- **A path in `inputs.user`.** A string on `user` is the prompt text. Use `{ref: path}` to load a file.
- **A `{role, content}` list or a `turns` list.** Use a string or a list of strings.
- **App-specific keys directly under `expectations`.** Put them under `expectations.details`.
- **An inline payload directly under `tool_fixtures`.** Wrap it in `by_tool` (use `"*"` for every tool).
- **Ground truth inside a fixture file.** Keep it on the item, in `expectations`.
- **Paths relative to the experiment.** Paths in a dataset resolve from the dataset file.
- **A reduced copy of a dataset for smoke runs.** Use `dataset.limit`.
- **Coupling thousands of item ids.** Use a cartesian design for large corpora.
- **An unquoted HITL reply that starts with `Operator:`.** YAML reads it as a mapping. Quote it.
- **`memory_seeds` in `experiment.yaml`.** The experiment schema rejects it. Seed from items or from the agent.
- **Expecting `inputs.session_id` to pin the session.** The native runner ignores it today.

---

## See also

- [dataset-migration.md](dataset-migration.md): prompt to `inputs.user` string
- [overlay.md](overlay.md): scenario-level memory seeds and params
- [Tutorial 3](../tutorials/03-experiments-and-analysis/README.md): hands-on benchmarks
