<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Extensions lab (paper §5.3)

This lab compares memory **overlays** on the trip-planner **MAS**. The overlays are vector retrieval, Letta injection, a tools-only baseline, and a guardrail. Read this page to run the paper §5.3 experiment.

Smallest working command, run from the repo root:

```bash
mas-lab benchmark run labs/extensions.lab/experiment.yaml --progress
```

You should see a progress bar. Results and figures then appear under `$XDG_DATA_HOME/mas/labs/memory-extension-reproducibility/results/`.

Terms: [glossary.md](../../docs/glossary.md).

## Claim

Context sources are typed contracts. Adding memory is an **overlay** change, not application code.

## Dependencies

Vector-memory scenarios use the in-tree `SemanticMemoryPlugin`. They need no extra install.

The Letta scenarios (`with-letta-memory`, `with-letta-factrecall`) need the upstream [`letta`](https://pypi.org/project/letta/) package, declared in [`requirements.txt`](requirements.txt). It is not a `mas-lab` extra. Letta pins `opentelemetry-sdk==1.30.0`, which conflicts with `google-adk` used by `mas-library-skills[all]`. So Letta gets its own venv:

```bash
# from the repo root
task install-lab LAB=labs/extensions.lab
```

## Run

```bash
mas-lab benchmark run labs/extensions.lab/experiment.yaml --progress

# Letta scenarios (dedicated venv, see Dependencies)
labs/extensions.lab/.venv/bin/mas-lab benchmark run labs/extensions.lab/experiment.yaml --progress
```

The command runs all **scenarios** × **dataset** items × `n_runs`. Then it runs the **level hooks**: `run.post` and experiment `post:` steps that compute metrics, confidence intervals, and figures. Re-run to refresh figures from the **trace cache**.

Two further experiments in this folder use the same overlays:

- `experiment-fact-recall.yaml`: a 100-fact profile, `n_runs: 3`.
- `experiment-fact-recall-smoke.yaml`: a 10-item smoke variant, `n_runs: 1`.

Both compare active vector retrieval with Letta passive injection. Both need the dedicated venv above.

```bash
mas-lab benchmark run labs/extensions.lab/experiment.yaml --dry-run
```

## Output

`experiment.name`: `memory-extension-reproducibility`

`$XDG_DATA_HOME/mas/labs/memory-extension-reproducibility/results/`

```bash
mas-lab benchmark show last plots
```

## Layout

| Path | Role |
|------|------|
| `experiment.yaml` | **Scenarios**, **dataset**, level hooks (paper §5.3 benchmark) |
| `experiment-fact-recall*.yaml` | Fact-level provenance variants (full and smoke) |
| `datasets/` | Lab-local datasets |
| `overlays/` | **Overlay** manifests |
| `extensions_lab/` | Lab-local library: custom **pipeline steps** and Letta helpers |
| `RESULTS.md` | Dated notes on an earlier run |

## See also

- [labs-quickstart.md](../../lab/docs/labs-quickstart.md)
- [paper/index.md](../../docs/paper/index.md)
- [cli/observability.md](../../docs/cli/observability.md)
