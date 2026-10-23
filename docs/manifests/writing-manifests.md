<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# How to write manifests

Every attribute in a MAS Lab YAML file is one of three things: an **inline object**, a **file path**, or a **catalog id**.
A `LIBRARY:` prefix (a library name before a colon) is valid on any path or id, not only on `app:`.
Read this page when one manifest points at another and you want to avoid paths such as `../../apps/foo/mas.yaml`.

Smallest example of all three forms:

```yaml
spec:
  agency:
    agents:
      - id: lead                        # inline: the object is the agent
        description: Orchestrator
      - id: backend                     # file: a path relative to this mas.yaml
        ref: agents/backend.yaml
      - id: shared                      # id-based: a library-qualified path
        ref: example-library:apps/my-app/v1/agents/shared.yaml
```

Field lists for each kind live in this folder ([README](README.md)). This page covers only how to write references.
In the UI, copy the `ref` field from an item (for example `part2:sre-triage@v2`)
and paste it into the experiment or overlay that should point at it. Versions
and ranges are resolved by `GET /api/resolve?ref=`.

---

## 1. Inline, file, or id

```yaml
# Inline: the object is the manifest (or a fragment of one)
spec:
  agency:
    agents:
      - id: lead
        description: Orchestrator
        design_pattern: { type: react }

# File: separate YAML, referenced by path
      - id: telemetry
        ref: agents/telemetry.yaml          # relative to this mas.yaml
      - id: backend
        ref: example-library:apps/my-app/v1/agents/backend.yaml

# Id: catalog name, optionally versioned and library-qualified
experiment:
  application:
    app: example-library:my-app@v1        # id, not a path
  dataset:
    name: my-app-scenarios@v1
    locator: example-library
```

A dataset item is the setup of one run: `inputs.user` (the prompt string) plus optional `hitl`, `memory_seeds`, and `tool_fixtures`.
File pointers use `{ref: path}`.
Ground truth is `expectations` on the same item.

| Form | When to use | Example |
| ------ | ------------- | --------- |
| **Inline** | Small fragments, one-off agent blocks, overlay `patch:` | `design_pattern: { type: cot }` |
| **File path** | A real document on disk | `./mas.yaml`, `samples:apps/trip-planner/mas.yaml` |
| **Catalog id** | A named app, dataset, or tool in a library | `example-library:my-app@v2`, `trip-planner` |

Do not encode the version in the name (`my-app-v2`) or with a slash (`my-app/v2`).
A slash means a filesystem path.
`@` is the version separator, as in plugins (`react@v1`).

App-specific datasets live under the app version they evaluate: `apps/<app>/v<N>/datasets/<name>/`.
They use catalog id `<dataset>@vN` and `spec.app: <app>@^vN`.
Generic datasets live under the library-root `datasets/` folder.

---

## 2. `LIBRARY:` prefix

A `name:path` reference starts with a **library scheme**: `samples`, `example-library`, `standard`, or a lab-local library name.
After the colon, write one of three things:

- A **catalog id**, with no slash: `example-library:my-app@v2`.
- A **path inside a catalog object**, written as id, then slash: `example-library:my-app-cases@v2/tool_fixtures/case-01.yaml`.
- A **library-relative path**, with a slash after a folder: `example-library:apps/my-app/v2/mas.yaml`.

A slash after `name@version` is a path under that app or Dataset folder. It is not a second copy of the file.
Do not invent a sibling tree or a symlink so overlays can `../` out of `mas.yaml`.

```yaml
# Prefer these
- app: example-library:my-app@v2
- manifest: samples:apps/trip-planner/mas.yaml
- ref: samples:tools/calc.tool.yaml
- tool_fixtures: {ref: example-library:my-app-cases@v2/tool_fixtures/case-01.yaml}

# Avoid climbing out of the lab
- manifest: ../../apps/my-app/mas.yaml      # don't
- ref: ../../../example-library/apps/my-app/v1/agents/lead.yaml
```

Unknown library names raise `LookupError`. They are never treated as relative paths.
Bare ids (`my-app@v2`, `trip-planner`) are looked up in the global catalog before the filesystem.

`pkg://` is a package-resource URI, for example `pkg://mas.library.samples/apps/...`.

---

## 3. Version grammar

Canonical id: **`[library:]name[@version]`**.

A spec may pin `@version`.
An unpinned `my-app` means `@latest`, the highest `v*` folder. The app catalog (`mas.apps.get_app`) applies the same rule to `app:` in experiments.
Pin when you need a specific major version.

| Written | Resolves to |
|---------|-------------|
| `my-app@v2` | Folder `apps/my-app/v2/` |
| `my-app` / `my-app@latest` | Highest `v*` folder |
| `coding-agent-md` | Unversioned family — the name **is** the pin |
| `example-library:apps/my-app/v2` | Path under the library (directory) |
| `example-library:my-app-cases@v2/tool_fixtures/foo.yaml` | File inside that Dataset folder |

On disk the version is the folder name: `apps/<name>/v<N>/`.
`library.yaml` may list the whole family (`my-app: apps/my-app`) or one version (`my-app@v2: apps/my-app/v2`).

---

## 4. File names (what discovery looks for)

Follow these conventions and do not invent another suffix.
`mas.yaml` is the app-root file.
`*.mas.yaml` is an extra MAS document, not a second spelling of the app .

| Kind | Conventional file | How it is found |
| ------ | ------------------- | ----------------- |
| Library | `library.yaml` | Folder with this file is a library root |
| Lab | `lab-config.yaml` | Folder `*.lab/` or a directory containing this file |
| Experiment | `experiment.yaml` | That name, or YAML under `experiments/` |
| MAS app root | **`mas.yaml`** (or `mas-bench.yaml`) | App directory scan; `get_app` / `app:` |
| Extra MAS document | `*.mas.yaml` | **Explicit** `library.yaml` `apps:` path or a `LIBRARY:…` file ref. The samples topologies use this (`parallel.mas.yaml`). Not scanned as an app . |
| Agent | `agents/<id>.yaml`, `agents/<id>/agent.yaml`, or `agent.yaml` | Referenced from MAS `agency.agents[].ref` |
| Original persona tree | `agents/<id>/AGENT.md` | Non-mas-lab layout (e.g. `coding-agent-md`). The directory is still catalogued. |
| Tool | `*.tool.yaml` | `tools/<name>.tool.yaml` or `tools/<name>/*.tool.yaml` |
| Overlay | `*.yaml` with `kind: Overlay` | Path / overlay id; often under `overlays/` |
| Dataset | `datasets/**/*.yaml` (`kind: Dataset`) or `apps/<app>/v*/datasets/<name>/dataset.yaml` | Catalog id or path. App-specific datasets declare `spec.app` for that app version. Generic datasets live under library-root `datasets/`. |
| Pipeline | `pipelines/*.yaml` (`kind: Pipeline` / `pipeline:`) | Path. A `LIBRARY:` ref may omit `pipelines/` and `.yaml`. |
| Plugin split-file | `plugins/*.plugins.yaml` | Optional. Most plugins live inline in `library.yaml`. |

Overlays do not need a `*.overlay.yaml` suffix.
Some labs use that name for readability.
Discovery keys off `kind: Overlay` and the referring path or overlay id.

---

## 5. Worked examples

### Experiment pin (preferred)

```yaml
# labs/example.lab/experiments/foo/experiment.yaml
experiment:
  name: foo
  application:
    app: example-library:my-app@v1
  dataset:
    name: my-app-scenarios@v1
    locator: example-library
  scenarios:
    - id: baseline
      overlays: {logic: [], control: [], infra: []}   # no overlays
```

### Experiment with a path (when you need one specific file)

```yaml
experiment:
  application:
    manifest: samples:apps/trip-planner/mas.yaml
    configs_dir: overlays
```

### MAS with mixed inline and files

```yaml
# apps/my-app/v2/mas.yaml
apiVersion: mas/v1
kind: MAS
metadata:
  name: my-app
spec:
  agency:
    agents:
      - id: lead
        ref: agents/lead.yaml          # file next to this manifest
      - id: risk_assessment
        ref: agents/risk_assessment.yaml
```

### Overlay file

```yaml
# overlays/no-tools.yaml
apiVersion: mas/v1
kind: Overlay
metadata:
  name: no-tools
spec:
  target:
    kind: Agent
    name: qa                 # optional when applied to a MAS
  patch:
    tools: { "$op": { clear: true } }
  overrides:                 # optional ordered path assignments
    - '.mas.agent[qa].spec.context.role="security reviewer"'
```

The [Overlay manifest reference](overlay.md) documents the full target, patch, selector, compatibility, and validation contract.
The CLI [`--override`](../cli/overrides.md) accepts the same session-tree assignments.

---

## See also

- [Manifest reference](README.md): one page per kind
- [Labs vs libraries](../labs-and-libraries.md)
- [Library discovery](../library-discovery.md)
- [Experiment](experiment.md) · [MAS](mas.md) · [Agent](agent.md) · [Overlay](overlay.md) · [Tool](tool.md)
