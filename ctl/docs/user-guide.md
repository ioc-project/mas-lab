<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-ctl User Guide

`mas-ctl` runs agents and multi-agent systems (MAS) from YAML manifests. This guide is for teams that run a MAS, switch environments with flavours, and apply overlays.

## Install

From a checkout of the repository (the PyPI upload is still pending):

```bash
uv sync --package mas-ctl     # or: uv sync --all-packages
```

`mas-ctl` is then available through `uv run mas-ctl ...` (or activate the virtual environment).

## Quickstart

Run from a MAS manifest:

```bash
mas-ctl run-mas mas.yaml --flavour local
```

Run with an explicit prompt and an overlay (a partial manifest merged on top of the base):

```bash
mas-ctl run-mas mas.yaml --overlay overlays/baseline.yaml --prompt "Plan a 3-day trip to Paris" --flavour local
```

Add `--override PATH=VALUE` (repeatable) for one-off, schema-validated changes applied last.

## Core concepts

- MAS manifest: declares agents, topology/workflow, metadata
- Deployment: placement strategy and runtime binding (`local-inproc` is the only OSS-supported strategy)
- Overlay: a partial manifest merged over the base (`--overlay`, `--override`). In `mas-lab`, a *scenario* is the experiment-level construct that selects overlays per run (hierarchy: Experiment, Scenario, Item, Run).
- Flavour: a named environment profile shipped by a library (list them with `mas-ctl flavour list`). Only `local` is available today.
- Policy: guardrails and runtime limits declared in the manifest

### Placement (OSS)

Only `local-inproc` placement is supported in mas-lab OSS. Strategies
`local-multiprocess`, `docker`, and `kubernetes` are planned for a later release.
`compose` and `plan` fail with a clear error if an unsupported strategy is used.

## Common tasks

### Validate effective topology

- Dry-run composition and placement: `mas-ctl plan mas.yaml`.
- Print the composed manifest: `mas-ctl compose mas.yaml` (write it with `--output`).
- Check manifests against their schemas: `mas-ctl validate mas.yaml`.

### Switch environments

- Keep the same MAS manifest; switch only `--flavour` (or `--infra-ref` for an infra bundle).

### Audit secret usage

- Secrets are never stored in manifests. Verify that every `*_env` reference names an environment variable that is set at runtime.

## CLI map

Run `mas-ctl --help` and `mas-ctl <command> --help` for all options.

- `chat`, `tui` — interactive conversation with an agent or MAS manifest
- `run-mas` — execute a MAS workflow (`--prompt`/`--query`, `--interactive`)
- `compile`, `compose`, `plan` — manifest compilation, composition and placement dry-run
- `validate`, `schemas` — YAML validation and schema listing
- `flavour list` — flavours shipped by installed libraries
- `infra list` (alias `list-bundles`) — infra bundles
- `registry list`, `registry query` — plugin registry introspection
- `plugin list`, `plugin enable`, `plugin doctor` — plugin inspection and checks
- `serve` — expose an agent through the endpoint plugin its infra selects
- `checkpoint` (`list`, `show`, `fork`, `resume`) — session checkpoints
- `control` (`attach`, `inspect`, `pause`, `resume`, `steer`, `snapshot`, ...) — drive a running session

Benchmarking: **`mas-lab benchmark run`** (see [Tutorial 3](../../docs/tutorials/03-experiments-and-analysis/README.md)).

## Troubleshooting

- Overlay not applied: check the `--overlay` path and the `--override` session-tree path.
- Agent startup failure: validate referenced agent manifests.
- Policy mismatch: inspect effective config output before run.
