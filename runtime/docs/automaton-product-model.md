<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Agent automaton as a product of Mealy machines

This page is the formal reference for how the MAS runtime composes an agent from small state machines.
It is written for runtime architects, contract implementers, and manifest authors.
For a gentler introduction, read [mealy-machines-guide.md](mealy-machines-guide.md) first.
For the implementation checklist (authoritative for v0.1), read [mealy-envelope.md](mealy-envelope.md).

> **Naming.** The shipped kernel uses `CMFactory` (`runtime/src/mas/runtime/contracts/`) and
> `DesignPatternPlugin` (`runtime/src/mas/runtime/machines/design_pattern/`). The names `DPFactory`,
> `DPPhaseMachine`, and `DPMachine` describe the formal design target. §16 maps each concept to what ships today.

Companion pages: [contracts/DESIGN_SPACE.md](contracts/DESIGN_SPACE.md) (ten dimensions) and
[contracts/MANIFEST_MAPPING.md](contracts/MANIFEST_MAPPING.md) (manifest mapping).

## Smallest example

An agent is one product formula whose factors the manifest chooses. This fragment is illustrative:

```yaml
spec:
  design_pattern:
    type: react            # picks one summand of the design-pattern sum
governance:
  - type: budget           # one governance machine per entry
observability:
  - type: otel             # one observer machine per entry
```

Every side effect (LLM call, tool call, memory read) runs as a fixed word of seven symbols
(`σ₁ ∘ … ∘ σ₇`, see §3.4) on which all governance and observability machines step.

**Contents:** 1 Overview · 2 Mealy notation · 3 Composition operators · 4 Full agent formula ·
5 Dimension roles · 6 Context sources · 7 Context machine · 8 Observability and governance ·
9 Design patterns · 10 Alphabet and shared tape · 11 Contract registry · 12 Multi-agent and I/O boundaries ·
13 Hook equivalence · 14 Mutation matrix · 15 Manifest and kernel · 16 Shipped kernel and implementation map

---

## 1. Overview

An agent is not one state machine. It is a composed automaton built from four ingredients:

1. **Orthogonal dimensions** that coexist for the whole session: infrastructure, capabilities, governance, observability.
2. **Context sources** that all produce the same artifact, a `ContextPart` with `ContextProvenance`, through different calling paths (§6).
3. **Design patterns** (ReAct, CoT, Plan-Execute, ...) that schedule which contract calls happen and in what order. ReAct is one instance, not the kernel. Design patterns also provide context (`collect_context`).
4. **Contract-call envelopes** that wrap every side effect (LLM, tool, memory query, transport send) with governance before and after, and observability throughout.

The kernel is a thin orchestrator. It steps the product on input symbols, runs impure I/O in `execute_fn` callbacks, interprets machine outputs (especially from `DPMachine` and `ContextMachine`), and never hard-codes ReAct.

## 2. Mealy machine notation

Each subsystem is a Mealy machine \(M = (Q, \Sigma, \Gamma, \delta, \lambda, q_0)\):

| Symbol | Meaning |
|--------|---------|
| \(Q\) | Finite state set |
| \(\Sigma\) | Input alphabet (global symbols and dimension-specific symbols) |
| \(\Gamma\) | Output alphabet (actions plus `NO_OUTPUT`) |
| \(\delta: Q \times \Sigma \to Q\) | Transition, optionally guarded by `TransitionContext` |
| \(\lambda: Q \times \Sigma \to \Gamma\) | Output |
| \(q_0\) | Initial state |

Guards are predicates on `TransitionContext` (agent_id, messages, security annotations, pending tool name, and so on). The production composer, `GuardedProductComposer`, evaluates guards at step time. It does not drop them at build time.

**Purity rule.** Machines never perform I/O. Side effects live in the `execute_fn` passed to `execute_contract_call()`. The product learns the result through `{contract}_execute` symbols and `context.metadata["result"]`.

## 3. Composition operators

The agent formula uses four composition forms. Only the first is a classical automata product; the others are control structure.

### 3.1 Synchronous parallel product (⊗)

For machines \(M_1, \ldots, M_n\), the product \(M_1 \otimes \cdots \otimes M_n\) has state \(q = (q_1, \ldots, q_n)\).
On input \(\sigma\), each component with an enabled transition fires. The others hold their state and output `NO_OUTPUT`.
The joint output is the ordered list of \((\text{id}_i, \lambda_i(q_i, \sigma))\).

Dimensions are orthogonal: session state does not replace model state, and one governance instance does not replace another.

- **Runtime:** `GuardedProductComposer`, \(O(n)\) per step, no state-space explosion.
- **Offline verification:** `ProductMealyComposer` materialises \(Q_1 \times \cdots \times Q_n\) for model checking and under-approximates guarded transitions.

### 3.2 Family product (⊗ᵢ): equivalent plugins

When several plugins implement the same role and all must see every relevant symbol, they form a family product:

\[
M_{\text{gov}} = M_{\text{gov}_1} \otimes \cdots \otimes M_{\text{gov}_n}
\qquad
M_{\text{obs}} = M_{\text{obs}_1} \otimes \cdots \otimes M_{\text{obs}_m}
\]

`governance` and `observability` are `MULTIPLE_INSTANCE` dimensions. Each instance is an independent machine registered as `governance_budget`, `governance_sandbox`, `observability_otel`, `observability_native`, and so on.

- On `governance_authorize` and `governance_validate`, every governance machine steps. Any `ABORT_EXECUTION` or `DENY` aborts the call (`GovernanceError`).
- On every symbol of a contract-call envelope (all seven), every observability machine steps. Observers are equivalent: none is authoritative, none filters another's input, and all see governance decisions before and after execution.

### 3.3 Tagged sum (⊔): exactly one active variant

When the manifest selects exactly one implementation, the choice is a disjoint union of state spaces with a tag:

\[
M_{\text{DP}} \in \{M_{\text{ReAct}}, M_{\text{CoT}}, M_{\text{PlanExecute}}, \ldots\}
\qquad
M_{\text{CM}} \in \{M_{\text{stack}}, M_{\text{sliding}}, M_{\text{summarising}}, \ldots\}
\]

The runtime does not run all patterns in parallel. `DPFactory` and `CMFactory` instantiate the selected variant, and switching pattern means re-instantiating the product with another summand.

### 3.4 Sequential composition (∘): the seven-symbol envelope

A contract call is a fixed word over \(\Sigma\), not one product step:

\[
\mathcal{E}_{\text{call}}(c) = \sigma_1 \circ \sigma_2 \circ \cdots \circ \sigma_7
\]

`MealyProductIntegration.execute_contract_call(contract_type, operation, execute_fn)` implements it. `contract_type` is any string.

| # | Symbol | Who fires | Purpose |
|---|--------|-----------|---------|
| 1 | `{c}_{op}_start` | Obs\* | Open span, announce call |
| 2 | `governance_authorize` | Gov\* + Obs\* | Authorize before the side effect |
| 3 | `observability_pre_execute` | Obs\* | Snapshot pre-state |
| - | `execute_fn()` | **Kernel** | Actual I/O, outside the product |
| 4 | `{c}_{op}_execute` | Cap + Obs\* + DP | Notify the product of the result |
| 5 | `observability_post_execute` | Obs\* | Snapshot post-state |
| 6 | `governance_validate` | Gov\* + Obs\* | Validate after the side effect |
| 7 | `{c}_{op}_end` | Obs\* | Close span |

\*All instances of the family product step.

Governance touches symbols 2 and 6 only. Observability touches all seven. The capability machine for \(c\) is logically active at symbol 4. Context assembly uses the same pattern with `ctx_collect_*`, `ctx_filter_*`, and `ctx_assemble_*` triples (§7).

Today any `contract_type` string can use the envelope. The target covers every capability invocation: model, tool, memory, prompt, transport, session checkpoint, and ctx filter.

No capability call bypasses the envelope. Plugins never invoke governance themselves; the product does. Observability never blocks: an observer error moves that machine to `ERROR` (fail-open).

### 3.5 Iteration (μ): the design-pattern loop

The turn loop is not product state. It is an outer scheduler driven by `DPMachine` outputs:

\[
\mu X.\; \text{step}(\text{iteration\_start}) \;;\; \mathcal{E}_{\text{ctx}} \;;\; \mathcal{E}_{\text{llm}} \;;\; (\text{branch}_{\text{tool}} \cdot \mathcal{E}_{\text{tool}} \cdot \mathcal{E}_{\text{ctx}}')^* \;;\; \text{step}(\text{iteration\_end}) \;;\; X
\]

`DPMachine` emits `THINK` (to the LLM), `ACT` (to a tool), `FINALIZE`, `WAIT_EVENT`, and similar actions. ReAct is one fixed point of this scheduler, CoT may skip the tool branch, and Plan-Execute inserts `PLAN` before `ACT`. The kernel must not hard-code `if tool_calls`; it interprets DP outputs.

## 4. Full agent formula

\[
\boxed{
M_{\text{agent}} =
M_{\text{transport}}^{\varnothing}
\otimes M_{\text{session}}
\otimes M_{\text{obs}}^{\otimes m}
\otimes M_{\text{ctx}}
\otimes M_{\text{DP}}^{(p)}
\otimes M_{\text{model}}
\otimes M_{\text{src}}^{\otimes k}
\otimes M_{\text{gov}}^{\otimes n}
\otimes M_{\text{coord}}^{\varnothing}
}
\]

The superscript \(\varnothing\) marks a factor omitted when not configured. The factor \(\otimes_i M_i\) is the synchronous product of session, model, sources, and the governance and observability families. It steps on every symbol of the envelopes.

| Factor | Required? | Notes |
|--------|-----------|-------|
| \(M_{\text{transport}}\) | Optional | Multi-agent messaging |
| \(M_{\text{session}}\) | Typical | History, checkpoint, `session_id` |
| \(M_{\text{obs}}^{\otimes m}\) | At least 1 | All observers equivalent |
| \(M_{\text{ctx}}\) | Yes | Assembler and manager (§7) |
| \(M_{\text{DP}}^{(p)}\) | Yes | One pattern \(p\) from the sum |
| \(M_{\text{model}}\) | Yes | LLM, embeddings |
| \(M_{\text{src}}^{\otimes k}\) | At least 1 | Unified context sources (§6) |
| \(M_{\text{gov}}^{\otimes n}\) | 0 or more | Budget, sandbox, policy |
| \(M_{\text{coord}}\) | Optional | Delegation, fan-out |

The loop around the product is:

\[
M_{\text{agent}} = \mu\,\text{turn}.\;
\mathcal{E}_{\text{ctx}} \circ \text{sched}\bigl(M_{\text{DP}}^{(p)}\bigr) \circ \bigl(\otimes_i M_i\bigr),
\qquad \forall e:\ \text{wrap}(e)=\sigma_1\circ\cdots\circ\sigma_7
\]

\(\text{sched}(M_{\text{DP}})\) emits sequences of \(\mathcal{E}_{\text{call}}\) envelopes. Every contract in `mas.runtime.contracts` is one of: a factor \(M_i\), a source in \(M_{\text{ctx}}\), a governance or observability summand, a ⊔ variant (DP, coordination, CM), or an I/O boundary (surface, evaluation pipeline). §11 maps each contract.

**Not separate factors:**

- `ToolMachine` and `MemoryMachine` as capability dimensions. They are context sources (§6) when the role is "query to `ContextPart`", and still use \(\mathcal{E}_{\text{call}}\) when executing.
- `TransformationMachine`. It is absorbed into the \(M_{\text{ctx}}\) filtering phase (RBAC, truncation).
- `ContextManagerContract`. It is a sub-phase of \(M_{\text{ctx}}\) (§7).

## 5. Dimension roles

| Group | Machine | Contract | Role |
|-------|---------|----------|------|
| Infrastructure | Transport | `TransportContract` | Deliver inter-agent messages |
| Infrastructure | Session | `SessionContract`, `ExecutionSessionContract` | Durable turns, checkpoint |
| Infrastructure | Observability (×m) | `RecorderContract`, OTel plugins | Observe everything (§8) |
| Orchestration | DP (one of ⊔) | `DesignPatternContract` | Schedule iterations; emit THINK/ACT/FINALIZE |
| Orchestration | Coordination (optional) | `CoordinationContract` | Multi-agent topology |
| Capability | Model | `ModelAccessContract` | `complete()`, embeddings |
| Capability | Context | `ContextContract` + assembler + manager | Build `messages[]` and provenance (§7) |

Memory, tools, and skills are context sources (§6), not separate categories. Governance instances (×n) are:

| Plugin example | Authorize / validate behaviour |
|----------------|--------------------------------|
| Budget | authorize: token and cost limits; validate: usage recorded |
| Sandbox | authorize: tool and path allow-list |
| Routing / policy | authorize: model and route constraints |

All governance machines must pass on symbols 2 and 6 for the call to proceed.

## 6. Context sources

### 6.1 One semantic kind, three calling paths

Tools, memory, and skills are context sources. They answer queries and return context (`ContextPart` or message-shaped content). They differ in calling semantics and provenance, not in automaton kind.

| Source | Typical `source_type` | `mechanism` | Initiator | When |
|--------|----------------------|-------------|-----------|------|
| Role / prompt template | `prompt` | `inject` | Runtime | Pre-LLM collect |
| DP instructions | `dp` | `inject` | Runtime | Pre-LLM collect |
| Skill catalog (facet) | `skill` | `inject` | Runtime | Pre-LLM collect |
| Tool directory (facet) | `tool` | `inject` | Runtime | Pre-LLM collect |
| Working memory (default feed) | `memory` | `inject` | Context manager | Pre-LLM assemble |
| Semantic / vector memory | `memory` | `rag` | Context manager | Pre-LLM collect |
| Full source catalog | `skill` / `agent` / `tool` | `inject` | Plugin config | Pre-LLM collect |
| `consult_skills` tool | `skill` | `tool_call` | LLM | Mid-loop |
| `memory_read` / working-memory tools | `memory` | `tool_call` | LLM | Mid-loop |
| Deterministic calculator, etc. | `tool` | `tool_call` | LLM | Mid-loop |
| RAG tool (retrieval tool) | `memory` / `rag` | `tool_call` | LLM | Mid-loop |

Provenance is uniform. Every contribution is a `ContextPart` with `ContextProvenance` (`mechanism`, `actor`, `source_type`, `source_id`, `via`, `semantic_role`). Whether retrieval ran in the assembler before the call or a tool fetched chunks after it, the same observability path records a context segment with the appropriate `mechanism`.

### 6.2 Source machines in the product

A context source is a plugin that implements `ContextContract.collect_context()` and optionally `ToolContract.call()` for the stochastic path. Structurally \(M_{\text{src}}^{\otimes k} = M_{\text{src}_1} \otimes \cdots \otimes M_{\text{src}_k}\).

On `CTX_COLLECT_EXECUTE`, each source machine either emits `EMIT_PARTS` (a list of `ContextPart`) or `NO_OUTPUT` if it does not contribute this turn.

On the stochastic path, the LLM emits a tool call, \(\mathcal{E}_{\text{tool}}\) runs, and the result is appended to `messages[]`. A second context pass, `CTX_COLLECT_POST_TOOL` (or collect with `step` > 0), merges tool-returned text into the same provenance model with `mechanism=tool_call`. "RAG before" and "tool after" differ only in when collect runs and who triggered it.

### 6.3 Three memory layers

"Working memory" names three distinct layers. Confusing them causes double injection (the same text in `messages[]` and in a `ContextPart`) or missing context across turns.

| Layer | Store | Holds | Reaches the LLM through |
|-------|-------|-------|-------------------------|
| **L1 transcript** | `SessionState.history`, live `messages[]` | User, assistant, and tool turns of prior steps | Session and **ContextManager** (`manage_history`), not `collect_context` |
| **L2 structured stores** | Working-memory KV (`working_memory_*` tools), `MemoryContract`, `MEMORY.md`, blackboard | Facts, embeddings, session KV, workspace files | Re-read each step through `collect_context` (`inject` / `rag`), or on demand through the `memory_read` tool |
| **L3 pattern state** | `DPState.memory` | Plan steps, executed results, failures, ToT tree | Control only; fragments surface through DP `collect_context` (phase-dependent instructions) |

State lifetimes in code:

- `AgentState`: per run, one LLM step. A design-target concept; the shipped kernel keeps per-turn state in `QProduct`.
- `SessionState`: durable per contact or channel; survives `handle_task` invocations.
- `ExecutionSession`: Mealy-layer checkpoint (`dp_state`, token usage, in-flight loop).
- `DPState.memory`: pattern-internal; orthogonal to Mealy lifecycle states.

**Store once, project many, track always.** Content lives in the right store. \(M_{\text{ctx}}\) projects a fresh LLM-facing view each step. Every projected part carries provenance, and every eviction and control signal is logged.

**What is re-injected on every call**

| Material | Re-collected each `collect_context`? | In `messages[]`? |
|----------|--------------------------------------|------------------|
| DP phase instructions | Yes (phase-dependent) | No, system band |
| Tool catalog descriptions | Yes (cached if static facet) | No, `SYSTEM_TOOLS` |
| RAG memory hits | Yes (query-dependent) | No, `SYSTEM_MEMORY` |
| Prior user / assistant turns | No | Yes, via session and CM |
| Tool results this turn | Appended after each tool | Yes, `role=tool` messages |
| Parsed plan, executed steps | No (L3 `DPState.memory`) | Only if synthesis prompts reference them |

**Dedup rules**

| Content already in... | Do not also... |
|-----------------------|----------------|
| `messages[]` (recent assistant / tool turns) | RAG-retrieve identical chunks into `SYSTEM_MEMORY` |
| Working-memory KV injected last turn, unchanged | Re-inject the full snapshot (prefer delta, pin, or cache) |
| Static facets (`TOOLS.md`, role, DP protocol) | Worry about duplication: re-collect is fine, `ContextFacetProvider` caches within a session |

Re-running `collect_context` before every LLM step re-projects stores onto the prompt; it does not copy them into a new blob. The transcript is already in `messages[]` (CM carries it forward), static sources cache, and RAG re-queries with `CollectContextRequest.query` each turn. `RetentionPolicy` and `WorkingMemoryAnnotation` verdicts (`retain`, `stale`, `low_score`, `policy_pin`, `dropped`) govern memory-store items at assembly time.

Bounding is policy, not architecture. `SummarisingConversation` (CM) compresses history (L1) and not necessarily L2 stores. `MaxTokenStrategy` evicts parts by priority. The automaton requires explicit provenance when content is dropped (`_evicted_parts` to observability), not a particular summarisation strategy.

### 6.4 Tool dual channel

Tools take part in two orthogonal channels from the same registry (`collect_tools`):

```text
ToolContract.on_collect_tools()  ->  registry
                  |-> ContextPart.tools()  -> prompt catalog   (SYSTEM_TOOLS)
                  '-> available_tools[]    -> ModelAccessContract.complete() schemas
```

| Channel | Payload | Placement / API | Purpose | Tracked as |
|---------|---------|-----------------|---------|------------|
| Prompt catalog | Names and descriptions (text) | `ContextPart` in the `SYSTEM_TOOLS` band (priority 30-39) | Planning, DP protocols, prose tool choice | `context_part_contributed`; `mechanism=inject`, `source_type=tool` |
| Execution catalog | JSON schemas for function calling | `available_tools` parameter of `ModelAccessContract.complete()` | Native `tool_calls` dispatch when the DP allows | Governance envelope on `llm_execute`; not a context segment |

The DP is one context provider (`SYSTEM_PATTERN`) and the tool catalog is another (`SYSTEM_TOOLS`). Neither replaces the execution catalog. Planning needs descriptions in the prompt. Execution needs schemas for the runtime, not ad-hoc model tool calls during planning.

Contributors to the prompt channel are `ContextFacetProvider` and the `TOOLS.md` workspace file (to `SYSTEM_TOOLS`), and the DP's `_get_dp_instructions(available_tools)` (phase-specific protocol at `SYSTEM_PATTERN`). A dedicated tool-catalog provider is the design target and is not yet a default plugin; Plan-Execute inlines tool names in its DP instructions today:

```python
class ToolCatalogContextProvider(ContextContract):
    def collect_context(self, request):
        tools = request.metadata.get("available_tools") or registry.collect_tools()
        return [ContextPart.tools(render_descriptions(tools), source="tool_catalog")]
```

The DP then references the catalog in its protocol text without duplicating `_build_tools_block` logic.

The DP, not the assembler, controls the execution channel:

| DP / phase | Prompt catalog | `available_tools` API |
|------------|----------------|-----------------------|
| ReAct DECIDE | Optional (`SYSTEM_TOOLS`) | On |
| Plan-Execute PLAN / REPLAN | On (names for the JSON plan) | Off (`THINK_TEXT_ONLY`) |
| Plan-Execute ACT (between steps) | n/a, no LLM call | n/a, runtime `tool_call` from the plan |
| Plan-Execute synthesis | Instructions only | Off |
| CoT / ToT THINK | As configured | Pattern-specific |

### 6.5 Division of labour: injection and tracking

| Responsibility | Owner | Mechanism |
|----------------|-------|-----------|
| What to inject (CoT protocol, plan JSON format, RAG chunks, role text) | DP, memory bridge, facets, workflow injector | `collect_context()` returns `ContextPart` with `ContextProvenance` |
| Assembly and provenance ledger | `ContextAssemblerPlugin` (sub-phase of \(M_{\text{ctx}}\)) | `collect_results("collect_context")`, order, budget, write `messages[]`, `_context_segments` |
| History compression | `ContextManagerContract` (sub-phase of \(M_{\text{ctx}}\)) | `manage_history(past_turns)`; never mutates `ContextPart`s |
| Execution-surface control | DP (`on_pre_llm_call` today, `DPMachine` output in the target) | Strip or pass `available_tools`; not context text |

DP subclasses override `_get_dp_instructions()`, not `collect_context()`. The resulting part looks like:

```python
ContextPart(
    placement=SYSTEM_PATTERN,
    provenance={"mechanism": "design_pattern_injection", "source_type": "design_pattern", ...}
)
```

RAG and memory plugins use the same pipe with `mechanism=rag` or `inject`. The assembler is the single choke point for what entered the prompt this step. Mutating `available_tools` is the one exception: it is a call-envelope control signal (`THINK_TEXT_ONLY`), audited through model pre-call governance and observability, not merged into system-message segments.

## 7. Context machine

The codebase has two classes, and the product model treats both as sub-phases of one \(M_{\text{ctx}}\):

| Component | Class | Responsibility |
|-----------|-------|----------------|
| Context manager | `ContextManagerContract` | Transform past conversation turns (`manage_history`) |
| Context assembler | `ContextAssemblerPlugin` | Collect parts, order, budget, write `messages[]` |

Legacy names stay in code (`ContextAssemblerPlugin`, `CMFactory`); architecturally they are transitions inside \(M_{\text{ctx}}\).

### 7.1 States

```text
IDLE
  -> FILTER_HISTORY  (ContextManager: manage_history on past turns)
  -> COLLECTING      (all ContextContract.collect_context on CTX_COLLECT_EXECUTE)
  -> FILTER_PARTS    (ContextStrategy: token budget, eviction)
  -> ASSEMBLING      (placement ordering, write messages[])
  -> EMIT_PROVENANCE (_context_segments, context_part_contributed)
  -> DONE -> IDLE
```

The implementation may interleave the budget check and DP control signals. `ContextAssemblerPlugin` runs the history filter before collect. The DP `THINK_TEXT_ONLY` control applies to the model envelope after assembly, on the hook bus or through `DPMachine` output.

### 7.2 Envelope and filter phases

\[
\mathcal{E}_{\text{ctx}} =
\texttt{ctx\_collect}_{s,x,e} \circ \texttt{ctx\_filter}_{s,x,e} \circ \texttt{ctx\_assemble}_{s,x,e}
\]

where \(s, x, e\) stand for `_start`, `_execute`, `_end`. Each `_start` / `_end` pair is wrapped by Obs\*, and by Gov\* if policy applies to context content (for example PII rules on injected memory).

| Phase | Class | Input | Output |
|-------|-------|-------|--------|
| History filter | `CMFactory` / `ContextManagerContract` | Past user and assistant turns (excluding the live user turn) | Trimmed or summarised history |
| Collect | All `ContextContract` plugins | `CollectContextRequest` (query, session_id, step, `available_tools` metadata) | `ContextPart[]` |
| Parts filter | `ContextStrategy` / `MaxTokenStrategy` | All parts | Trimmed parts and `_evicted_parts` |
| Assemble | `ContextAssemblerPlugin` | Parts, history, live user turn | `messages[]`, `_context_segments`, `context_part_contributed` events |

`ContextAssemblerPlugin.on_pre_llm_call` runs in this order:

1. Budget check (typed `BudgetContract`).
2. Conversation strategy on history.
3. `collect_results("collect_context")` for DP, RAG, facets, memory bridge.
4. Strategy filter and assembly into `messages[]`.
5. `post_context_assembly` for observability.

DP tool-stripping in `on_pre_llm_call` runs on the hook bus (transform role), ideally before the assembler passes `available_tools` into `CollectContextRequest.metadata`. Tool names are then visible to `_get_dp_instructions` while the native API tools are cleared.

### 7.3 Why one machine

- One machine owns the provenance outputs `_context_segments`, `_evicted_parts`, `_summarized_turns`.
- One ordering policy covers history compression and part injection. Summary blocks are themselves `ContextPart`s.
- One collect semantics serves pre-LLM and post-tool passes: the same graph runs again with updated `TransitionContext.messages` and `step`.

### 7.4 Placement bands

All injectors are peers at collect time. Order is by `ContextPlacement` band, then `priority` (see the placement table in `context_contract.py`). The assembly policy resolves conflicts, not injector precedence.

```text
SYSTEM_HEADER   identity, role
SYSTEM_PATTERN  DP instructions                 <- DP collect_context
SYSTEM_AGENTS   sub-agent directory
SYSTEM_TOOLS    tool catalog                    <- facet / tool-catalog provider
SYSTEM_SKILLS
SYSTEM_ONTOLOGY
SYSTEM_BODY
SYSTEM_MEMORY   RAG / episodic inject           <- MemoryContextPlugin
-- conversation history (CM-filtered) --
-- current user turn --
```

### 7.5 One LLM step, end to end

1. DP emits `COLLECT_CONTEXT` or `THINK`.
2. \(M_{\text{ctx}}\): CM, collect, filter, assemble, emit provenance.
3. DP applies `THINK_TEXT_ONLY` or `THINK_WITH_TOOLS` on the model envelope.
4. \(\mathcal{E}_{\text{llm}}\): governance, `model.complete(messages, tools=...)`, governance validate.
5. `DP.next_action(llm_response)` returns `ACT`, `THINK`, `FINALIZE`, or an internal action.
6. If `ACT`: \(\mathcal{E}_{\text{tool}}\), append the tool message, optionally run a post-tool \(M_{\text{ctx}}\) collect.
7. `iteration_end`.

What gets written where:

| Event | Written to | Notes |
|-------|------------|-------|
| LLM response | `messages[]` assistant turn | Model span; `post_llm_call` |
| Tool result (in loop) | `messages[]` tool turn | Target: `mechanism=tool_call` if re-collected; tool span |
| End of `handle_task` | `SessionState` | Today: user turn and final assistant turn only |
| Full tool trace across turns | Policy choice | OTel keeps the intra-turn trace; session reload omits tool turns unless policy is extended |

The post-tool pass runs \(M_{\text{ctx}}\) again with `step > 0` and updated `messages[]`. Tool-returned text then enters the same provenance model as pre-LLM RAG (`mechanism=tool_call`, `via=collect_context`), or stays transcript-only if already in `messages[]` (dedup, §6.3).

## 8. Observability and governance

### 8.1 Observability monitors governance

On `governance_authorize` (symbol 2), each \(M_{\text{gov}_i}\) steps to an allow or deny decision, and each \(M_{\text{obs}_j}\) steps to an `EMIT_EVENT` recording each decision (machine id, policy id, allow/deny, rationale code). `governance_validate` (symbol 6) works the same way after execution. Observability therefore covers governance before and after LLM, tool, and (if governance is configured for `ctx`) context-assembly calls.

### 8.2 Invariants

| Invariant | Statement |
|-----------|-----------|
| Obs coverage | For every σ in the envelope and every j, \(M_{\text{obs}_j}\) fires on σ |
| Gov coverage | For every critical call and every i, \(M_{\text{gov}_i}\) fires on `governance_authorize` and `governance_validate` |
| Gov denial | A deny on symbol 2 aborts the call before `execute_fn`; a deny on symbol 6 rejects the result |
| Obs equivalence | No observer is downstream of another; order is registration order only |
| Fail-open obs | An observer error isolates to its own `ERROR` state and never blocks governance or capabilities |

### 8.3 Span structure (conceptual)

```text
span: agent.turn
  span: context.collect
    event: context.part {provenance...} x N
    event: context.evicted x M
  span: model.generate
    event: governance.authorize
    event: governance.validate
    event: model.tokens
  span: tool.execute (x k)
    event: governance.authorize
    event: governance.validate
    event: context.part {mechanism: tool_call}
```

## 9. Design patterns as schedulers

\(M_{\text{DP}}^{(p)}\) states map to phases (`DPPhase`: DECIDE, PLAN, ACT, OBSERVE, FINALIZE, WAIT_EVENT, ...). Phases are orthogonal to the runtime lifecycle states (`IDLE`, `REASONING`, `ACTING`). Changing the pattern changes only \(M_{\text{DP}}^{(p)}\) and the scheduler edges; the product formula is unchanged.

### 9.1 Generic iteration

```text
iteration_start
  DP -> emit COLLECT_CONTEXT
  E_ctx
  loop:
    DP -> emit THINK | ACT | FINALIZE | WAIT
    if THINK:    E_llm(model);  DP consumes LLM_RESPONSE
    if ACT:      E_tool(source); E_ctx' (merge result, same provenance); DP consumes TOOL_RESULT
    if FINALIZE: break
  session.save_turn
iteration_end
```

### 9.2 ReAct and the other patterns

| ReAct step | DP phase | Product envelopes |
|------------|----------|-------------------|
| Reason | DECIDE, THINK | \(\mathcal{E}_{\text{ctx}}, \mathcal{E}_{\text{llm}}\) |
| Tool use | ACT | \(\mathcal{E}_{\text{tool}}, \mathcal{E}_{\text{ctx}'}\) |
| Observe | OBSERVE | context merge only |
| Answer | FINALIZE | session persist |

Registry names and scheduler profiles of the OSS patterns:

| Pattern | Registry name | Phases | `THINK` | `ACT` |
|---------|---------------|--------|---------|-------|
| ReAct | `react` | DECIDE, ACT, OBSERVE | LLM reasoning | Tools, skills, memory tools; native `tool_calls` adapter allowed (§9.5) |
| CoT | `cot` | DECIDE loop, FINALIZE | Chain-of-thought LLM steps | Rare or none |
| Plan-Execute | `plan_execute` | PLAN, ACT, (REPLAN), synthesis, FINALIZE | Plan / replan / synthesis, text-only | Runtime executes the plan via `DPAction(tool)` |
| Tree of Thoughts | `tree_of_thoughts` | EXPAND, EVALUATE, BACKTRACK | Multiple LLM samples | Optional |
| Introspection | `introspection` | REFLECT, CRITIQUE | Self-critique | Optional |

Event-driven and accumulator patterns (sensor-fed, not in every lab) use `WAIT_EVENT`, `COLLECT`, `READY` through `event_loop_phase_machine` and `accumulator_phase_machine`. `EventLoopDP` goes from FINALIZE to `WAIT_EVENT` instead of ending the agent, and the session machine stays `ACTIVE`. CoT skips the ACT branch until FINALIZE and adds THINK iterations through `should_continue`.

Reference implementations: `library-standard/src/mas/library/standard/plugins/design_patterns/react.py` and `plan_execute.py`. Integration tests: `runtime/tests/integration/test_design_pattern_plugins_integration.py`. Lab examples: `pattern-cot.yaml`, `pattern-tree-of-thoughts.yaml`, `pattern-reflection.yaml`.

### 9.3 ReAct versus Plan-Execute

| Aspect | ReAct | Plan-Execute |
|--------|-------|--------------|
| Who picks the next tool | Model, every THINK step | Model only in PLAN / REPLAN (JSON); runtime in ACT |
| Native function calling | On every THINK | Off during PLAN, REPLAN, synthesis |
| Tool awareness when planning | Native API plus optional `SYSTEM_TOOLS` text | Tool names in the prompt (`_get_dp_instructions` or catalog facet) |
| Loop shape | DECIDE, ACT, OBSERVE, DECIDE, ... | PLAN, ACT (sequential tools, no LLM between steps), synthesis THINK, FINALIZE |
| Tool error recovery | Stay in DECIDE, inject feedback | REPLAN (Plan-Execute does not handle DECIDE) |

Plan-Execute sequence:

```text
PLAN (THINK, text-only)
  collect_context: PLAN-AND-EXECUTE protocol + tool names; available_tools = []
  LLM -> JSON {"plan": [{"tool": "...", "arguments": {...}}, ...]}
  next_action: parse plan -> DPAction(tool) for step 0
ACT loop (no LLM between steps)
  for each plan step: execute_contract_call("tool", ...)
  on_action_result -> next DPAction(tool), or the synthesis flag
on tool error (if allow_replan): REPLAN -> THINK text-only -> new plan
synthesis (THINK, text-only) -> LLM final prose -> FINALIZE
```

ReAct sequence: loop { THINK with tools: \(\mathcal{E}_{\text{ctx}}\), then \(\mathcal{E}_{\text{llm}}\)(`available_tools` = full catalog); if `tool_calls` then ACT, \(\mathcal{E}_{\text{tool}}\), append tool message, OBSERVE; else FINALIZE }.

| Behaviour | Plugin (`plan_execute.py`) | Kernel (`RuntimeKernel` + driver) |
|-----------|-------------------------------|-----------------------------------|
| Clear native tools in PLAN | `collect_context` / phase | Envelope σ₃ with DP state |
| Parse JSON plan | `_parse_plan` in PLAN phase of `_evaluate` | `DPOutputResolver` (target) |
| Execute plan steps | `_schedule_plan_step` (target: `DPAction(action_type="tool")`) | Tool symbol σ₅ |
| REPLAN on tool error | REPLAN phase output is parsed; entering it on error is target (`allow_replan`) | DP phase transition |
| Synthesis LLM call | `SYNTH` phase (`dp_data["phase"]`) | Model symbol σ₄ |

Plan-Execute runs through the same product formula as ReAct. Parity checks: `runtime/tests/test_plan_execute_plugin.py` and the golden labs.

### 9.4 `DPPhaseMachine` versus `DPMachine`

| Construct | Layer | Purpose |
|-----------|-------|---------|
| `DPPhaseMachine` | L1 formal graph | Declarative phase FSM (`DPPhase` edges); liveness check at `DPFactory.register`; `to_mealy_machine()` for model checking |
| `DesignPatternContract` | L3 plugin API | `start`, `next_action`, `on_action_result`, `should_continue`, `finalize`, `collect_context` |
| `DesignPatternPlugin` | Shipped plugin API | `handle_event`, `on_user_input`, `on_context_complete`, `on_evaluate`, `evaluate_next`; phase kept in `QProduct.dp_data` |
| `DPMachine` (stub) | L1 product slot | Product-integrated scheduler emitting `THINK` / `ACT` / `FINALIZE` |

\(M_{\text{DP}}^{(p)} = \text{realize}(\text{DPPhaseMachine}_p, \text{DPContract impl}_p)\). The phase machine is the spec (reachable phases, legal edges). `next_action()` is the implementation and must respect the spec (`ValidatedDP` enforces it at runtime). `DPMachine` is the adapter that reads plugin outputs and emits kernel commands. Built-in phase machines in `dp_phase_machine.py`: `react_phase_machine`, `event_loop_phase_machine`, `accumulator_phase_machine`, plus `get_phase_machine()` on each plugin class.

### 9.5 ReAct adapter for native `tool_calls`

ReAct can be realised two ways, both the same \(M_{\text{DP}}^{(\text{react})}\):

| Path | When |
|------|------|
| Formal | `ReactPlugin.next_action()` emits `ACT` with tool name and args |
| Adapter | After \(\mathcal{E}_{\text{llm}}\), map `response.tool_calls` to `ACT` and populate `TransitionContext` |

The adapter lives inside the ReAct summand or a thin `DPOutputResolver`, not in the kernel loop. CoT, ToT, and Plan-Execute never use it; they require a formal `next_action()`.

### 9.6 Swapping the pattern (manifest only, once the kernel is migrated)

1. Set `spec.design_pattern.type` to a registry name (`react`, `cot`, ...).
2. Keep pattern-specific overlays to the DP and prompt parts.
3. Check the `DPFactory` liveness log at startup (FINALIZE reachable).
4. Run `runtime/tests/integration/test_design_pattern_plugins_integration.py` for the pattern.
5. Run the affected lab experiment with the same `--flavour` and `--infra` as before.
6. Compare OTel and events JSONL: the span kinds match, step counts may differ.

Context sources, governance ⊗ⁿ, observability ⊗ᵐ, and session need no change.

## 10. Alphabet and shared tape

**Global input alphabet**

- Lifecycle: `agent_start`, `agent_end`, `iteration_start`, `iteration_end`, `shutdown`, `interrupt_signal`, `contract_error`.
- Context: `ctx_collect_{start,execute,end}`, `ctx_filter_{start,execute,end}`, `ctx_assemble_{start,execute,end}`, and optionally `ctx_post_tool_collect_*` (or reuse collect with metadata).
- Per capability \(c \in \{\text{model}, \text{tool}, \text{memory}, \text{transport}, \ldots\}\): `{c}_{op}_start`, `{c}_{op}_execute`, `{c}_{op}_end`, `governance_authorize`, `governance_validate`, `observability_pre_execute`, `observability_post_execute`.
- DP-internal (may map to global symbols): `user_message`, `llm_response`, `tool_result`, `plan_ready`, `sensor_event`.

**TransitionContext.** The product shares a context tape that is not part of Mealy state:

```python
TransitionContext:
  messages: List[Message]       # current LLM-facing list
  parts: List[ContextPart]      # collected this turn
  provenance_log: List[...]     # append-only for obs
  session_id, step, task_id
  metadata: { result, tool_name, query, ... }
```

Machines read and write the tape through guarded transitions. Determinism holds with respect to machine outputs given (state, σ, tape); the impurity of `execute_fn` is outside.

## 11. Contract registry

Every contract exported from `mas.runtime.contracts` (and adjacent factories) fits the product model without special cases.

### 11.1 Capability contracts

| Contract ID | Class | Machine / envelope | Role |
|-------------|-------|--------------------|------|
| `context` | `ContextContract` | \(M_{\text{ctx}}\) collect and source ⊗ | Contributors emit `ContextPart`: facets, memory bridge, DP instructions |
| `context_manager` | `ContextManagerContract` | \(M_{\text{ctx}}\) filter-history sub-phase | `manage_history()`; not a separate factor |
| `tool` | `ToolContract` | Source (stochastic) and \(\mathcal{E}_{\text{tool}}\) | `call_tool` when the DP emits `ACT`; directory facet when `inject` |
| `memory` | `MemoryContract` | Source (rag/inject) and \(\mathcal{E}_{\text{memory}}\) | `read` / `write` / `query`; working-memory tools are tool-shaped sources |
| `model` / `llm` | `ModelAccessContract` | \(M_{\text{model}}\) and \(\mathcal{E}_{\text{llm}}\) | `complete()`, embeddings |
| `prompt` | `PromptContract` | \(\mathcal{E}_{\text{prompt}}\) or ctx collect | Template fetch; maps to `pre_prompt_build` hooks today |
| `session` | `SessionContract` | \(M_{\text{session}}\) | Turn log, `windowed_history`, contact metadata |
| `execution` | `ExecutionSessionContract` | \(M_{\text{session}}\) checkpoint sub-state | Mealy-layer `ExecutionSession` persistence |
| `recorder` | `RecorderContract` | One summand of \(M_{\text{obs}}^{\otimes m}\) | Low-level event sink |
| `sensor` | `SensorContract` | Transport / session ingress | `SensorEvent` to `USER_MESSAGE` / `sensor_event` |
| `control` | `ControlContract` | \(M_{\text{model}}\) and \(M_{\text{dp}}\) | See the steering modes below |
| `message` | `MessageContract` | \(\mathcal{E}_{\text{transport}}\) | Simple send; subset of transport |
| `transport` | `TransportContract` | \(M_{\text{transport}}\) | Bus unicast / broadcast, delivery ack |
| `delegation` | `DelegationContract` | Tool and coordination | `DelegateTaskTool`: tool envelope and coordination dispatch |
| `shared_context` | `SharedContextContract` | Context source (`inject`) | Blackboard to `ContextPart`, `source_type=shared` |
| `gateway` | `GatewayContract` | Transport, session, surface | Persistent agent lifecycle, channel routing |
| `surface` | `SurfaceAdapter` | Outside the product (I/O boundary) | CLI / WebSocket envelopes to `USER_MESSAGE` / `user_output` |
| `dp` | `DesignPatternContract` | \(M_{\text{DP}}^{(p)}\) ⊔ summand | Turn scheduler only |
| `chat` | `ChatContract` | Surface and session (lazy) | OC-parity; optional summand |

`ControlContract` steering modes:

- `steer(preempt)`: keep the prefix, `M_model CALLING -> IDLE`, `M_dp -> CTX_BUILD`, same turn continues.
- `steer(replace)`: drop the prefix, `M_dp -> IDLE`, new turn.
- `steer(after)` / `enqueue_input`: queued, runs once the live decode finishes.
- `cancel_inflight`: drop the remainder, no follow-up.

`EvalContract` lives in `mas.lab.evaluation` (benchmark pipeline only; not stepped during `handle_task`).

### 11.2 Orchestration and governance contracts

| Contract ID | Class | Machine | Role |
|-------------|-------|---------|------|
| `workflow` | `WorkflowContract` (= `CoordinationContract`) | \(M_{\text{coord}}\) ⊔ variant | Topology driver: dynamic, supervised, sequential, graph |
| `coordination` | `CoordinationContract` | \(M_{\text{coord}}\) | Routing, fan-out, barrier, vote |
| `budget` | `BudgetContract` | `governance_budget` | Authorize / validate on LLM, tool, memory (token and cost ceilings) |
| `routing` | `RoutingContract` | `governance_routing` | Topology edges (future) |

Workflow plugins (`DynamicWorkflow`, `GraphWorkflow`, ...) select one coordination summand at MAS scope. Single-agent runs omit \(M_{\text{coord}}\).

Library-standard governance plugins map to governance machines (they are not separate contract types): `BudgetPlugin` to `governance_budget`, `GuardrailPlugin` to `governance_guardrail`, `HITLPlugin` to `governance_hitl` (may gate the tool machine's `WAITING_HITL`), and `GuardBundle` as a meta-registration of several. The circuit breaker is not a governance machine: it is a library plugin (`type: circuit_breaker`, default `threshold`) that wraps engine I/O inside execute, at the same layer as infrastructure retry.

### 11.3 Observability plugins

Each observability plugin is one \(M_{\text{obs}_j}\). All implement `ObservabilityPluginBase` / `OBSERVABILITY_HOOKS`:

| Hook (today) | Product symbol(s) | Obs output |
|--------------|-------------------|------------|
| `on_pre_execution` | `agent_start`, `iteration_start` | `EMIT_SPAN` |
| `on_post_execution` | `iteration_end`, `agent_end` | `CLOSE_SPAN` |
| `on_post_context_assembly` | `ctx_assemble_end` | `EMIT_EVENT` and provenance segments |
| `on_post_llm_call` | `llm_execute`, `obs_post_execute` | `EMIT_EVENT` |
| `on_pre_tool_call` / `on_post_tool_call` | `tool_*` envelope | `EMIT_EVENT` |
| `on_pre_memory_read` / `on_post_memory_read` | `memory_read_*` | `EMIT_EVENT` |
| `on_pre_memory_store` / `on_post_memory_store` | `memory_write_*` | `EMIT_EVENT` |
| `on_governance_event` | `governance_authorize`, `governance_validate` | `EMIT_EVENT` |
| `on_pre_skill_execution` / `on_post_skill_execution` | `tool_execute` (skill tools) | `EMIT_EVENT` |
| `on_pre_agent_communication` / `on_post_*` | `transport_*` | `EMIT_EVENT` |
| `on_user_input` / `on_user_output` | surface to `USER_MESSAGE` / finalize | `EMIT_EVENT` |

Adding an observability plugin registers another \(M_{\text{obs}_j}\) and needs no kernel change. It sees the full symbol stream, including governance decisions.

### 11.4 Library-standard plugins as sources or backends

| Plugin category | Placement |
|-----------------|-----------|
| `RoleInstructionsProvider`, `FilePromptProvider`, `FewShotPlugin` | ctx collect, `inject`, `source_type=prompt` |
| `ContextFacetProvider`, `WorkflowPromptInjector` | ctx collect, `inject`, skills / agents / tools directories |
| `SkillPlugin`, `SkillEligibilityChecker` | ctx collect and `consult_skills` tool path |
| `MemoryContextPlugin`, vector / Letta overlays | ctx collect, `rag` or `inject` |
| `DefaultContextPlugin`, `ContextPolicyPlugin` | ctx filter and governance overlay on parts |
| `OpenAIModelAccess` | `execute_fn` behind \(\mathcal{E}_{\text{llm}}\) |
| `ToolProviderPlugin`, `LocalToolProvider`, `system_tools` | tool envelope and tool directory facet; target `ToolCatalogContextProvider` (§6.4) |
| `PlanExecutePlugin`, `ReactPlugin`, other DPs | ctx collect `SYSTEM_PATTERN` and DP scheduler (§9) |
| `LocalTransportPlugin`, `GRPCTransportPlugin` | \(M_{\text{transport}}\) |
| `DynamicWorkflow`, `GraphWorkflow`, ... | \(M_{\text{coord}}\) ⊔ |
| `CLISurfaceAdapter`, `WebSocketSurfaceAdapter` | Surface boundary to kernel events |

## 12. Multi-agent and I/O boundaries

**Coordination.** When `spec.agency` or the workflow topology is not single-agent, \(M_{\text{agent}} \otimes M_{\text{coord}}^{(w)}\) with \(w \in \{\text{dynamic}, \text{supervised}, \text{sequential}, \text{graph}, \text{async\_dynamic}\}\). Coordination emits `ROUTE`, `DISPATCH`, `BARRIER`, `COLLECT_VOTE`, `EMIT_RESULT`. Each dispatch wraps delegate calls in \(\mathcal{E}_{\text{tool}}\) or \(\mathcal{E}_{\text{transport}}\) with the same governance and observability envelopes. Topology labs: `design-space.lab/02-topologies` (supervised, linear-pipeline, parallel, moderator-broker, verifier).

| Mechanism | Contracts | Envelope |
|-----------|-----------|----------|
| In-process delegate | `DelegationContract` | `tool.execute` and sub-agent `handle_task` |
| Bus / gRPC | `TransportContract` | `transport_send`, `transport_receive` |
| Persistent channels | `GatewayContract` | Gateway lifecycle and transport |
| `SendToCallerTool` | Tool and surface | Returns through the parent session |

**Surfaces** are adapters outside the product. `SurfaceAdapter.recv()` calls `product.step(USER_MESSAGE)`, and `product.step(user_output)` calls `SurfaceAdapter.send()`. The CLI (`mas-ctl chat` / `run-mas`), WebSocket, and benchmark HITL all drive the same kernel.

**Sensor and control (reactive agents).** `SensorContract.pull()` yields `USER_MESSAGE` or `sensor_event`. `ControlContract.steer(mode="preempt")` gives `M_model CALLING -> IDLE` (prefix kept), `M_dp -> CTX_BUILD`, same turn continues. `steer(mode="replace")` gives `M_model CALLING -> IDLE` (prefix dropped), `M_dp -> IDLE`, new turn.

## 13. Hook equivalence

Migration removes `HookPlane` phase numbers. Every hook maps to product symbols, so governance and observability are systematic rather than ad hoc callbacks.

| Hook | Symbol sequence |
|------|-----------------|
| `pre_execution` / `post_execution` | `agent_start` / `agent_end` |
| `pre_context_assembly` | `ctx_collect_start` |
| `collect_context` | `ctx_collect_execute` |
| `post_context_assembly` | `ctx_assemble_end` and obs provenance |
| `pre_prompt_build` | `prompt_fetch_start` ... `prompt_fetch_end` |
| `pre_llm_call` (assembler) | `ctx_filter_*`, `ctx_assemble_*`, provenance emit |
| `pre_llm_call` (model gate) | `governance_authorize` and `observability_pre_execute` |
| `on_pre_llm_call` (DP control) | DP `THINK_TEXT_ONLY` / `THINK_WITH_TOOLS` on the model envelope |
| `post_llm_call` | `llm_execute`, `observability_post_execute`, `governance_validate` |
| `pre_tool_call` | `governance_authorize` (tool) and `observability_pre_execute` |
| `post_tool_call` | `tool_execute`, `observability_post_execute`, `governance_validate` |
| `pre_memory_read` / `post_memory_read` | `memory_read_*` envelope (7 symbols) |
| `pre_memory_store` / `post_memory_store` | `memory_write_*` envelope |
| `governance_event` | folded into `governance_authorize` / `governance_validate` |
| `user_input` / `user_output` | `USER_MESSAGE` / finalize emit |

**Equivalence claim.** For any hook sequence \(H\) used by agents and labs today, a symbol sequence in \(\Sigma^*\) exists such that stepping the product with the same plugins registered produces the same observable behaviour: messages to the LLM, tool results, session persistence, events JSONL, OTel spans, and governance denials.

## 14. Mutation matrix

Who can read and mutate what. Anything outside the listed paths is denied by default.

| Component | Reads | Mutates | Guarded resource | Tracking |
|-----------|-------|---------|------------------|----------|
| User / task | - | Initial user turn | `messages[]` | `role=user`; session store |
| DesignPattern | tools list, phase, `DPState` | `collect_context` parts; `available_tools` control; `DPState.memory` | Prompt and API tools parameter | `mechanism=design_pattern_injection`; phase in `DPState` |
| Memory / RAG | query, session | `collect_context` parts; `MemoryContract` read/write | Context window; external stores | `mechanism=rag` / `inject`; memory envelope |
| Tool catalog provider | `collect_tools` | `SYSTEM_TOOLS` part | Prompt | `source_type=tool` |
| ContextManager | past turns | `manage_history` only | L1 transcript | `_summarized_turns` |
| ContextAssembler | all parts | Merge into `messages[]`; eviction | Full prompt | `_context_segments`, `_evicted_parts`, `context_part_contributed` |
| Governance ⊗ⁿ | pre-call payload | Block or modify calls | LLM, tool, memory, ctx | `governance_authorize` / `validate` events |
| Observability ⊗ᵐ | all symbols | Emit spans only | Telemetry | JSONL / OTel |
| ToolContract | - | Execute; append tool messages (via kernel) | External systems | `tool_*` envelope |
| ModelAccessContract | assembled messages | LLM response | Tokens, budget | `post_llm_call` / model span |
| SessionContract | - | `append_turn`; `save_session` | Cross-turn L1 log | `session_id`-keyed store |
| Runtime kernel | all | Schedule loop; invoke envelopes | Step budget | `ExecutionSession`, `AgentState` |
| ControlContract | signals | `OperatorSteerReceived` to `model_on_preempt` and `M_dp` (`CTX_BUILD` / `IDLE`) | Loop continuation (preempt) or new turn (replace) | control events |
| SharedContext | blackboard | KV via async API | Multi-agent coordination | `source_type=shared` inject |

Invariants on context mutation:

1. No plugin other than the assembler writes the final `messages[]` system-band composition. Legacy `on_pre_llm_call` message mutation is deprecated.
2. Every `ContextPart` has `ContextProvenance` before assembly.
3. `available_tools` changes are control signals on the model envelope, logged by governance and observability even when not context segments.
4. Transcript appends (assistant, tool) carry call linkage (`tool_call_id`, `llm_call_id`) in the target kernel; this is partially implemented today.
5. Eviction is never silent: `_evicted_parts` and `context_compression` spans record it.

## 15. From formula to manifest and kernel

A conceptual agent manifest (illustrative):

```yaml
orchestration:
  dp:
    type: react          # selects M_DP^{(react)} from the sum

capabilities:
  context:
    conversation_manager: sliding-window   # CM summand
    context_strategy: max-tokens
    sources:
      - type: working_memory
        default_inject: true
      - type: semantic_memory
        mechanism: rag
      - type: skill_catalog
        mechanism: inject
      - type: tools
        mechanism: inject
        tools: [consult_skills, calculator, memory_read]
  model:
    provider: openai

governance:
  - type: budget
  - type: sandbox

observability:
  - type: otel
  - type: native   # second observer, equivalent coverage
```

`manifest_to_machines(config)` registers each plugin as one or more Mealy machines, and `create_agent_product(machines)` builds the product.

Kernel pseudocode (target runtime). It has no ReAct-specific branch, only DP outputs and envelopes:

```python
class AgentKernel:
    def __init__(self, product: MealyProductIntegration, dp: DPMachine):
        self.product = product
        self.dp = dp

    def handle_task(self, task):
        ctx = TransitionContext.from_task(task)
        self.product.step(Input.AGENT_START, ctx)
        while True:
            self.product.step(Input.ITERATION_START, ctx)
            out = self.dp.output  # DP machine output from the last step

            self._run_envelope("ctx", "collect", lambda: self._collect_sources(ctx))
            self._run_envelope("ctx", "filter", lambda: self._filter_context(ctx))
            self._run_envelope("ctx", "assemble", lambda: self._assemble_messages(ctx))

            action = self._resolve_dp(out)
            if action == "FINALIZE":
                break
            if action == "THINK":
                self.product.execute_contract_call(
                    "model", "generate", lambda: self.model.complete(ctx.messages), ctx)
            if action == "ACT":
                self.product.execute_contract_call(
                    "tool", "execute", lambda: self._execute_tool(ctx), ctx)
                # post-tool context merge: same M_ctx, mechanism=tool_call
                self._run_envelope("ctx", "collect", lambda: self._collect_post_tool(ctx))

            self.product.step(Input.ITERATION_END, ctx)

        self.product.step(Input.AGENT_END, ctx)
        return ctx.final_response
```

## 16. Shipped kernel and implementation map

The v0.1 OSS release ships a compact Mealy kernel under `mas/runtime/kernel/`, the envelope hot path, the `RuntimeInstance` driver, and ctl-owned bootstrap (`instantiate_runtime` / `SessionController`).

```text
mas/runtime/
  kernel/            RuntimeKernel: sole orchestration (§15)
  machines/          DP / gov / obs Mealy machines
  driver/            RuntimeInstance, run_turn, checkpoints
  factory/builder.py RuntimeBuilder embed entry
  contracts/         plugin contracts and factories
  boundary/          context assembly, skills injection
```

| Retired concept | Shipped replacement |
|-----------------|---------------------|
| Monolithic `runtime.py` loop | `RuntimeKernel` and product composition |
| Hook-plane phase numbers | Product symbols and envelope σ schedule (§3.4, §13) |
| `AgentBuilder` / `AgentRuntime` | `RuntimeBuilder` and `mas.ctl.SessionController` |
| Ad hoc governance in the LLM path | Governance machines on σ₂ / σ₆ |

Plugin classes, manifests, `DPFactory`, `CMFactory`, lab pipeline YAML, and overlay merge (`mas/v1` `Overlay` with `spec.patch`) are unchanged.

**Current versus target.** For the CI-enforced status see [mealy-envelope.md](mealy-envelope.md) §2.

| Concept | Target | v0.1 codebase |
|---------|--------|---------------|
| Product ⊗ | `GuardedProductComposer` over n summands | Shipped: `M_obs ⊗ M_gov ⊗ M_capability` via `get_product_composer()` |
| Governance family ⊗ⁿ | Separate Mealy per policy dimension | Partial: policy engine, egress chokepoint, single `GovEnvelopeMachine` |
| Observability family ⊗ᵐ | Separate Mealy per channel | Partial: `ObsEnvelopeMachine` and `ObservabilityOperator` |
| \(M_{\text{ctx}}\) unified | Single context machine | Shipped: context assembly before LLM egress; CM plugins |
| Sources unified | `collect_context` and tool path | Shipped: same `ContextPart` model |
| DP scheduler | `evaluate_next` / `handle_event` drives egress | Shipped: `RuntimeKernel` and DP plugins (`react`, `plan_execute`, ...) |
| Plan-Execute | Text-only plan, runtime tool execution | Shipped: `PlanExecutePlugin`; kernel integration tests |
| Context assembler | Collect, filter, assemble, provenance | Shipped: hot path before each `LLM_CALL` |
| Tool catalog facet | `collect_tools` to prompt facet | Partial: manifest tools; auto-bridge varies by flavour |
| Seven-symbol envelope | `execute_contract_call` / chokepoints | Shipped: TOOL and LLM hot paths via `egress_gate.py` |
| Provenance | `ContextProvenance` to obs | Shipped: `context_assembled` events |
| Session persistence | Policy-selected turn log | Partial: final turn in session; full trace in `events.jsonl` |
| Post-tool ctx pass | Post-tool `collect_context` | Partial: kernel supports it; not all labs enable it |
| Offline `ProductMealyComposer` | ⊗ materialisation for model checking | Not shipped (design reference, §3.1) |

**CI gates**

| Gate | Purpose |
|------|---------|
| `runtime/tests/test_mealy_envelope.py` | Envelope σ ordering |
| `runtime/tests/test_overlay_merge.py` | Canonical `mas/v1` overlays |
| `tests/test_golden_labs_run.py` | End-to-end lab parity |
| `ctl/tests/` | Compose, deployment, session |

**Lab coverage.** The kernel runs the labs without manifest or overlay changes, except optional DP type swaps.

| Lab / area | Stresses |
|------------|----------|
| `design-space.lab/01-design-patterns` | All OSS DPs: \(M_{\text{DP}}^{(p)}\), ctx, model, tool |
| `design-space.lab/02-topologies` | \(M_{\text{coord}}\), delegation, transport |
| `extensions.lab` | Memory overlays (vector, Letta, tool-memory): ctx sources, memory envelope |
| `lifecycle-control.lab` | Budget, guardrail, circuit breaker, governance-off: \(M_{\text{gov}}^{\otimes n}\), \(M_{\text{obs}}^{\otimes m}\) |
| `mas-ctl chat` | Surface, session, ctx, model, dp |
| `ctl` trip-planner / statemachine | Workflow agents: coordination, session, tool |
| Benchmark pipelines | OTel export, neo4j, service_start: outside the agent product |

`mas-lab benchmark` pipeline steps (`export_otel`, `neo4j_push`, `service_start`) and `EvalContract` metrics are outside the agent product. They consume artifacts that the observability family produces.

**Equivalence validation plan**

1. Golden traces: record events JSONL and OTel per lab scenario on the current runtime.
2. Replay: run the same manifests on the migrated kernel and diff span kinds and attributes (not timestamps).
3. Contract tests: the kernel, envelope, and design-pattern tests under `runtime/tests/` and `library-standard/tests/`.
4. Lab smoke: `--single-run` on each lab `experiment.yaml` in CI.
5. Governance: deny paths (budget exhausted, guardrail violation) raise the same exceptions.
6. DP bisimulation: `DPPhaseMachine` and `check_bisimulation` for pattern refactors.
