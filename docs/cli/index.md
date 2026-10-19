<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Command-line tools

MAS-Lab ships two CLIs. **`mas-ctl`** runs agents and MAS manifests.
**`mas-lab`** runs experiments, telemetry, and workspace bootstrap.

`mas-runtime` is a library (no CLI). Use `mas-ctl chat` / `run-mas`.

| CLI | Package | Use for |
|-----|---------|---------|
| [`mas-ctl`](mas-ctl.md) | `ctl/` | Chat, TUI, compile, compose, validate, `run-mas` |
| [`mas-lab`](mas-lab.md) | `lab/` + libraries | Experiments, telemetry, KG, plots — [man-page index](mas-lab.md) |
| `mas-ctl --help` / `mas-ctl COMMAND --help` | — | Always-current flag list |

**mas-lab man pages** live with the owning package ([index](mas-lab.md)).
**Complete mas-ctl option tables:** [mas-ctl.md](mas-ctl.md).
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

Do not score experiments from the exchange log.

---

## `mas-ctl` (day to day)

```bash
mas-ctl chat agent.yaml -q "What is 2+2?"
mas-ctl run-mas mas.yaml -q "Plan a trip from Celestia to Verdantia"
mas-ctl validate agent.yaml
```

Full flags: [mas-ctl.md](mas-ctl.md). Persist human defaults in
[config.yaml](../references/config.yaml.md) (`mas_ctl.trace: summary`).

---

## `mas-lab`

```bash
mas-lab init                          # ~/.config/mas/config.yaml (+ optional infra)
mas-lab config                        # effective paths
mas-lab benchmark run experiment.yaml
mas-lab telemetry show traces/events.jsonl
```

| Command | Purpose |
|---------|---------|
| `mas-lab init` | User config + optional default LLM infra |
| `mas-lab config` | Print resolved data / cache paths |
| `mas-lab check` / `validate` | Experiment / manifest checks |
| `mas-lab benchmark …` | Run, list, show, plot, pipeline |
| `mas-lab telemetry …` | Show / dump / push `events.jsonl` |
| `mas-lab plot …` | Trajectory and communication diagrams |

Benchmark flag table (repo): [`lab/docs/cli-reference.md`](../../lab/docs/cli-reference.md)
(also `mas-lab benchmark run --help`). Not published on GitHub Pages.

---

## Related

- [User guide](../user-guide.md)
- [User configuration (XDG / paths)](../user-config.md)
- [`mas-ctl compile`](compile.md)
- [Tutorial 0](../tutorials/00-environment-setup/README.md)
- [TUI](../ctl/tui.md)
