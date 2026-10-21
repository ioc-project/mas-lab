<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Command-line tools

MAS Lab ships two CLIs. `mas-ctl` runs agents and MAS manifests. `mas-lab` runs
experiments and telemetry, and sets up a workspace. Start with `mas-ctl chat`.

`mas-runtime` is a library and has no CLI.

| CLI | Package | Use for |
|-----|---------|---------|
| [`mas-ctl`](mas-ctl.md) | `ctl/` | Chat, TUI, serve, compile, compose, validate, `run-mas`, control |
| [`mas-lab`](mas-lab.md) | `lab/` + libraries | Experiments, telemetry, knowledge graphs, plots. See the [man-page index](mas-lab.md) |
| `mas-ctl --help` / `mas-ctl COMMAND --help` | — | Always-current flag list |

**mas-lab man pages** live with the owning package ([index](mas-lab.md)).
**Complete mas-ctl option tables:** [mas-ctl.md](mas-ctl.md). Controller HTTP:
[`mas-lab serve`](serve.md).
**Workspace file:** [config.yaml reference](../references/config.yaml.md).
**Machine run logs:** [observability.md](observability.md) (`events.jsonl`).

---

## What goes where

| Stream | Consumer | How you get it |
|--------|----------|----------------|
| **stdout** | Humans | Conversation (`You:` / `Agent:`) |
| **stderr exchange log** | Humans | `--trace` or `mas_ctl.trace` in `config.yaml` |
| **`events.jsonl`** | Machines | `--events`, overlay, or manifest `spec.observability` |
| **`mas-lab telemetry`** | Machines | Post-run inspect / export of `events.jsonl` |

`events.jsonl` is the trace of a run: one JSON event per line. Never score
experiments from the exchange log.

---

## `mas-ctl` (day to day)

```bash
mas-ctl chat agent.yaml -q "What is 2+2?"
mas-ctl run-mas mas.yaml -q "Plan a trip from Celestia to Verdantia"
mas-ctl validate agent.yaml
```

These commands chat with an agent, run a team, and check a manifest. Full
flags: [mas-ctl.md](mas-ctl.md). To keep a default, set `mas_ctl.trace: summary`
in [config.yaml](../references/config.yaml.md).

---

## `mas-lab`

```bash
mas-lab init                          # ~/.config/mas/config.yaml (+ optional infra); --local for a project
mas-lab config                        # effective data / cache / labs paths
mas-lab benchmark run experiment.yaml
mas-lab telemetry show traces/events.jsonl
```

| Command | Purpose |
|---------|---------|
| `mas-lab init` | User (or `--local` project) config + optional default LLM infra |
| `mas-lab config` | Print resolved data / cache / labs paths (`--json` for machines) |
| `mas-lab check MAS_SPEC` | Validate a MAS (or any manifest) against its schema and references |
| `mas-lab validate MANIFEST…` | Validate `experiment.yaml`, `pipeline.yaml`, `lab-config.yaml` |
| `mas-lab benchmark …` | `run`, `list`, `show`, `follow`, `analyze`, `artifact`, `pipeline`, `step`, `step-info`, `export` / `import`, `clean`, `rename`, `update` |
| `mas-lab telemetry …` | `show`, `convert` (to OTel spans), `verify`, `inspect`, `push`; ClickHouse queries (`sessions`, `traces`, `apps`, `dump`) |
| `mas-lab plot …` | `trajectory`, `multilevel-trajectory`, `communication-flow`, `list` |
| `mas-lab eval SOURCE` | Score a trace with MCE LLM-as-judge metrics |
| `mas-lab serve` · `control` · `worker` | Controller daemon / HTTP API for the [web UI](../ui/index.md) |

`mas-lab --help` lists every command, including those added by installed
libraries (for example `kg`, `otel`, `replay`, `demo-ui`).

The benchmark flag table is in the repository at
[`lab/docs/cli-reference.md`](https://github.com/outshift-open/mas-lab/blob/main/lab/docs/cli-reference.md).
It is not on this site. `mas-lab benchmark run --help` shows the same flags.

---

## Related

- [User guide](../user-guide.md)
- [User configuration (XDG / paths)](../user-config.md)
- [`mas-ctl compile`](compile.md)
- [Tutorial 0](../tutorials/00-environment-setup/README.md) (install) and [Tutorial 1](../tutorials/01-building-an-agent/README.md) (first agent)
- [TUI](../ctl/tui.md)

Next: [mas-ctl options](mas-ctl.md).
