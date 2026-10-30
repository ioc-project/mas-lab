<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Conversation summarization and eval models

How mas-lab compresses long conversation history, which LLM it uses, where
the token thresholds come from, and how to override the **summarizer** and
**MCE judge** independently.

Committed specs pin a model or say **`any`**. There is no silent global
model in `config.yaml`: that file only fills `any` on the local machine.
The experiment (or lab) pin is a **slot map** (`experiment.models`); scalar
`experiment.model` is shorthand for `models.main`. Summarizer and `eval_mce`
overrides apply on top of those slots.

**Schemas:** `agent.schema.yaml` (`spec.models[]`, `spec.context_manager`,
`spec.working_memory.compaction`) · `experiment.schema.yaml`
(`experiment.models`, `experiment.model`, `experiment.evaluation.model`) ·
**Bindings:** [plugin-bindings.md](plugin-bindings.md)

---

## Why this exists

Long multi-turn agents fill the model context window. Production harnesses
(LangGraph `SummarizationNode` / `ConversationSummaryBufferMemory`, Claude
Code auto-compact, Cursor compact, AutoGen `TokenLimitedChatCompletionContext`,
CrewAI memory summarizer, OpenAI Agents session compaction) all do the same
three things:

1. Keep the **recent** turns verbatim.
2. Compress **older** turns with an LLM (or drop them).
3. Trigger on a **token budget derived from the model context window**, not a
   magic constant.

mas-lab's default `context_manager: summarising` is that pattern. The
summarizer is a **sub-plugin** (`llm` | `drop`) composed by the context
manager — not a second history engine.

---

## How a summary call happens

On every LLM turn, `ContextAssemblerPlugin` builds `messages[]` and asks the
context manager to bound committed history (`manage_history`):

1. Resolve the effective model window and completion reserve. Estimate fixed
  system/context, current input, working-memory, and tool-schema costs, then
  keep the configured safety margin.
2. Compare committed history to the soft **history trigger**. If under budget,
  return history unchanged; the final assembly trim still enforces the hard
  safety-bounded request budget.
3. Split into user-turn groups. Keep the last `keep_turns` (default **10**)
   verbatim.
4. Ask the summarizer to compress the prefix:
   - `summarizer: llm` — one out-of-band `engine.summarize_messages` call
     (counts against `spec.budget.max_llm_calls`). The plugin wraps the prefix
     with `params.instructions` or the package default (see [Trigger vs prompt](#trigger-vs-prompt)).
   - `summarizer: drop` — discard the prefix; no LLM call.
5. Cache the summary (**hysteresis**, default `0.2`). New turns append
   verbatim until the managed payload exceeds `budget × 1.2`, so the
   summarizer is not called on every in-turn tool step.

After the turn commits, the same recency cap is written back to
`committed_messages` (folded prefix dropped). See
[context-assembly.md](context-assembly.md).

The summary call is **not** a tracked agent turn. It does not go through the
ReAct loop or tool dispatch.

---

## Skill content is never compacted

Activated skill instructions are durable guidance
([Agent Skills, Step 5](https://agentskills.io/client-implementation/adding-skills-support#protect-skill-content-from-context-compaction)).
Every history strategy (`summarising`, `sliding_window`, `stack`) exempts them
from pruning:

- An `activate_skill` result in the folded prefix is identified by
  `chunk.pin_policy: never` (and `section_id: skills/activated/<name>`).
  Each section's latest body stays in history as its own `system` row,
  after the summary block.
- The summarizer receives `[content retained verbatim: <section_id>]` in
  place of the body, so the summary never paraphrases instructions.
- The assembly trimmer never removes a retained never-pin row.
- With `pin_activated: true` (default, `spec.context_sources`), the body is
  already pinned in `SYSTEM_SKILLS`, and the retained history row is left out
  of the request. With `pin_activated: false`, the history row is the only
  copy and is always sent.
- The archive leaf that holds the `activate_skill` tool result carries
  `chunk.pin_policy: never` so a summary
  never folds it out of the covering view.

`activate_skill(name, unload=true)` does not rewrite history, so a skill
activated before the unload keeps its retained row.

---

## Trigger vs prompt

**When it fires** is not a user prompt. The CM compares estimated history
tokens to the budget (`context_window − max_tokens`, or
`summary_threshold`). There is no “please summarize now” instruction in the
agent role.

**What the summary LLM sees** is a system prompt plus JSON of older turns.
Default (`SUMMARIZE_INSTRUCTIONS`):

> Summarize the following conversation turns concisely, preserving key facts,
> decisions, and any identifiers (names, IDs, numbers) a later turn might
> need to reference. Write plain prose, not a transcript.

Override with `summarizer.params.instructions`. Empty / omitted → default.

```yaml
params:
  summarizer:
    type: llm
    params:
      model: gpt-4o-mini
      instructions: |
        Preserve city names, dates, and numeric facts. One short paragraph.
```

Runnable pin: [library-standard/examples/context/summarizer-override/](https://github.com/ioc-project/mas-lab/tree/main/library-standard/examples/context/summarizer-override).

---

## Plugin and CM parameters

`summarizer: llm` constructor params (the only new knobs on that plugin):

| Param | Default | Meaning |
|-------|---------|---------|
| `model` | summarizer slot, else this agent's resolved turn model | LiteLLM id or `spec.models[].id` |
| `instructions` | package constant above | System prompt for the summary call |

`drop` has no params. Context-manager knobs (already on `summarising`):

**Default: auto at runtime.** The engine resolves the input window after infra
model mappings, then accounts for completion reserve, fixed request content,
tool schemas, safety margin, and hysteresis.

| Input | Default | Role |
|-------|---------|------|
| `spec.models[].context_window` | omitted = auto; fallback `128000` with warning when unknown | Model **input** window |
| `spec.models[].max_tokens` | `2000` reserve when unset | Completion **reserve** — left free so the next answer still fits. Unset still sends no output limit on the request ([Output-token limits](agent.md#output-token-limits)) |
| `infra.spec.generation.context_window` | omitted | Optional deployment override |
| `summary_threshold` | auto, safety-bounded | Optional absolute estimated-history trigger |
| `trimmer.max_tokens` / `trimmer.reserve_tokens` | auto window / completion budget | Assembly-time payload cap (tool-group-aware) |
| `trimmer.safety_margin_ratio` | `0.1` | Portion kept unused for estimate/provider overhead |
| `keep_turns` | `10` | Recent user turns never summarized |
| `hysteresis_ratio` | `0.1` | Reuse the summary until near the hard budget |

Omitting `context_window` means auto-resolve from the effective model. Explicit
agent and infra overrides take precedence over the model catalog. Unknown models
use the documented fallback with a warning. A positive `summary_threshold`
can move compaction earlier; it is capped so hysteresis stays inside the hard
safety-bounded budget. Explicit trimmer fields override their auto values.

Prompt estimates include tool schemas, system context, and conversation, but
remain a chars÷4 heuristic. Provider usage is recorded separately after a call.
See [Context Management](../references/context-management.md) for the breakdown,
control snapshot, cache rate, and pricing limitations.

`mas-ctl compile agent.yaml` preserves auto values when the final runtime model
is not yet known. Use `inspect_context(session_id)` to see the resolved runtime
window and the latest request estimate.

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

---

## Which model writes the summary?

**Default: the summarizer slot, else this agent's resolved turn model.**

Turn model (`id: main`):

1. Agent `spec.models[id=main]` if concrete
2. else MAS `spec.models[id=main]` if concrete
3. else `experiment.models.main` / `experiment.model` if concrete
4. else `any` → local `config.yaml` `defaults.model`, then package `defaults.yaml`

Summary call, when `summarizer.params.model` is omitted or `any`:

1. Agent `spec.models[id=summarizer]` if concrete
2. else MAS `spec.models[id=summarizer]` if concrete
3. else `experiment.models.summarizer` if concrete
4. else the resolved turn model (`source=agent`)

`summarizer.params.model` overrides that default (a `spec.models[].id` or a
LiteLLM string). `model: any` on the summarizer means inherit the slot chain
above.

### Override on the summarizer sub-plugin (canonical)

`params.model` is a `spec.models[].id` **or** a LiteLLM model string.
Resolution lives in the kernel (`mas.runtime.spec.model_ref`) so
`CMFactory` does not import `library-standard`:

1. empty / omitted / `any` → summarizer slot (Agent → MAS →
   `experiment.models.summarizer`), else this agent's live engine
   (`source=agent`)
2. matching `spec.models[].id` → that entry's `model` string
   (`source=spec.models[id=…]`); if the slot is `any`, inherit MAS /
   `experiment.models.<id>`
3. same string as the engine model → `source=agent`
4. anything else → sent as a LiteLLM id (`source=override`)

If an id matches a `spec.models[]` row that has no `model` field, fall
back to the agent engine rather than sending the bare id to the provider.

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

Shorthand (top-level `model` on the binding, equivalent):

```yaml
params:
  summarizer:
    type: llm
    model: gpt-4o-mini          # raw LiteLLM id
```

String `summarizer: llm` keeps **this agent's** turn model. `summarizer: drop` never
calls an LLM.

### Override on `working_memory.compaction` (sugar)

Prefer `spec.context_manager`. If you still use the compaction facade:

```yaml
spec:
  working_memory:
    compaction:
      strategy: summarize
      model: gpt-4o-mini        # → summarizer: {type: llm, params: {model: …}}
      keep_turns: 10
```

If both `context_manager` and `working_memory.compaction` are set,
`context_manager` wins.

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

---

## Logs (runtime + MCE)

Set log level to **INFO** (`mas-ctl -v` / the lab runner) to see the models.

| Logger | When | What |
|--------|------|------|
| `mas.library.standard.plugins.context.summarizer` | CM bind | `summarizer llm: model=… source=… agent_model=… instructions=default|override` |
| `mas.runtime.engine.llm_live` | each summary HTTP call | `history summarizer: completing N message(s) model=… (override)` |
| `mas.library.standard.plugins.context.conversation` | compaction fires | `compacted N exchange(s) model=… source=… estimated_tokens=… threshold=… keep_turns=…` |
| `mas.library.eval.mce.runner` | MCE jury install | `MCE Jury configured (model=…, source=…, …)` |
| `mas.library.lab.steps.eval.mce` | `eval_mce` step | `judge model=… source=…` |

`source` is the spec field that won (`agent`, `spec.models[id=summarizer]`,
`override`, `experiment.evaluation.model`, `eval_mce.config.model`,
`application.spec.models`, `experiment.metadata.model_name`,
`defaults.model`, …).

Compaction also stores `model`, `model_source`, `estimated_tokens`, and
`threshold` on `last_compaction_metadata` (forwarded on the assembler hook as
`_compaction_metadata` for observability). `eval_mce` puts
`judge_model` / `judge_model_source` on the step metadata.

---

## MCE judge model {#mce-judge-model}

`eval_mce` is LLM-as-judge. Default is **`experiment.models.judge`**, then the
resolved turn model (`models.main` / `experiment.model` → MAS → agent). Pin a
different judge with `experiment.evaluation.model` or `eval_mce.config.model`.

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

Alias: `evaluation.config.model` (the top-level `evaluation.model` wins when
both are set). Interactive labs use the same fields on `lab-config.yaml`.

### Resolution order

1. `eval_mce` step `config.model` (or `config.judge_model`)
2. `experiment.evaluation.model` (judge override)
3. `experiment.evaluation.config.model`
4. pipeline template vars `eval_model` / `judge_model`
5. `experiment.models.judge`
6. `experiment.model` / `experiment.models.main` (`any` skipped).
   The env override (`MAS_CTL_MODEL` / `MAS_LLM_MODEL`) fills this slot
   so the judge matches the agents when `models.judge` is omitted.
7. application MAS/Agent unique concrete `spec.models[]`
8. `experiment.metadata.model_name` (legacy annotation)
9. local `config.yaml` `defaults.model` / package `defaults.yaml` (only for `any`)

Metric *prompts* for stock MCE ids are owned by MCE (`mce_metrics_plugin`);
mas-lab does not override them. Only the **model id** is a step/lab field.
Libraries may register additional session metrics (`type: eval_metric` on the
MCE eval provider). A pipeline may also define inline metrics on
`eval_mce.config.prompt_metrics` (`id`, `prompt`, `unit`, `evidence`).
Those fields belong to the metric. Stock MCE ids use MAS input/output.
All ids land in the same `metrics.json`. Map:
[Tutorial 10](../tutorials/10-evaluation-metrics/README.md).

Runnable pin: [library-eval/examples/mce/judge-override/](https://github.com/ioc-project/mas-lab/tree/main/library-eval/examples/mce/judge-override).

---

## Compared to other harnesses

| Harness | Trigger | Summarizer model | Recency |
|---------|---------|------------------|---------|
| **mas-lab** | `context_window − max_tokens` (overridable) | This agent's `spec.models[]`; `summarizer.params.model` override | `keep_turns` (10) + hysteresis 0.2 |
| LangGraph | `max_tokens` on `ConversationSummaryBufferMemory` / `SummarizationNode` | Often a cheaper bound LLM | keep recent messages |
| Claude Code / Cursor | Near the model window | Often a cheaper compact model | keep recent turns |
| AutoGen | `TokenLimitedChatCompletionContext` | Optional transform model | token cap |
| CrewAI | Memory summarizer LLM | Can differ from agent LLM | memory window |
| OpenAI Agents SDK | Session compaction | Session LLM | recent items |

mas-lab follows that shape: window-relative budget, recency pin, optional
cheaper summarizer, judge override on the eval spec.

---

## See also

- [context-assembly.md](context-assembly.md) — assembler, trim, pairing
- [plugin-bindings.md](plugin-bindings.md) — `summarizer` sub-plugin
- [agent.md](agent.md) — `models[]`, `working_memory`
- [experiment.md](experiment.md) — `evaluation.model`
- [Compiled agent defaults](../references/defaults.md) — `mas-ctl compile` expansion
- [working-memory-compaction.md](../design/working-memory-compaction.md) — design history
- [Pipeline steps](https://github.com/outshift-open/mas-lab/blob/main/lab/docs/pipeline-steps.md) — `eval_mce`
- Feature examples (not sample apps): [summarizer-override](https://github.com/ioc-project/mas-lab/tree/main/library-standard/examples/context/summarizer-override) · [MCE judge-override](https://github.com/ioc-project/mas-lab/tree/main/library-eval/examples/mce/judge-override)
- Plugin card: [summarizer.md](https://github.com/ioc-project/mas-lab/blob/main/library-standard/src/mas/library/standard/plugins/context/summarizer.md)
