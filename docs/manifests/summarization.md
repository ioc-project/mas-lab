<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Conversation summarization and eval models

This page covers how the default `context_manager: summarising` compresses long conversation history.
It explains which LLM writes the summary, where the token thresholds come from, and how to override the **summarizer** and the **MCE judge** independently.

Terms used here:

- **Summarizer**: the LLM call that compresses older turns into short text.
- **Compaction**: shrinking history, by summary or by dropping turns, so it fits the context window.
- **Context window**: the maximum tokens a model reads in one request.
- **MCE judge**: the LLM-as-judge in MCE, the Metrics Computation Engine behind `eval_mce` pipeline steps.

Read this page when a long conversation overflows the context window or when you want a cheaper summary model.
This override uses a cheaper model for summaries:

```yaml
spec:
  context_manager:
    type: summarising
    params:
      keep_turns: 10
      summarizer:
        type: llm
        params:
          model: gpt-4o-mini
```

Committed specs pin a model or say **`any`**.
`config.yaml` holds no silent global model: it only fills `any` on the local machine.
The experiment (or lab) pin is a **slot map** (`experiment.models`).
The scalar `experiment.model` is shorthand for `models.main`.
Summarizer and `eval_mce` overrides apply on top of those slots.

Schemas:

- `agent.schema.yaml`: `spec.models[]`, `spec.context_manager`, `spec.working_memory.compaction`.
- `experiment.schema.yaml`: `experiment.models`, `experiment.model`, `experiment.evaluation.model`.

Bindings: [plugin-bindings.md](plugin-bindings.md).

## Contents

- [Why](#why)
- [How a summary call happens](#how-a-summary-call-happens)
- [Skill content is never compacted](#skill-content-is-never-compacted)
- [Trigger vs prompt](#trigger-vs-prompt)
- [Plugin and CM parameters](#plugin-and-cm-parameters)
- [Which model writes the summary?](#which-model-writes-the-summary)
- [Logs (runtime + MCE)](#logs-runtime-mce)
- [MCE judge model](#mce-judge-model)
- [See also](#see-also)

## Why

Long multi-turn agents fill the model context window.
The default keeps recent turns verbatim and compresses older turns with an LLM, or drops them.
It triggers on a token budget derived from the context window.
The summarizer is a **sub-plugin** (`llm` or `drop`) of the context manager, not a second history engine.

## How a summary call happens

On every LLM turn, `ContextAssemblerPlugin` builds `messages[]`.
It asks the context manager to bound committed history (`manage_history`):

1. Resolve the effective model window and the completion reserve. Estimate the costs of the fixed system context, the current input, working memory, and tool schemas. Keep the configured safety margin.
2. Compare committed history to the soft **history trigger**. If history is under budget, return it unchanged. The final assembly trim still enforces the hard safety-bounded request budget.
3. Split history into user-turn groups. Keep the last `keep_turns` (default **10**) verbatim.
4. Ask the summarizer to compress the older prefix:
    - `summarizer: llm` makes one out-of-band `engine.summarize_messages` call. It counts against `spec.budget.max_llm_calls`. The plugin wraps the prefix with `params.instructions` or the package default (see [Trigger vs prompt](#trigger-vs-prompt)).
    - `summarizer: drop` discards the prefix and makes no LLM call.
5. Cache the summary (**hysteresis**, `hysteresis_ratio` default `0.1`). New turns append verbatim until the managed payload exceeds `budget x 1.1`. The summarizer is therefore not called on every in-turn tool step.

After the turn commits, the runtime writes the same recency cap back to `committed_messages` and drops the folded prefix.
See [context-assembly.md](context-assembly.md).

The summary call is **not** a tracked agent turn.
It does not go through the ReAct loop or tool dispatch.

## Skill content is never compacted

Activated skill instructions are durable guidance ([Agent Skills, Step 5](https://agentskills.io/client-implementation/adding-skills-support#protect-skill-content-from-context-compaction)).
Every history strategy (`summarising`, `sliding_window`, `stack`) exempts them from pruning:

- The runtime identifies an `activate_skill` result in the folded prefix by its `<skill_content name="...">` wrapper. Each skill's latest block stays in history as its own `system` row, after the summary block.
- The summarizer receives `[skill content retained verbatim: <name>]` in place of the body. The summary never paraphrases skill instructions.
- The assembly trimmer never removes a retained skill row.
- With `pin_activated: true` (default, `spec.context_sources`), the body is already pinned in `SYSTEM_SKILLS`. The request leaves out the retained history row.
- With `pin_activated: false`, the history row is the only copy, and the request always sends it.

`activate_skill(name, unload=true)` does not rewrite history.
A skill activated before the unload keeps its retained row.

## Trigger vs prompt

**When it fires** is not a user prompt.
The context manager (CM) compares estimated history tokens to a budget.
By default the budget is the input window, minus the completion reserve, the safety margin, and the fixed request content (system context, tools).
The runtime divides the result by `1 + hysteresis_ratio`, so the reuse ceiling still fits.
A positive `summary_threshold` lowers the budget further.
The agent role contains no "please summarize now" instruction.

**What the summary LLM sees** is a system prompt plus JSON of older turns.
The default (`SUMMARIZE_INSTRUCTIONS`) reads:

> Summarize the following conversation turns concisely, preserving key facts,
> decisions, and any identifiers (names, IDs, numbers) a later turn might
> need to reference. Write plain prose, not a transcript.

Override it with `summarizer.params.instructions`.
An empty or omitted value restores the default.

```yaml
params:
  summarizer:
    type: llm
    params:
      model: gpt-4o-mini
      instructions: |
        Preserve city names, dates, and numeric facts. One short paragraph.
```

Runnable pin: [library-standard/examples/context/summarizer-override/](https://github.com/outshift-open/mas-lab/tree/main/library-standard/examples/context/summarizer-override).

## Plugin and CM parameters

`summarizer: llm` has two constructor params, the only new knobs on that plugin:

| Param | Default | Meaning |
|-------|---------|---------|
| `model` | summarizer slot, else this agent's resolved turn model | LiteLLM id or `spec.models[].id` |
| `instructions` | package constant above | System prompt for the summary call |

`drop` has no params.

The context-manager knobs below already exist on `summarising`.
By default they resolve automatically at runtime.
The engine resolves the input window after infra model mappings.
It then accounts for the completion reserve, fixed request content, tool schemas, safety margin, and hysteresis.

| Input | Default | Role |
|-------|---------|------|
| `spec.models[].context_window` | omitted = auto; fallback `128000` with warning when unknown | Model **input** window |
| `spec.models[].max_tokens` | `2000` reserve when unset | Completion **reserve**, left free so the next answer still fits. Unset still sends no output limit on the request ([Output-token limits](agent.md#output-token-limits)) |
| Infra `spec.models.generation.context_window` | omitted | Optional deployment override ([infra.md](infra.md#llm-output-token-policy)) |
| `summary_threshold` | auto, safety-bounded | Optional absolute estimated-history trigger |
| `trimmer.max_tokens` / `trimmer.reserve_tokens` | auto window / completion budget | Assembly-time payload cap (tool-group-aware) |
| `trimmer.safety_margin_ratio` | `0.1` | Portion kept unused for estimate and provider overhead |
| `keep_turns` | `10` | Recent user turns never summarized |
| `hysteresis_ratio` | `0.1` | Reuse the summary until near the hard budget |

Omit `context_window` to auto-resolve it from the effective model.
Explicit agent and infra overrides take precedence over the model catalog.
Unknown models use the documented fallback with a warning.
A positive `summary_threshold` can move compaction earlier.
The runtime caps it so hysteresis stays inside the hard safety-bounded budget.
Explicit trimmer fields override their auto values.

Prompt estimates include tool schemas, system context, and conversation.
They remain a chars/4 heuristic.
The runtime records provider usage separately after a call.
See [Context Management](../references/context-management.md) for the breakdown, control snapshot, cache rate, and pricing limitations.

`mas-ctl compile agent.yaml` preserves auto values when the final runtime model is not yet known.
Use the control API's `inspect_context(session_id)` (`ControlContract`) to see the resolved runtime window and the latest request estimate.

```yaml
spec:
  models:
    - id: main
      model: gpt-4o
      max_tokens: 2000          # completion reserve
      # context_window omitted: auto-resolve from the effective model
  context_manager:
    type: summarising           # package default
    params:
      keep_turns: 10
      hysteresis_ratio: 0.1
      summarizer: llm           # summarizer slot, else turn model
      trimmer:
        reserve_tokens: 2000
        safety_margin_ratio: 0.1
```

Override the trigger without changing the model:

```yaml
spec:
  context_manager:
    type: summarising
    params:
      summary_threshold: 8000   # compact earlier (absolute estimated tokens)
      trimmer:
        max_tokens: 12000
        reserve_tokens: 512
```

## Which model writes the summary?

The default is the summarizer slot, else this agent's resolved turn model.

The turn model (`id: main`) resolves in this order:

1. Agent `spec.models[id=main]` if concrete.
2. Else MAS `spec.models[id=main]` if concrete.
3. Else `experiment.models.main` or `experiment.model` if concrete.
4. Else `any`: local `config.yaml` `defaults.model`, then package `defaults.yaml`.

The summary call resolves in this order when `summarizer.params.model` is omitted or `any`:

1. Agent `spec.models[id=summarizer]` if concrete.
2. Else MAS `spec.models[id=summarizer]` if concrete.
3. Else `experiment.models.summarizer` if concrete.
4. Else the resolved turn model (`source=agent`).

`summarizer.params.model` overrides that default.
It takes a `spec.models[].id` or a LiteLLM string.
`model: any` on the summarizer means inherit the slot chain above.

### Override on the summarizer sub-plugin (canonical)

`params.model` is a `spec.models[].id` **or** a LiteLLM model string.
Resolution lives in the kernel (`mas.runtime.spec.model_ref`), so `CMFactory` does not import `library-standard`:

1. Empty, omitted, or `any`: use the summarizer slot (Agent, then MAS, then `experiment.models.summarizer`), else this agent's live engine (`source=agent`).
2. A matching `spec.models[].id`: use that entry's `model` string (`source=spec.models[id=...]`). If the slot is `any`, inherit MAS or `experiment.models.<id>`.
3. The same string as the engine model: `source=agent`.
4. Anything else: send it as a LiteLLM id (`source=override`).

If an id matches a `spec.models[]` row that has no `model` field, the runtime falls back to the agent engine.
It does not send the bare id to the provider.

```yaml
spec:
  models:
    - id: main
      model: gpt-4o
      context_window: 128000
      max_tokens: 2000
    - id: summarizer
      model: gpt-4o-mini
      context_window: 128000
      max_tokens: 2000
  context_manager:
    type: summarising
    params:
      summarizer:
        type: llm
        params:
          model: summarizer     # spec.models[].id → gpt-4o-mini
```

The shorthand puts `model` at the top level of the binding.
It is equivalent:

```yaml
params:
  summarizer:
    type: llm
    model: gpt-4o-mini          # raw LiteLLM id
```

The string `summarizer: llm` keeps **this agent's** turn model.
`summarizer: drop` never calls an LLM.

### Override on `working_memory.compaction` (sugar)

Prefer `spec.context_manager`.
If you still use the compaction facade:

```yaml
spec:
  working_memory:
    compaction:
      strategy: summarize
      model: gpt-4o-mini        # → summarizer: {type: llm, params: {model: …}}
      keep_turns: 10
```

If you set both `context_manager` and `working_memory.compaction`, `context_manager` wins.

### Overlay (per experiment / flavour)

```yaml
apiVersion: mas/v1
kind: Overlay
metadata: {name: cheap-summarizer}
spec:
  target: {kind: Agent}
  patch:
    context_manager:
      type: summarising
      params:
        summarizer:
          type: llm
          params:
            model: gpt-4o-mini
```

## Logs (runtime + MCE)

Set the log level to **INFO** (`mas-ctl -v` or the lab runner) to see the models.

| Logger | When | What |
|--------|------|------|
| `mas.library.standard.plugins.context.summarizer` | CM bind | `summarizer llm: model=… source=… agent_model=… instructions=default|override` |
| `mas.runtime.engine.llm_live` | each summary HTTP call | `history summarizer: completing N message(s) model=… (override)` |
| `mas.library.standard.plugins.context.conversation` | compaction fires | `compacted N exchange(s) model=… source=… estimated_tokens=… threshold=… keep_turns=…` |
| `mas.library.eval.mce.runner` | MCE jury install | `MCE Jury configured (model=…, source=…, …)` |
| `mas.library.lab.steps.eval.mce` | `eval_mce` step | `judge model=… source=…` |

`source` is the spec field that won.
Values include `agent`, `spec.models[id=summarizer]`, `override`, `experiment.evaluation.model`, `eval_mce.config.model`, `application.spec.models`, `experiment.metadata.model_name`, and `defaults.model`.

Compaction also stores `model`, `model_source`, `estimated_tokens`, and `threshold` on `last_compaction_metadata`.
The assembler hook forwards it as `_compaction_metadata` for observability.
`eval_mce` puts `judge_model` and `judge_model_source` on the step metadata.

## MCE judge model {#mce-judge-model}

`eval_mce` is LLM-as-judge.
The default is **`experiment.models.judge`**, then the resolved turn model (`models.main` or `experiment.model`, then MAS, then agent).
Pin a different judge with `experiment.evaluation.model` or `eval_mce.config.model`.

```yaml
experiment:
  name: topology-ablation
  models:
    main: gpt-4o                # turn default
    judge: gpt-4o-mini          # MCE default
  applications:
    - manifest: ./mas.yaml      # spec.models[] if models.main is any
  evaluation:
    method: llm_judge
    # model: gpt-4o             # optional judge override (wins over models.judge)
  application:
    post:
      - type: eval_mce          # inherits models.judge
        depends_on: [extract_trajectories]
      - type: eval_mce
        name: eval_mce_strict
        config:
          model: gpt-4o         # per-step override
```

The alias `evaluation.config.model` also works, and the top-level `evaluation.model` wins when both are set.
Interactive labs use the same fields on `lab-config.yaml`.

### Resolution order

1. `eval_mce` step `config.model` (or `config.judge_model`)
2. `experiment.evaluation.model` (judge override)
3. `experiment.evaluation.config.model`
4. pipeline template vars `eval_model` / `judge_model`
5. `experiment.models.judge`
6. `experiment.model` / `experiment.models.main` (`any` skipped)
7. application MAS/Agent unique concrete `spec.models[]`
8. `experiment.metadata.model_name` (legacy annotation)
9. local `config.yaml` `defaults.model` / package `defaults.yaml` (only for `any`)

MCE owns the metric *prompts* for stock MCE ids (`mce_metrics_plugin`), and mas-lab does not override them.
Only the **model id** is a step or lab field.

Libraries can register additional session metrics (`type: eval_metric` on the MCE eval provider).
A pipeline can also define inline metrics on `eval_mce.config.prompt_metrics` (`id`, `prompt`, `unit`, `evidence`).
Those fields belong to the metric.
Stock MCE ids use MAS input and output.
All ids land in the same `metrics.json`.
The map is in [Tutorial 10](../tutorials/10-evaluation-metrics/README.md).

Runnable pin: [library-eval/examples/mce/judge-override/](https://github.com/outshift-open/mas-lab/tree/main/library-eval/examples/mce/judge-override).

## See also

- [context-assembly.md](context-assembly.md): assembler, trim, pairing
- [plugin-bindings.md](plugin-bindings.md): `summarizer` sub-plugin
- [agent.md](agent.md): `models[]`, `working_memory`
- [experiment.md](experiment.md): `evaluation.model`
- [Compiled agent defaults](../references/defaults.md): `mas-ctl compile` expansion
- [working-memory-compaction.md](../design/working-memory-compaction.md): design notes
- [Tutorial 3](../tutorials/03-experiments-and-analysis/README.md): experiments that pin models
- [Pipeline steps](https://github.com/outshift-open/mas-lab/blob/main/lab/docs/pipeline-steps.md): `eval_mce`
- Feature examples (not sample apps): [summarizer-override](https://github.com/outshift-open/mas-lab/tree/main/library-standard/examples/context/summarizer-override) and [MCE judge-override](https://github.com/outshift-open/mas-lab/tree/main/library-eval/examples/mce/judge-override)
- Plugin card: [summarizer.md](https://github.com/outshift-open/mas-lab/blob/main/library-standard/src/mas/library/standard/plugins/context/summarizer.md)
