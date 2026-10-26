<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Context Management

This reference is the map for what the model sees, what the session keeps, and
how summarization works. Manifest knobs:
[context assembly](../manifests/context-assembly.md),
[summarization](../manifests/summarization.md).

## Two stores, one request

**Archive** — the full session log. Every user message, assistant message, and
tool call/result is a *leaf* chunk. When old leaves no longer fit in a model
call, a *summary* chunk is added that **points at** those leaves. Leaves are
never deleted. On a single branch, expanding every summary (nested summaries
allowed) plus the leaves still in the request must cover that branch. After a
backtrack, abandoned-branch leaves stay stored but are not in the live covering
set — see [Live backtrack](#live-backtrack-copy-on-write-heads).

**Request** — the OpenAI `messages[]` this LLM call sends. Internally the
runtime picks a covering *set* of archive chunks (summary nodes and/or
leaves), then **projects those chunks into chat messages**. The model never
sees chunk ids. A **system prefix** (role, catalog, activated skill text)
goes first. The set is chosen so request tokens + room for the answer stay
inside the model window (or a smaller `context_window` override).

A user (or operator) message is just another write to the same log. HITL,
steering, and a follow-up chat line are the same kind of write. Design-pattern
steps and protocol rounds (ACCORD) are not a second memory.

Live tool rows for the current user message sit in working memory until that
exchange is stored as an archive leaf. They are included in the request. There
is **no message-count cap**.

Each assembled LLM call is tagged with `archive_view_id` (hash of the covering
set). That id is telemetry on the native `context_assembled` event and on the
`processing_call_start` / `processing_call_end` pair, so a trace row can be
joined back to the covering set that produced the messages. It is not part of
the prompt.

Runs that never exceed the model window (request tokens plus completion
reserve) assemble the same `messages[]` as before this change. The removed
20-row working-memory count cap only affects in-turn tool loops that used to
clip.

## System prefix (KV cache)

The system prefix is rebuilt from plugins every call. If the bytes did not
change, `system_prefix_reused` is true on `inspect_context` / the last usage
snapshot. Keep this prefix stable: same catalog order, no timestamps, pinned
skill bodies in the same place.

## Generic chunks (providers, including skills)

The runtime must not know what a skill is. Every context provider — catalog
plugins, `activate_skill`, memories, the context manager — contributes
**chunks** with the same metadata:

| Field | Values | Used for |
| --- | --- | --- |
| `pin_policy` | `never` · `prefer` · `ephemeral` | Evict / fold / fail |
| `semantic` | `catalog` · `instruction` · `history` · `tool_result` · `summary` | Which projector to run (stub, summarize, keep) |
| `token_estimate` | integer | Budget |
| `source` / `section_id` | strings | Inspect, cache, telemetry |

`activate_skill` is a library system tool. It returns a normal tool result and
a `chunk` object (`pin_policy: never`, `semantic: instruction`,
`section_id: skills/activated/<name>`). Dispatch strips `chunk` from the
model-facing JSON and writes it on the tool message as `mas`. The assembler
and archive only read those fields. They do not parse `<skill_content>`.

Context managers fold the covering set (which leaves stay verbatim, which
become a summary). They may register a projector per `semantic` so
summarization can stub instruction bodies without naming skills. Assembly
then turns that set into `messages[]`.

### When the system prefix exceeds the window

Harnesses do **not** summarize the system role the way they summarize chat.
Typical practice:

- **Progressive disclosure** — catalog only (~hundreds of tokens); load a body
  on demand (Claude Code, Agent Skills, LangGraph write-ups).
- **After compact, re-inject** — Claude Code rebuilds catalog from disk, then
  re-attaches invoked skill bodies capped at 5 000 tokens each and 25 000
  total (oldest dropped; truncation keeps the start of the file).
- **Hard cap then fail** — OpenClaw renders skills as metadata, then a compact
  format, then a character cap; overflow compaction retries are bounded (3),
  then the request fails. Anthropic returns 400 if input already exceeds the
  window. OpenAI `truncation: auto` drops middle items (off by default).
- **Offload** — LangGraph writes huge tool results to files and leaves a path
  in the prompt. Sub-agents quarantine context.

mas-lab policy, in order, when **request + completion reserve** would exceed
the window:

1. Fold `ephemeral` covering entries (history, old tool results) into summary
   nodes.
2. Shrink `prefer` prefix parts (truncate or project by `semantic`).
3. Keep `never`. If the `never` set alone still exceeds the window, **fail the
   call** (do not send a prompt the provider will 400). `inspect_context`
   shows the fill.

Pinned-only overflow used to log a warning and send anyway. That is the case
this policy closes.

## Skills (library, not kernel)

`activate_skill` does two things, both via generic chunk metadata:

1. Copies `SKILL.md` into the system prefix (`<activated_skill>`, default on)
   as a `never` / `instruction` part.
2. Marks the archive leaf for that tool result `pin_policy: never` so a fold
   never drops it from the covering view.

The tool result is the skill body. A count cap used to drop the live tool
result after 20 rows; that cap is gone. `working_memory_messages` is accepted
and ignored.

## When summarization runs

Only when this request, including completion reserve (room for the answer),
would exceed the window. Then older covering entries become a summary node;
the newest user messages stay verbatim (`keep_turns` is that verbatim tail
when a fold happens, not a second budget). To summarize earlier, set a smaller
`spec.models[].context_window` (or infra `generation.context_window`).

## Archive size and where it lives

Default: **RAM only. No archive files.** Scaled experiments must not fill the
disk. Persist only when `mas_ctl.archive.enabled` is **true**. Then every
chunk is written under `traces/archive/` (needed for shallow checkpoints).
`paths.archive_dir` chooses the folder after enable; setting the path does
not turn persist on. RAM is a cache once on disk (schema
`memory_watermark_bytes`; folded leaves drop their in-memory payload). Path
and watermark are **execution artefacts**, like the trace — not agent spec:

```text
<run_root>/
  traces/events.jsonl
  traces/archive/          # every leaf and summary (when archive is on)
  checkpoints/             # when a checkpoint store is on
```

There is no separate XDG archive path. Colocation with the run's traces
folder is enough; `$XDG_CACHE_HOME/mas/traces` remains the content-addressed
*trace cache*, a different artefact. Disable the session archive to save
space (`mas_ctl.archive.enabled: false`): then checkpoints must be deep.

Configure from workspace `config.yaml` (not `spec.context_manager.params`):

```yaml
mas_ctl:
  archive:
    enabled: false          # schema default — do not fill the disk
    dir_name: archive       # traces/<dir_name> when enabled and events are on
    memory_watermark_bytes: 67108864
```

A lab/benchmark run root is the item output directory. Interactive chat
colocates next to the resolved events file **only after** `enabled: true`.
No persist means RAM-only archive and deep checkpoints only.

Agent YAML still owns `keep_turns`, `hysteresis_ratio`, `trimmer`, and
`spec.checkpoint.portability`.
`pkg://mas.library.standard/overlays/production-memory.yaml` only reviews those
history knobs — not the archive path. Harness overlays in
`pkg://mas.library.skills/overlays/harness-profiles/` pick the variant.

Inspect with `inspect_archive(session_id)` (full tree, including abandoned
heads) and `inspect_context` (window, fill, `archive_view_id`, prefix reuse).

`inspect_context` does not return prompt text. Traces with content tracing on
are sensitive.

## Model Window Resolution

An omitted `spec.models[].context_window` means **auto**. Resolution order is:

1. Explicit agent model `context_window` override.
2. Infra `generation.context_window` override.
3. The model catalog entry for the model actually resolved after infra model
   mappings.
4. The default fallback, 128,000 tokens, with a warning naming the unresolved
   model and the override field to use.

The built-in catalog records Gemini 2.5 Flash at 1,048,576 input tokens. A
compiled generic value is not written back as an explicit 128,000-token model
override: infra may remap `any` or an experiment model after compile. The
runtime therefore resolves the final window when the live engine is built.

MAS Lab does not make a network request to LiteLLM or another provider to
look up context metadata at startup. Known models use the local catalog; custom
or proxy-only model names should specify `context_window` on the model binding
or `generation.context_window` in infra. This keeps startup deterministic and
avoids an additional credentialed network dependency.

The completion reserve comes from the effective output-token budget, then the
model binding, then the documented 2,000-token fallback. A user can override
`max_tokens`, `context_window`, and the trimmer settings independently.

## Safety Margin and Hysteresis

The automatic budget accounts for the resolved window, completion reserve,
fixed system/context content, current input, working memory, and tool schemas.
It keeps a configurable safety margin for tokenizer approximation and provider
request overhead. The default `trimmer.safety_margin_ratio` is `0.1` (10% of the
resolved input window).

For automatic summarization, the soft trigger is lower than the hard
safety-bounded history limit by `1 + hysteresis_ratio`. With the default
`hysteresis_ratio: 0.1`, the summarizer has room to reuse the cached summary as
new turns accumulate, while its retrigger ceiling stays inside the hard budget.
The defaults are intentionally coordinated: margin protects the provider
boundary; hysteresis avoids changing the prompt on every turn and helps retain
LLM cache hits.

A positive `summary_threshold` sets an earlier absolute estimated-history
trigger. It is capped by the resolved safety-bounded budget. `trimmer.max_tokens`,
`trimmer.reserve_tokens`, and `trimmer.safety_margin_ratio` override the
corresponding assembly limits. Setting the margin to zero is possible but not
recommended with an estimated counter.

```yaml
spec:
  models:
    - id: main
      model: any
      max_tokens: 12000
      # context_window omitted: resolve from the runtime model
  context_manager:
    type: summarising
    params:
      keep_turns: 10
      hysteresis_ratio: 0.1
      trimmer:
        reserve_tokens: 12000
        safety_margin_ratio: 0.1
```

For a deployment whose provider has an authoritative window different from the
catalog:

```yaml
# LLMProxy or LLMLocal infra
spec:
  generation:
    context_window: 200000
```

  An infra owner can also override the catalog's per-million-token pricing for a
  proxy or negotiated deployment rate:

  ```yaml
  spec:
    generation:
      pricing:
        input_per_million_tokens: 0.30
        cached_input_per_million_tokens: 0.03
        output_per_million_tokens: 2.50
  ```

The full request is estimated with a conservative `characters / 4` heuristic
plus message/tool overhead. Provider-reported `prompt_tokens` is recorded after
the call and is the better measurement for calibration. The estimate includes
tool schemas and text messages, but is not a provider tokenizer; multimodal
payloads and model-specific tokenization can still differ. The final trim keeps
complete tool-call/result groups intact. If pinned context alone exceeds the
budget, it is retained and a warning is emitted rather than silently deleting
instructions.

## Inspecting Context Usage

Every assembled LLM call records a `context.assembled` event with an estimated
request total, token breakdown, context window, completion reserve, remaining
budget, and metadata-only context parts (`source`, `section_id`, `placement`,
`tokens`, `pinned`). The latest snapshot is also available through the control
contract and JSON-lines RPC:

```text
inspect_context(session_id)
inspect_archive(session_id)
```

The returned `ContextUsageView` includes:

- The latest request's resolved model/window, estimated prompt tokens, reserve,
  remaining budget, fill ratio, and token breakdown.
- `archive_view_id` and `archive_covering_ids` for the covering set used to
  assemble that call, plus `system_prefix_hash` / `system_prefix_reused`.
  Those ids are inspect/telemetry, not prompt text.
- Metadata for pinned and non-pinned context parts, without their text.
- The most recent provider usage report, when available, with
  `provider_usage_source` set to `provider`, `cached_response`, or `unavailable`.
- Request-level cache hit/miss counts and rate, plus raw probe counts by cache
  layer. A request passing through nested caches is counted once in the overall
  rate and once per probed layer in the layer breakdown.
- The cumulative estimated provider cost for this session, and
  `cost_status`. Response-cache hits add zero provider cost.

`inspect_archive` returns the full tree (leaves, nested summaries, whether each
leaf is in the request or only covered by a summary). It is the debugger view
of the archive; it is not sent to the model.

Inspection is read-only: it does not reassemble the prompt, call the model, or
change cache keys. The snapshot is the latest assembled request; before the
first model call it is unavailable.

## Live backtrack (copy-on-write heads)

Disk checkpoints still serialize working memory and the archive so a new
process can resume. In a **live** session, `/backtrack` and snapshot navigate
should not deepcopy every message.

The kernel already freeze-clones `QProduct` / `RunLedger`
([snapshots](snapshots.md)). Working memory already freezes a snapshot and
copies list shells on the next write. The archive matches that grain:

| Object | Shared | Frozen per snapshot | Cloned on write |
| --- | --- | --- | --- |
| `chunks{}` | yes (append-only) | payloads stay | never mutate a frozen leaf |
| covering `order` (id list) | no — one list per **head** | yes | clone the id list, then append |
| in-turn working memory | no | freeze bit | new unfrozen list (existing CoW) |
| kernel `q` / `run` | no | `CowKernel` | `writable()` clone |

A checkpoint or in-memory snapshot stores a **head**: `covering_ids`,
`archive_view_id`, last leaf id, and a pointer to the frozen WM snapshot.
Navigate / `/backtrack` sets `live` to that head — almost free once the
archive is on disk. New turns after the jump copy the covering id list
(O(n) pointers, not O(payload)) and append new chunk ids into the shared
map. Abandoned-branch leaves remain inspectable; they are not in the live
covering set.

Shallow disk checkpoints are the same head written next to the archive.
Deep checkpoints hydrate every pointer so the file is self-contained (no
archive required). See [Deep vs shallow](../manifests/checkpoint.md#deep-vs-shallow).

Coverage invariant **per head**: expanding that head's summaries and leaves
reaches exactly the leaves that belong to that branch. The store-wide
`covers_archive()` check is “union of heads covers every non-GC leaf”.

Process restart still uses the serialized snapshot (`restore_ctx` today). Live
backtrack uses the head pointer so we do not load a second copy of the same
bytes.

## Tokens, Cache, and Cost

MAS Lab records provider usage fields such as `prompt_tokens`,
`completion_tokens`, and provider-specific cached-token details when the
provider returns them. A response-cache hit is recorded separately, by cache
layer (`infra_llm_cache` or `provider_cache`). A strict infra cache miss is a
`cache.lookup` event, not a fabricated model completion. The hit rate is
computed per cache layer from observed lookups; write-only recording has no
lookup denominator.

A cached response may carry the usage from the original live request. It is
useful for context analysis but is **not** new billable usage. Provider prompt
caching is different: the request still reaches the provider, and cached input
tokens are represented in provider usage if supported.

The model catalog can store versioned pricing rates, and infra
`generation.pricing` can override them. As of 2026-10-06, the catalog records
Vertex AI Gemini 2.5 Flash text rates from the
[Vertex AI pricing page](https://cloud.google.com/vertex-ai/generative-ai/pricing):
$0.30/M uncached input, $0.03/M cached input, and $2.50/M output. This is an
estimate: a LiteLLM proxy may use another region, account discount, or markup;
set the infra override to model that deployment. Models without known rates
remain unpriced (`cost_status: pricing_or_usage_unavailable`).

For live provider calls, estimated cost uses:

```text
uncached_input_tokens × input_rate
+ cached_input_tokens × cached_input_rate
+ output_tokens × output_rate
```

Response-cache hits have zero upstream model-call cost; provider prompt-cache
hits use the configured cached-input rate. Cost is cumulative over recorded
`LLM_CALL` returns for the session, not a billing invoice. Exact invoices may
differ due to region, tier, discounts, retries, multimodal tokens, and proxy
billing policy.

## Cache Stability

The response cache key depends on the exact prompt, tool definitions, and model
parameters. Context ordering and tool ordering are therefore behavioral: do not
add timestamps, random identifiers, or unstable ordering to prompt content.
The context snapshot is telemetry only and does not enter the request or cache
key. Hysteresis, stable system/context parts, and unchanged tool schemas keep
requests identical between compaction points.

## Framework Practice

MAS Lab uses local assembly because the demo runs through OpenAI-compatible Chat
Completions, including Gemini via LiteLLM. The OpenAI Responses API's server-side
compaction item is not available on this route.

- [LangGraph short-term memory](https://docs.langchain.com/oss/python/langgraph/add-memory#manage-short-term-memory)
  documents token-aware trim/summarize steps and preserving valid tool-message
  groups.
- [Anthropic context windows](https://platform.claude.com/docs/en/docs/build-with-claude/context-windows)
  count system prompts, messages, tool results, tool definitions, and output;
  the provider also offers token counting, context editing, and compaction.
- [OpenAI compaction](https://developers.openai.com/api/docs/guides/compaction)
  compacts at a configured threshold and returns a continuation item for the
  Responses API.

The practical pattern is consistent: measure the whole request, reserve output
space, compact before the hard provider limit, preserve recent/tool-boundary
state, and expose the exact budget decision to debugging tools.
