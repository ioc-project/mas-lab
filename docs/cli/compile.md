<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# `mas-ctl compile`

`mas-ctl compile` applies a manifest and its overlays, then writes the resolved spec. Use it to see what `chat` or `run-mas` will load.

Terms: a *manifest* is a YAML file that declares an agent or a MAS. An *overlay* is a small YAML file merged on top of a manifest. See the [glossary](../glossary.md) and the [overlay manifest](../manifests/overlay.md).

## The three commands most people run

```bash
mas-ctl compile agent.yaml -o overlays/tools.yaml                       # prints the resolved Agent YAML on stdout
mas-ctl compile agent.yaml -o overlays/tools.yaml -O compiled-agent.yaml   # writes it to a file
mas-ctl compile mas.yaml -o overlays/linear.yaml -O ./compiled/            # writes mas.yaml plus agents/ to a folder
```

## What "resolved" means

The resolved spec is the Agent or MAS document in memory before bootstrap wiring. Overlays are merged and omitted runtime defaults are filled. Bootstrap wiring then adds delegation tools, injects skills, and expands tool refs to files.

`compile` applies `target.kind: Agent` overlays onto each nested agent file.

`compose` and `run-mas` behave differently. They merge MAS overlays onto the MAS document (workflow, agency `$op`, models). They merge per-agent overlay patches onto the referenced Agent YAML when that file loads. Form A rows stay `{id, ref}`.

`mas-ctl compose` emits the effective bind and the placement plan. Use `compile` to inspect or snapshot YAML. Use `compose` to inspect runtime bind and placement.

## Task guide

### Print the resolved Agent

```bash
mas-ctl compile docs/tutorials/01-building-an-agent/agent.yaml \
  -o docs/tutorials/01-building-an-agent/overlays/tools.yaml \
  -o docs/tutorials/01-building-an-agent/overlays/skills.yaml \
  -o docs/tutorials/01-building-an-agent/overlays/memory.yaml
```

Repeat `-o` to stack overlays. Later overlays win.

### Save one agent to a file

```bash
mas-ctl compile agent.yaml -o overlays/tools.yaml -O compiled-agent.yaml
```

### Save a MAS as a folder

```bash
mas-ctl compile mas.yaml -o overlays/linear.yaml -O ./compiled/
```

The folder holds `mas.yaml` and an `agents/` directory with rewritten refs.

### Save a MAS as one file

```bash
mas-ctl compile mas.yaml -o overlays/linear.yaml --layout bundle -O team.yaml
```

Agents are inlined under `spec.agency.agents`.

### Patch one field without an overlay file

```bash
mas-ctl compile agent.yaml --override 'agent:spec.context.role="reviewer"'
```

Use the `agent:` root for a standalone Agent. Use the `mas:` root for a MAS. You can select agency entries that carry an inline `spec` by `id`. See [Agents in a MAS](overrides.md#agents-in-a-mas).

### Cap tokens on every model entry

```bash
mas-ctl compile agent.yaml --override 'agent:spec.models[*].max_tokens=4096'
```

### See only the overlay merge

```bash
mas-ctl compile agent.yaml -o overlays/tools.yaml --no-defaults
```

Explicit values are never overwritten, with or without defaults.

## Output layouts

`--output` / `-O` is a folder for a MAS tree. It is a file for a single agent and for `--layout bundle`. Omit it to print a bundle document on stdout.

| Layout | Agent | MAS | Use it to |
| --- | --- | --- | --- |
| `auto` (default) | File or stdout | A directory gives a tree. A `.yaml` path gives a bundle. | Cover the usual cases. |
| `tree` | `agent.yaml` in a folder | `mas.yaml` plus the original `agents/…` paths | Diff, re-validate, or re-run with the same file layout. |
| `bundle` | One Agent YAML | One MAS YAML with inlined Agent documents | Snapshot or review what the runtime saw. |

A bundle is valid input. `agency.agents` accepts either `{id, ref}` or a full `kind: Agent` document. A folder is better for editing. It also suits `mas-ctl validate` and `run-mas`, which expect separate agent files.

Relative tool and skill refs are not rewritten. Library scheme refs such as `samples:tools/…` stay portable. A ref such as `../../tools/foo.tool.yaml` still resolves from the original agent directory, not from the compiled tree.

## Defaults that compile fills

Omitted plugin slots are filled from the same accessors the runtime uses (`defaults.yaml`).

The model is not baked in. An omitted Agent or MAS model compiles to `any`. At run time the model resolves in this order: CLI, Agent, MAS, `experiment.models.main` or `experiment.model`, local `config.yaml` `defaults.model`, then the package `defaults.yaml`.

- `spec.design_pattern`: package default `react`. The string `design_pattern: cot` equals `{type: cot}`. Compile fills `params.max_cot_pass: 1` and `parallel: true`. It omits `max_steps` unless an overlay such as `with-hardened` sets it.
- `spec.models[0]`: `model: any` when omitted. At run time it inherits from the MAS or `experiment.models.main`, then local `config.yaml`.
- MAS `spec.models[0]`: the same `any` sentinel when the MAS omits a default.
- `spec.context_manager`: `summarising`, `keep_turns` 10, `hysteresis_ratio` 0.1, `summarizer: llm`, safety margin 0.1.
- `spec.assembler`: package default `assembler`. Compile fills `emit_segments: true` and `always_reassemble: false`.

The runtime resolves automatic context windows after infra model mappings. Compile does not freeze an unknown or remappable model to a generic window. Unknown models use the 128,000-token fallback and log a warning. See [Context Management](../references/context-management.md).

## Options

| Flag | Default | Description |
| --- | --- | --- |
| `-o` / `--overlay` | none | Overlay YAML. Repeatable. Later wins. |
| `--override ROOT:PATH=VALUE` | none | Schema-backed path override. Repeatable. Applies after overlays. |
| `-O` / `--output` | stdout | Directory or YAML file to write. |
| `--layout auto\|tree\|bundle` | `auto` | Output shape. See the layout table. |
| `--no-defaults` | off | Skip filling runtime defaults. |
| `--no-validate` | off | Skip schema validation. |
| `--no-header` | off | Omit the generated-by comment. |

Compile skips flavour and infra overlays. It emits Agent and MAS specs only. You cannot apply a MAS overlay to an Agent manifest.

For target names, patch schemas, selectors, wildcards, precedence, and compatibility rules, see the [override reference](overrides.md) and the [overlay manifest reference](../manifests/overlay.md).

## See also

- [Tutorial 1](../tutorials/01-building-an-agent/README.md): overlay stack
- [Tutorial 2](../tutorials/02-creating-a-mas/README.md): MAS topology overlays
- [`mas-ctl compose`](mas-ctl.md#other-commands): effective bind and placement
- [summarization.md](../manifests/summarization.md): compiled `summary_threshold`, summarizer model, and `params.instructions`
- Example: [summarizer-override](https://github.com/outshift-open/mas-lab/tree/main/library-standard/examples/context/summarizer-override)
