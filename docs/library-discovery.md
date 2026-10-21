<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Library discovery (developers)

> Examples use a synthetic `example-library` fixture to demonstrate discovery
> syntax. It is not an installed MAS Lab package.

This page is the contract that `mas-ctl`, the runtime, and `mas-lab` share to
find libraries. For the user-level model, read
[labs-and-libraries.md](labs-and-libraries.md) first.

Shared helpers live in `runtime/src/mas/library_roots.py`:

- `iter_named_library_roots(*anchors)` — ordered `(name, root)` pairs
- `resolve_named_library_root(name, *anchors)` — root or `None`

A **manifest library** is a folder with `library.yaml` at its root.

- `name:path` always starts with a library name.
- An unknown name raises `LookupError`. It is never read as a filesystem path.
- The lab validator rejects unknown library names.
- Do not put `library.yaml` on the lab root.
- `mas-ctl` must not depend on `mas-library-samples`.

---

## How a library becomes visible

A library becomes visible in two ways. Either way, MAS Lab finds a folder with
`library.yaml`, and that file fills the catalog (apps, datasets, tools).

1. **Install.** `uv pip install -e example-library` registers the scheme
   `example-library` on the `mas.runtime.manifest_libraries` entry point.
2. **Search paths.** A list of folders to scan. Each entry is a library root or
   the parent of sibling library folders. The library name is the directory
   stem. Do not invent aliases.

Three search lists exist. They use the same scan (`iter_libraries_in_search_path`):

| Where | Field |
| --- | --- |
| Lab | `lab-config.yaml` → `lab.libraries` |
| Workspace | `config.yaml` → `manifest_libraries` |
| Environment | `MAS_LIBRARY_PATHS` (`os.pathsep`) |

```yaml
lab:
  libraries:
    - my_lab/
    - ../../../example-library

manifest_libraries:
  - .                     # this workspace: example-library/, library-kg/, …
  - ../other-libraries
```

A listed directory without `library.yaml` still joins the Python `sys.path`
(lab-local code). Direct children of the lab root that contain `library.yaml`
are picked up too.

## Search order

The first name found wins. A lab-local library beats a workspace or installed
library of the same name. MAS Lab searches in this order:

1. Lab-local search paths (`lab.libraries` + lab-root children)
2. Workspace search paths (`manifest_libraries`)
3. Installed libraries (entry points)
4. `MAS_LIBRARY_PATHS`
5. An upward walk from the anchors or current directory, looking for
   `library.yaml`. It stops at `.git` and `.lab`. A `library.yaml` on the lab
   root is not registered twice.

`.git` ends the walk. It is not a search root.

---

## `library.yaml` contract

Schema: [`docs/schemas/library.schema.yaml`](schemas/library.schema.yaml).
Kind `Library`, `apiVersion: mas/v1`, flat (no `metadata:` / `spec:`).

| Field | Role |
| --- | --- |
| `name` | Package-style identity (`mas-library-samples`, `lifecycle-control-lib`) |
| `schemes` | Optional extra identifiers. Prefer one name: the directory stem and the install entry point. Do not add aliases. |
| `apps` / `datasets` / `tools` | Catalogs for `library:app`, `name:path`, and `app:` lookup. App keys are `name` or `name@version`; a value may be a family dir (`apps/my-app`) or one version dir. |
| `types` / `plugins` | Plugin manifest payload (same shape as `*.plugins.yaml`) |
| `plugin_manifests` | Extra plugin YAML files, if you split them |

When `library.yaml` declares `types:` or `plugins:`, it is also the plugin
manifest. See [plugin-registry-manifests.md](https://github.com/outshift-open/mas-lab/blob/main/runtime/docs/plugin-registry-manifests.md).

In-repo labs that list a local directory ship a `library.yaml` there, so the
directory is a real library. Pipeline YAML may still load steps by Python path
(`lifecycle_control_lab.steps.figure_call_counts:FigureCallCountsStep`). The
manifest catalogs them so discovery and `name:path` see the library.

---

## Scheme vs package name

The prefix in `samples:tools/calc.tool.yaml` is the **library name**
(`samples`), not the package name (`mas-library-samples`). Installed names in
this repository: `samples`, `standard`, `lab`, `eval`, `skills`, `ioa`, `kg`,
`telemetry`.

The basename of a lab-local directory (`lifecycle_control_lab/`) is its scheme.
It is also a Python package name, so it must not collide with another loaded
library's package (see [labs-and-libraries.md](labs-and-libraries.md)).

---

## Versioned catalog ids

Catalog ids use the same separator as plugins (`react@v1`): `[library:]name[@version]`.

- On disk: `apps/<name>/v<N>/` — the folder name is the version tag.
- Bare `name` is `@latest` (highest `v*` folder) for lookup
  (`get_app`, `mas-ctl check`, and experiment `app:` / `dataset.name`).
  Pin `@version` when you need a specific major.
- After `LIBRARY:`, no slash means an id: `example-library:my-app@v2`.
- A slash after `name@version` is a path inside that catalog object:
  `example-library:my-app-cases@v2/tool_fixtures/case-01.yaml`.
- A slash after a folder is a path from the library root:
  `example-library:apps/my-app/v1/datasets/scenarios/tool_fixtures/case-01.yaml`.
- `my-app-v2` and `my-app/v2` are not id aliases.

**Datasets** use the same `name@version` grammar. An app-specific dataset lives
at `apps/<app>/v<N>/datasets/<dataset>/dataset.yaml`, in the app's version
folder, and declares `spec.app` (`my-app@^v1`). Generic datasets live under the
library-root `datasets/`.

Rules for writing these references: [writing-manifests.md](manifests/writing-manifests.md).

---

## See also

- [How to write manifests](manifests/writing-manifests.md)
- [Labs vs libraries](labs-and-libraries.md)
- [User configuration](user-config.md)
- [lab-config schema](schemas/lab/lab-config.schema.yaml)
- [workspace config schema](schemas/config.schema.yaml)

Next: [How to write manifests](manifests/writing-manifests.md).
