<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Lab manifests (`mas-lab-bench`)

A **lab** is a `*.lab/` folder that holds experiment YAML files, an optional `lab-config.yaml`, **datasets** (the inputs a run uses), and **overlays** (small patches that define scenario variants).
Create one when you want a self-contained, runnable study.
A lab is the experiment surface, not a library.
Reusable code a lab needs lives in a subdirectory with its own `library.yaml`, listed under `lab.libraries` (see [labs-and-libraries.md](../labs-and-libraries.md)).

Smallest `lab-config.yaml`:

```yaml
lab:
  name: my-study
  description: "Compare two reviewer prompts"
```

Typical folder layout:

```text
my-study.lab/
  lab-config.yaml        # lab: name, libraries, plugins
  experiment.yaml        # experiment: (or <NN-name>/experiment.yaml per study)
  datasets/              # kind: Dataset files
  overlays/              # kind: Overlay files (scenario columns)
  my_study_lab/          # optional lab-local library (library.yaml + code)
```

Terms: [glossary.md](../glossary.md). Walkthrough: [Tutorial 3](../tutorials/03-experiments-and-analysis/README.md).

The schemas live in `docs/schemas/lab/`. The runtime resolves them through `mas.lab.schemas.paths.lab_schema_dir`.
Validate any of these files with `mas-lab validate <file>` (`mas.lab.manifests.validator`). The validator detects the kind from the top-level key.
`mas-lab benchmark run <experiment.yaml> --dry-run` also plans the run matrix.

---

## Manifest index

| Manifest | Top-level key | Schema file | Reference |
|----------|---------------|-------------|-----------|
| Experiment | `experiment:` | `experiment.schema.yaml` | [experiment.md](experiment.md) |
| Dataset | `kind: Dataset` | `dataset.schema.yaml` (items: `run-input.schema.yaml`) | [dataset.md](dataset.md) |
| Post-processing pipeline | `pipeline:` | `pipeline.schema.yaml` | [pipeline.md](pipeline.md#post-processing-pipeline-pipeline-root) |
| Pipeline library file | `kind: Pipeline` | `pipeline-manifest.schema.json` | [pipeline.md](pipeline.md#pipeline-library-kind-pipeline) |
| Lab metadata | `lab:` | `lab.schema.yaml` | [below](#lab-configyaml) |
| Interactive lab config | `lab:` | `lab-config.schema.yaml` | [below](#lab-configyaml) |

The controller API (`mas-lab serve`, `GET /api/schemas/{id}`) serves these schema ids: `experiment`, `dataset`, `pipeline-post` (`pipeline:` root), and `pipeline` (`kind: Pipeline`).

---

## `lab-config.yaml`

Two schemas share the `lab:` root.
`mas-lab validate` picks the interactive schema when `lab:` contains any of `mas`, `scenarios`, `ui`, `dataset`, or `evaluation`.
Otherwise it picks the lab-metadata schema.

### Lab metadata (`lab.schema.yaml`)

The shipped labs use this form:

```yaml
lab:
  name: lifecycle-control
  description: "Lifecycle control and governance stacking"
  libraries:
    - lifecycle_control_lab/      # dir with library.yaml -> scheme "lifecycle_control_lab"
  # enable_plugins:
  #   - mas.checkpoint_store.hybrid
```

| Field | Required | Meaning |
| --- | --- | --- |
| `name` | yes | Lab name. |
| `description` | no | Free text. |
| `libraries` | no | Library directories, relative to this file. A directory with `library.yaml` becomes a `name:path` library scheme (name = basename). A directory without it is only added to the Python import path. Lab-local libraries win over workspace and installed libraries of the same name. |
| `plugins` | no | Lab-local plugins: a module name, or `{id, path \| module}`. |
| `enable_plugins` | no | Plugin URNs this lab turns on from the workspace plugin catalog (the catalog behind `config.yaml` `plugins:`). Off unless listed. |

### Interactive lab config (`lab-config.schema.yaml`)

This schema adds the following keys:

- `version`.
- `applications`: same shape as `experiment.application`, as a one-item list. `lab.mas` is a deprecated alias.
- `scenarios`: `id`, `overlays`, `description`, `tags`, and the legacy `user_prompt`.
- `dataset`: `path`, `filter`, `source`.
- `model` / `models`, `evaluation`, `trace_cache_dir`.
- `ui`: `port`, `mode: interactive | automated`, `layout: dag | grid | default`, `node_positions`.
- The legacy `default_flavour` and `output_dir`.

Workspace defaults are described in [user-config.md](../user-config.md).

---

## Experiment lifecycle model

An experiment has four levels. Each level can hold `pre:` and `post:` **level hooks**: lists of pipeline steps that run before or after that level.

```text
experiment
├── pre/post      (experiment root; once per experiment)
├── scenario      pre/post  (per scenario column)
├── item          pre/post  (per scenario x dataset item)
└── run           pre/post  (per run index; declares n_runs)
```

Each `pre:` / `post:` entry is one of:

- a step,
- a pipeline file path,
- `{steps: [...]}`,
- `{ref: path}`,
- `{id: library-pipeline}`.

`application:` is the MAS binding, not a level.
`application.post` and `test:` are deprecated aliases of the experiment-root `post:` and `item:`.

---

## Relationship to runtime manifests

| Lab concept | Runtime manifest |
|-------------|------------------|
| `application.manifest` / `application.app` | `kind: MAS` (or `kind: Agent`) |
| `scenarios[].overlays` | `kind: Overlay` stack |
| `dataset` | `kind: Dataset` |
| Benchmark flavour / infra | `--flavour` / `--infra` CLI flags, or `default_flavour` / `default_infra`, which map to `Flavour` / workspace `infra_refs` |

## Bench-only infrastructure types

Post-run export codecs (ClickHouse, Neo4j, filesystem) use **`mas.lab.infra`** (`DatastoreSpec`, `resolve_datastore`).
They are not part of mas-runtime or mas-ctl.
See [`lab/components/bench/src/mas/lab/infra/README.md`](https://github.com/ioc-project/mas-lab/blob/main/lab/components/bench/src/mas/lab/infra/README.md).

---

## See also

- [experiment.md](experiment.md)
- [dataset.md](dataset.md) and [overlay.md](overlay.md)
- [labs-and-libraries.md](../labs-and-libraries.md)
- [Tutorial 3](../tutorials/03-experiments-and-analysis/README.md)
