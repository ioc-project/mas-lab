<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Tutorial 15 — World events and fixtures

> **Packages:** `lab/components/bench`, `library-lab`, `library-standard`
> **Time:** about 20 minutes
> **Prerequisite:** [Tutorial 3](../03-experiments-and-analysis/README.md).
> Run every command from the repository root.

## Goal

An experiment can start loopback fixtures, fire a world action after a tool
result, and record that work in `traces/environment.jsonl`. The demo UI
draws those events as a timeline lane.

The offline example is `labs/revocation.lab/`. After the second `pay_fare`
tool result it POSTs `/admin/revoke` at a fake issuer. The wait cell then
advances a virtual clock 21 s so a payments cache refreshes to denied.

Fixtures and the virtual clock live on the experiment. Per-case triggers
live on the dataset items. That is the same split as memory seeds (per
item) versus the agent (every run). It does not replace
`inputs.tool_fixtures` (mock payloads a tool reads) or pipeline
`service_start`.

## Files

| File | Purpose |
| --- | --- |
| `labs/revocation.lab/experiment.yaml` | `environment.fixtures` and virtual clock |
| `labs/revocation.lab/dataset.yaml` | two items, quoted `"on": tool_result` |
| `labs/revocation.lab/fixtures/` | issuer, payments, scripted model (loopback) |
| `demo-ui/layouts/examples/run-view-environment.yaml` | timeline panel |

## Step 1 — Validate the example

```bash
mas-lab validate labs/revocation.lab/experiment.yaml
```

```text
OK
```

YAML 1.1 treats an unquoted `on:` key as boolean `True`; the dataset quotes
it: `{"on": tool_result, ...}`. The experiment writes
`scenarios[].overlays` with empty `logic` / `control` / `infra` lists —
omitting `overlays` would infer a file named `issuer-backed`.

`experiment.yaml` holds `environment.fixtures` and `clock.kind: virtual`.
`dataset.yaml` holds, per item, `environment.triggers` (revoke after the
second `pay_fare` result; the wait item also advances the clock). The
item cannot declare fixtures. Field reference:
[environment.md](../../manifests/environment.md),
[dataset.md §10](../../manifests/dataset.md#10-world-around-the-item-environment).

Then run it (loopback fixtures, `models.main: scripted`, no live model key):

```bash
mas-lab benchmark run labs/revocation.lab/experiment.yaml --force
```

`mas-lab benchmark run` hands the run to the shared controller daemon and starts it if it is not
running. The daemon stays up for the next run; stop it with `mas-lab control stop`. Fixtures
belong to the run, not to the daemon: they are stopped when the run ends, and a fixture left
by a killed runner is reaped by the next run.

`traces/environment.jsonl` for `revoked_mid` contains, in order:

```text
trigger.matched  id=revoke-after-2  anchor.occurrence=2
action.started   step=http_call
action.done      step=http_call
```

## Step 2 — What the cells do

| Item | Third `pay_fare` |
| --- | --- |
| `revoked_mid` | still allowed (payments cache) |
| `revoked_mid_wait` | `clock_advance` 21s on that tool call, then denied |

`traces/environment.jsonl` holds `trigger.matched` with
`anchor.occurrence == 2` immediately followed by `action.done` for
`http_call`.

## Step 3 — Timeline

Open the demo UI with `task demo-ui` and the layout
`demo-ui/layouts/examples/run-view-environment.yaml`. The environment
panel reads `custom.environment`: fixture bars, trigger diamonds
(`id #occurrence`), action spans.

## Step 4 — Stop a slow model

`kill_switch` cancels a model call that is in flight. At a tool result nothing is in flight, so
fire it from a wall-clock timer while the model is still deciding. `scripted_model.py` takes
`--delay-s N` to hold every completion:

```yaml
environment:
  clock: {kind: wall}
  timers:
    - id: kill
      at: 1s
      do: [{type: kill_switch, config: {reason: stop-now}}]
```

The turn ends on the cancel and `action.done` carries `cancelled: 1`. Persist needs a
checkpoint store; without one it pauses and `persisted` is 0. With nothing in flight it
carries `cancelled: 0` and the turn continues.

## What you learned

- `environment:` is the world around a run, not the kernel.
- The experiment starts fixtures; a dataset item overlays the triggers
  for that case. Complementary to memory seeds and tool fixtures, not a
  replacement.
- Triggers match at the governance synchronous point; world actions run
  while the kernel waits, then `environment@v1` ALLOWs.
- Environment events live in `environment.jsonl`, not `events.jsonl`.

## Going further

[Environment manifest](../../manifests/environment.md),
[Tutorial 13](../13-control-and-debug/README.md) for gdb `env` / `emit`.
