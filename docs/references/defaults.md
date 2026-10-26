<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Compiled agent defaults

This page shows what a minimal `Agent` manifest expands to. Read it to see which defaults the runtime applies to the fields you leave out.

## The problem

A minimal manifest omits most fields. `mas-ctl compile` fills each omitted field before the runtime sees the manifest. It sets the plugin for every singleton slot (`design_pattern`, `context_manager`, `assembler`). It sets the sub-plugin that one slot composes (`context_manager.params.summarizer`). It sets every attribute each plugin takes.

A *plugin* is a unit of behaviour that a manifest selects by name. Three forms give the **same runtime behaviour**: omit the field, write the plugin name, or write the fully expanded object. See [plugin-bindings.md](../manifests/plugin-bindings.md).

This page is a worked example, not a schema. Field-by-field docs are on the pages linked from each block. If a number here looks stale, re-run the command in [Regenerating this page](#regenerating-this-page) and trust its output.

---

## The input: Tutorial 1's minimal manifest

[`docs/tutorials/01-building-an-agent/agent.yaml`](../tutorials/01-building-an-agent/agent.yaml)
declares a model, a description, and a system-prompt fragment. It declares nothing else:

```yaml
apiVersion: mas/v1
kind: Agent

metadata:
  name: qa-agent

spec:
  models:
    - model: gpt-4o

  description: Answer general knowledge questions.
  context:
    intent: "Answer general knowledge questions."
    role: |
      Answer questions clearly and concisely.
```

## The output: `mas-ctl compile agent.yaml`

```yaml
apiVersion: mas/v1
kind: Agent
metadata:
  name: qa-agent
spec:
  models:
  - model: gpt-4o
  description: Answer general knowledge questions.
  context:
    intent: Answer general knowledge questions.
    role: 'Answer questions clearly and concisely.

      '
  design_pattern:
    type: react@v1
    params:
      max_cot_pass: 1
      parallel: true
  context_manager:
    type: summarising
    params:
      keep_turns: 10
      hysteresis_ratio: 0.1
      summarizer: llm
      trimmer:
        reserve_tokens: 2000
        safety_margin_ratio: 0.1
  assembler:
    type: assembler
    params:
      emit_segments: true
      always_reassemble: false
```

## What each block is

| Block | Plugin (default) | Attributes | Docs |
|-------|-------------------|------------|------|
| `models[0].context_window` | none | Auto-resolved from the effective model; an unknown model warns and uses `128000` | [agent.md](../manifests/agent.md#spec-fields) · [context-management](context-management.md) |
| `design_pattern` | `react@v1` (registry alias `react`) | `max_steps` (dispatch-loop cap), `max_cot_pass`, `parallel` | [plugin-bindings.md § design_pattern](../manifests/plugin-bindings.md#design_pattern-default-react) |
| `assembler` | `assembler` (`ContextAssemblerPlugin`) | `emit_segments`, `always_reassemble` — builds `messages[]`; **not** where history policy lives | [plugin-bindings.md § assembler](../manifests/plugin-bindings.md#assembler-default-assembler) · [context-assembly.md](../manifests/context-assembly.md#assembler-required-default) |
| `context_manager` | `summarising` | `keep_turns`, `hysteresis_ratio`, `summary_threshold`, `archive`, `trimmer.{max_tokens,reserve_tokens,safety_margin_ratio}` | [plugin-bindings.md § context_manager](../manifests/plugin-bindings.md#context_manager-default-summarising) · [context-management](context-management.md) |
| `context_manager.params.summarizer` | `llm` (sub-plugin of `summarising`) | `params.model` omitted → summarizer slot (Agent/MAS/`experiment.models.summarizer`) else agent's primary model. Compile does **not** invent a cheaper summarizer. | [plugin-bindings.md § summarizer](../manifests/plugin-bindings.md#summarizer-sub-plugin-of-summarising) · [summarization.md](../manifests/summarization.md) |

Every block is a real registered plugin. The runtime resolves `type: react@v1`, `type: summarising`, `type: assembler`, and `type: llm` through the same registry as an explicit choice. No hidden "if omitted, do X in code" branch exists. The default *is* a binding.

Defaults come from two places:

- The singleton slots (`model`, `design_pattern`, `context_manager`, `assembler`) come from the built-in `runtime/src/mas/runtime/defaults.yaml` of `mas-runtime`.
- Other slots and sub-plugins (`summarizer`, `tool_provider`, `llm_provider`, `agent_comm`, `hitl_contract`, `user_io_contract`) come from the `defaults:` map in `library-standard/library.yaml`.

You can override the four singleton keys per workspace in `config.yaml` `defaults:`. No agent manifest changes. See [config.yaml, defaults](config.yaml.md#defaults).

## Regenerating this page

The numbers above drift as defaults change. Reproduce them from any manifest. The expansion works on your own agents, not only the tutorial:

```bash
cd docs/tutorials/01-building-an-agent
mas-ctl compile agent.yaml --no-header
```

Add `-o overlays/<file>.yaml` (repeatable) to fold overlays in first. An *overlay* is a small YAML file merged on top of a manifest. See the [compile command reference](../cli/compile.md). To see what a manifest sets versus package defaults, add `--no-defaults` and diff.

## See also

- [plugin-bindings.md](../manifests/plugin-bindings.md): the binding shape these defaults fill (string equals object, list slots).
- [agent.md](../manifests/agent.md): every `spec` field, not only the plugin slots shown here.
- [context-assembly.md](../manifests/context-assembly.md): how `assembler`, `context_manager`, and `summarizer` cooperate at assemble time.
- [summarization.md](../manifests/summarization.md): thresholds, summarizer and judge model, logs.
- [working-memory-compaction.md](../design/working-memory-compaction.md): why `context_manager` is the single history-policy surface.
- [config.yaml reference](config.yaml.md): the `defaults:` override.
- [Tutorial 1: building an agent](../tutorials/01-building-an-agent/README.md): the manifest this page compiles.
