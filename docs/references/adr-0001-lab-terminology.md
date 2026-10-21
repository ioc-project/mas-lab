<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# ADR 0001: "lab", "labs", and "components" terminology

**Status:** Accepted (terminology) · Rename of `mas.lab.components` **not done** (deferred, see below)
**Date:** 2026-06-29

An ADR (architecture decision record) states one decision, its context, and its consequences.

## Context

The words *lab* and *components* each mean several things in this repository. Newcomers find the tree hard to navigate.

| Name | What it is |
| --- | --- |
| `lab/` | The `mas-lab` **package** (CLI entry `mas-lab = mas.lab.cli:app`). It depends on the component wheels below and ships its own code under `mas.lab.*`. |
| `lab/components/{core,bench,controller}/` | Three separately built **wheels**: `mas-lab-core`, `mas-lab-bench`, `mas-lab-controller`. Each is a uv workspace member. |
| `labs/` | The three paper **lab definitions** (`design-space.lab`, `extensions.lab`, `lifecycle-control.lab`). They are data and config, not code. |
| `library-lab/` | `mas-library-lab`: a plugin library for lab extensions (the `mcev1` eval provider, UI plots, pipeline steps, design patterns). It depends on `mas-lab`. |
| `mas.lab.components` (`lab/src/mas/lab/components/`) | A **runtime module directory** inside the `mas-lab` package (`common`, `controller`, `evaluation`, `infrastructure`, `metrics`, `observability`, `package`, `ui`). It is unrelated to the build "components" above. |

So *components* means both "the published sub-wheels" and "a runtime module package". *Lab* spans a package, a directory of data, and a plugin library.

Nothing is functionally broken. Python namespaces keep `lab/components/*/src/mas/lab/...` (which installs into the `mas.lab` namespace) distinct from the `mas.lab.components` subpackage. The naming is still a readability hazard.

## Decision

1. **Use one vocabulary** in docs and code:
    - **package**: a published wheel (`mas-lab`, `mas-lab-core`, and so on).
    - **component**: one of the three sub-wheels under `lab/components/`.
    - **lab (definition)**: a `*.lab` folder under `labs/` that describes a paper experiment.
    - **runtime modules**: the code under `mas.lab.components` that the `mas-lab` package ships.
2. Document this vocabulary in the [glossary](../glossary.md) and the [packages reference](../packages-reference.md).
3. **Rename `mas.lab.components` to `mas.lab.runtime_modules`.** The rename removes the collision with the `lab/components/` wheels. It is **deferred** until the full test suite can validate it, because it touches *dynamic* references that static analysis cannot verify. As of v0.2.0 the directory is still `lab/src/mas/lab/components/`.

## Consequences: why the module rename is deferred

Ordinary `import` statements reference `mas.lab.components`. So do **dynamic string imports**, which fail only at runtime:

- `lab/src/mas/lab/components/controller/backends.py` (`_SERVICE` map): `"mas.lab.components.metrics.server:main"`, spawned via `python -m …`.
- `library-lab/src/mas/library/lab/steps/eval/annotate_metrics.py`: `_DEFAULT_METRIC_CLASS = "mas.lab.components.evaluation.deepeval_wrapper.AnswerRelevancyMetric"`.
- `lab/components/controller/src/mas/lab/controller/routes/health.py`: the same class path as a display-name map key.

A `py_compile` pass and most unit tests miss a stale string here. The metrics backend would fail only when the controller spawns it. The rename must land in a change set where `pytest` and a backend-spawn smoke test can confirm it.

## Rename recipe

1. `git mv lab/src/mas/lab/components lab/src/mas/lab/runtime_modules`.
2. Replace every dotted occurrence of `mas.lab.components` with `mas.lab.runtime_modules`, in imports **and** strings. Current list:
    - `lab/src/mas/lab/runtime_modules/observability/otlp_http_local.py`
    - `lab/src/mas/lab/runtime_modules/controller/backends.py`
    - `lab/src/mas/lab/runtime_modules/common/telemetry_feed.py`
    - `lab/src/mas/lab/runtime_modules/evaluation/interface.py` (docstring)
    - `library-lab/src/mas/library/lab/steps/eval/annotate_metrics.py`
    - `lab/components/controller/src/mas/lab/controller/routes/health.py`
3. Grep to confirm zero remaining `mas\.lab\.components` references, docs included.
4. Run the full suite **and** start the controller so the `metrics` backend spawns. This covers the dynamic imports.

## See also

- [Glossary](../glossary.md)
- [Packages reference](../packages-reference.md)
- [ADR 0002](adr-0002-observability-event-model.md): another decision in the same format.
