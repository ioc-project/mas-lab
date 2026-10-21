<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Labs, libraries, and local plugins

> The `example-library` names in the examples below are synthetic documentation
> fixtures. MAS Lab does not ship an `example-library`.

This page explains the difference between a lab and a library, and when to
create each. Read it before you add a folder with experiments or reusable code.

- A **lab** is a folder of experiments (scenarios, overlays, datasets, pipelines).
- A **library** is a folder of reusable code and YAML, with a `library.yaml`.

Do not turn a lab into a library. Do not create a library when you only need an
experiment.

**Datasets.** A dataset is the set of input items an experiment runs over.
`library-samples` publishes the full dataset pack for the reference app
(`trip-planner-benchmark`). CI and tests pin that catalog (`locator: samples`)
and cap rows with `dataset.limit`. They must not use `path:` into `labs/`,
because paper labs are not shipped with the packages. Experiment-specific
datasets (QA reasoning, MAS necessity, lifecycle queries, extensions) live in
that lab's `datasets/` folder. Only that lab references them.

Developer contract for finding libraries: [library-discovery.md](library-discovery.md).

---

## Lab

A lab is a `*.lab/` folder with `lab-config.yaml`. It stays flat and holds
scenarios, overlays, datasets, and pipelines. It refers to other files with
`name:path` references, for example `samples:tools/calc.tool.yaml`.

Paper labs are under [`labs/`](https://github.com/outshift-open/mas-lab/blob/main/labs/README.md).
Create a lab to compose an experiment. Do not create one to publish reusable tools.

## Library

A library is a folder with `library.yaml`. It holds apps, tools, overlays,
plugins, and Python code.

In a `name:path` reference, `name` is always a **library name**: `samples`,
`standard`, `lab`, `eval`, `skills`, `ioa`, `kg`, `telemetry`, or a lab-local
name such as `lib`. It is never a filesystem path. An unknown name fails.

The repository ships eight libraries. Each folder has a scheme (the name you
write in YAML):

| Folder | Scheme |
| --- | --- |
| `library-samples/` | `samples` |
| `library-standard/` | `standard` |
| `library-lab/` | `lab` |
| `library-eval/` | `eval` |
| `library-skills/` | `skills` |
| `library-ioa/` | `ioa` |
| `library-kg/` | `kg` |
| `library-telemetry/` | `telemetry` |

The scheme is not the Python package name (`mas-library-samples`). An installed
library registers its scheme on the `mas.runtime.manifest_libraries` entry point.
A library found on a search path uses its directory name. `library.yaml` may
list extra names under `schemes:`, but use one name per library (see
[library-discovery.md](library-discovery.md)).

A lab pins a MAS with `library:app`, for example `example-library:my-app@v2`.
`name@version` selects a version; a bare name means `@latest`. A slash is a
path (`example-library:apps/my-app/v2`), not an alias for an id.

How to write those refs: [writing-manifests.md](manifests/writing-manifests.md).

A library **may live inside a lab**. That does not make the lab itself a
library — do not put `library.yaml` on the lab root.

## Local plugins

A **plugin** is a runtime or pipeline extension registered by a library. Put
plugin Python and YAML inside a library, lab-local or shipped. There is no third
"plugins folder" beside labs and libraries.

| What you are adding | Where it lives |
| --- | --- |
| One-off bench/pipeline step for this experiment | That lab’s local library (catalogued in `library.yaml`) |
| Agent/runtime plugin (design pattern, memory, tool provider, …) | Declared in that library’s `library.yaml` so discovery finds it |
| Experiment pack (scenarios, overlays, lab-owned datasets) | The lab, not a library |

A pipeline step is a plugin of type `step`. The lab runs the experiment. The
library registers the step.

**Enable without editing agent YAML.** The `plugins:` key of `config.yaml` and
`lab.enable_plugins` turn on **runtime** plugins (gdb, checkpoint stores). Every
catalog entry is commented out in the init template. Uncomment one to enable it.
`spec.observability` and `spec.governance` stay on the agent.

---

## When to use which

1. **A lab alone** is enough when you only compose existing libraries:
   experiments, overlays, datasets, and refs like `samples:tools/calc.tool.yaml`.
   You write no reusable code.
2. **A local library inside the lab** fits when this lab needs reusable
   manifests, tools, plugins, or code that do not yet justify a package. Put
   them in a subfolder with `library.yaml`, list it in `lab.libraries`, and
   refer to it as `name:path`.
3. **A shipped or workspace library** fits when more than one lab, or `mas-ctl`
   and the runtime, should reuse the code. Make it an installable package or add
   its path to `manifest_libraries:` in `config.yaml`.
4. **A local plugin** lives inside a library (option 2 or 3). A bench step for
   one experiment stays in the lab's local library. An agent or runtime plugin
   is declared in that library's `library.yaml`.

---

## Lab-local library (in-repo pattern)

`labs/lifecycle-control.lab` and `labs/extensions.lab` keep pipeline code in a
package named after the lab. The lab lists that directory:

```yaml
# lab-config.yaml
lab:
  name: lifecycle-control
  libraries:
    - lifecycle_control_lab/
```

```yaml
# lifecycle_control_lab/library.yaml
apiVersion: mas/v1
kind: Library

name: lifecycle-control-lib
description: Lab-local pipeline figure steps for this lab.
version: "0.1.0"
```

The library name in refs is the listed basename: `lifecycle_control_lab/`
becomes `lifecycle_control_lab`. Direct children of the lab root that contain
`library.yaml` are picked up too.

The directory is also the Python package (`lifecycle_control_lab.steps.…`). All
libraries share one `sys.path`, so package names must be unique within a
process. Two labs that both ship a `lib/` package would shadow each other. The
plugin registry fails and prints both paths when it detects a clash.

A listed directory without `library.yaml` only joins the Python `sys.path`. Do
not leave an in-repo lab in that state.

## How to write `name:path`

```yaml
# Overlay / experiment / agent — prefix is a library name
tools:
  - samples:tools/calc.tool.yaml

application:
  app: example-library:my-app@v2
  # or: manifest: samples:apps/trip-planner/mas.yaml

# Workspace config.yaml — search folders (library root or parent of libraries)
manifest_libraries:
  - .
  - ./libraries
```

Lab-local names win over the same name in workspace config or an installed
library. The first name found wins.

`lab.libraries`, workspace `manifest_libraries`, and the `MAS_LIBRARY_PATHS`
environment variable feed the same scan. Installing a package (entry point) is
the other way in. `library.yaml` fills the catalog. Do not declare alias names
for a library.

---

## In the UI

The studio lists **scopes**, not folders. A lab with a local library (`lab.libraries`)
shows the lab's experiments under `lab:<name>` and the local library's applications
under `library:<that-library>` nested in the lab picker. A lab with only
`lab-config.yaml` is still a valid scope — it has no applications of its own.
A lab root is never reported as a library.

---

## See also

- [How to write manifests](manifests/writing-manifests.md) — inline vs file vs `LIBRARY:` id
- [User configuration](user-config.md) — workspace `config.yaml` and path vars
- [Lab manifests](manifests/lab.md) — `lab-config.yaml` fields
- [Tutorial 1](tutorials/01-building-an-agent/README.md) — overlay `name:path` refs
- [Package map](libraries.md) — installable wheels
- [Library discovery](library-discovery.md) — search order and `library.yaml` contract

Next: [Library discovery](library-discovery.md) for the search order, or the
[package map](libraries.md) for installable packages.
