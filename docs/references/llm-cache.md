<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# LLM cache reference

**Packages:** `mas-runtime` (pipeline, cache file format), `mas-ctl` (ref merge, engine wiring), `mas-library-standard` (`LlmCacheMiddleware` plugin, `type: infra_middleware`) · **Schema:** [`infra-middleware-params.schema.yaml`](../schemas/runtime/fragments/infra-middleware-params.schema.yaml) · **Middleware id:** `llm_cache`

This page is the full reference for the `llm_cache` infra middleware. It stores model responses on disk, so a repeated request skips the LLM call. Read it to record a run, replay it offline, or share a cache across a deployment. For a walkthrough, see the [hands-on guide](../manifests/llm-cache.md).

Terms: *infra* is the layer that says where things are and how to reach them. *Middleware* is a step that wraps the LLM call. A *run* is one execution of an agent or MAS. The *trace cache* (also called the LLM cache here) stores completed model responses.

## The problem

LLM calls cost money and vary between runs. The cache stores each response under a key built from the request. A later run reads the stored response instead of calling the model.

Read, write, and replay are separate switches on `kind: InfraMiddleware` manifests. To record once and replay offline:

1. Record: set `allow_read: false` and `allow_write: true`.
2. Replay: set `allow_read: true`, `allow_write: false`, and `raise_on_miss: true`. Any request the cache lacks then fails.

The [manifest example](#inframiddleware-manifest) below shows the YAML.

---

## Two cache mechanisms

MAS has two separate LLM response caches:

| Mechanism | Configured via | When to use |
| --- | --- | --- |
| **Built-in engine cache** | `RuntimeEngine.spec.cache`, `MAS_LLM_CACHE` / `MAS_LLM_CACHE_READ` / `MAS_LLM_CACHE_WRITE`, `--cache-read` / `--cache-write` (and `--no-…`) on `mas-ctl chat` | Deployment-wide policy via `runtime_refs`. See [runtime-engine.md](../manifests/runtime-engine.md). |
| **Infra middleware cache** (this reference) | `InfraMiddleware` with `middleware: llm_cache` | Shared deployment policy, write-only recording, strict replay (`raise_on_miss`), experiments |

When `llm_proxy.pipeline` is non-empty, `build_engine` disables the built-in `LiveLlmEngine` disk cache (`cache_active = not pipeline`). For that session, middleware `params` own read and write. The built-in `RuntimeEngine` disk cache does not.

Source: `ctl/src/mas/ctl/session/engine_factory.py`.

---

## Pipeline model

### Invoke path

```text
LLM_CALL (forward):

  ┌──────────────────┐     ┌──────────────────┐     ┌─────────────────┐
  │ LlmCacheMiddleware │ → │ LiveLlmEngine    │ → │ Provider        │
  │ (llm_cache)        │   │ (assemble + API) │   │ (OpenAI)          │
  └──────────────────┘     └──────────────────┘     └─────────────────┘
         ▲ miss + allow_write
         │ SHA-256(exchange_preview) → cache_path JSON
         ▼ hit + allow_read
       return MODEL_TEXT (inner never called)
```

`LlmCacheMiddleware.invoke`:

1. Pass through to `inner` when `not (allow_read or allow_write)` or `io.op != "LLM_CALL"`.
2. Call `exchange_preview("LLM_CALL")` **once**. It has side effects on correlation state.
3. On a hit (`allow_read` and key in cache), return the cached `EngineIoReturn`. It is text, `TOOL_CALL`, or `PARALLEL_TOOL_CALLS`, as recorded. It is tagged `cache_status: hit`, `cache_layer: infra_llm_cache`.
4. On a miss with `allow_read and raise_on_miss`:
    - append `{key, preview}` to `miss_log_path` when set,
    - record a `cache.lookup` miss,
    - raise `RuntimeError`.
5. Otherwise call `inner.invoke(io)`. On `allow_write` and `MODEL_TEXT` with `next_step` in `{STOP, TOOL_CALL, PARALLEL_TOOL_CALLS}`, persist the response.

Source: `library-standard/src/mas/library/standard/plugins/infra/llm_cache.py`. The runtime dispatches by registry name from `runtime/src/mas/runtime/engine/infra_pipeline.py`.

### What is cached

| `LLM_CALL` outcome | `next_step` | Written when `allow_write`? | Replayed when `allow_read`? |
| --- | --- | --- | --- |
| Final text answer | `STOP` | Yes | Yes |
| Single tool request | `TOOL_CALL` | Yes | Yes |
| Parallel tool batch | `PARALLEL_TOOL_CALLS` | Yes | Yes |

The cache stores only **`LLM_CALL`** responses. It does not cache `TOOL_CALL` or `TOOL_RESULT` engine ops. Tool *outputs* still change the preview, and so the key, of later `LLM_CALL` entries.

### Cache key

- **Key:** `sha256(inner.exchange_preview("LLM_CALL"))` as hex. The preview is the conversation assembled so far, including the user query and prior tool results.
- **Value:** a plain string (simple `STOP` text) or a structured dict (tool calls, parallel tools, optional `_preview` annotation).
- **File:** a single JSON object at `cache_path`.
- Each agentic turn has one key. Post-tool completions are separate entries.
- Non-deterministic tool-call IDs change the preview between runs.

### Deterministic trajectory replay

When **tools are deterministic** (same inputs give the same tool results), replay reproduces the recorded trajectory. Every turn is a cache hit. The provider is never called.

Requirements for strict offline replay (`raise_on_miss: true`):

1. Same `cache_path` and replay manifest as the recording.
2. Same overlays, tools, and prompts as the recording run.
3. Stable tool-call IDs in the preview (live recordings) or deterministic tools.

### Ref merge order

Infra refs merge in this order (`merge_infra_refs`):

```text
workspace infra_refs
  → user default_infra (if no workspace infra)
  → CLI --infra-ref flags (left to right)
```

Each ref's `pipeline` steps append via `_merge_pipeline(a, b) → a + b`.

### Pipeline wrap order

Only **`InfraMiddleware`** refs (and explicit bundle pipeline entries) append to `llm_proxy.pipeline`. **`LLMProxy`** and **`LLMLocal`** refs supply provider config only (`api_base`, `model_access`, …). They add no pipeline steps.

`wrap_bidirectional_pipeline` wraps with `reversed(pipeline)`:

```text
pipeline: [ llm_cache, fault_inject ]

wrap: engine → fault_inject(engine) → llm_cache(...)

invoke: llm_cache → fault_inject → LiveLlmEngine → provider
```

**First entry in merged pipeline = outermost middleware.**

| Scenario | CLI order effect |
| --- | --- |
| One provider + one `llm_cache` ref | Order is irrelevant. Only the middleware adds a step. |
| Multiple middleware refs | The first merged ref is outermost. List `llm_cache` before the others. |
| `InfraBundle` `spec.entries[]` | Entry order defines merge order. In `standard:llm-proxy-cached`, the cache comes before openai. |

Sources: `ctl/src/mas/ctl/workspace/config.py`, `ctl/src/mas/ctl/infra/resolve.py`,
`runtime/src/mas/runtime/engine/infra_pipeline.py`.

### `cache_path` hydration

At resolve time (`resolve_infra_refs`), `mas-ctl` fills a missing or empty `params.cache_path` for each `llm_cache` pipeline entry:

```text
$XDG_CACHE_HOME/mas/llm_cache.json
```

The path is `UserConfig.cache_dir / "llm_cache.json"`. An explicit `cache_path` in the manifest is never overwritten.

At infra load time, relative `cache_path` and `path` strings become absolute paths. They resolve against the directory that contains the middleware YAML file. Infra file refs themselves resolve from the MAS or agent **anchor**, or from the workspace root. They never resolve from the process working directory. See [infra.md](../manifests/infra.md).

The `MAS_LLM_CACHE` env var applies to the **built-in** engine cache only. It does not set middleware `params.cache_path`.

---

## `InfraMiddleware` manifest

```yaml
apiVersion: infra/v1
kind: InfraMiddleware

metadata:
  name: llm-cache-write

spec:
  middleware: llm_cache          # required; selects LlmCacheMiddleware
  applies_to:
    - LLM_CALL                   # default
  params:
    allow_read: false
    allow_write: true
    raise_on_miss: false
    include_preview: false
    cache_path: .cache/run.llm-cache.json
```

`_load_file` loads the manifest when `kind in {InfraMiddleware, InfraInterceptor}`. `spec.middleware` becomes the pipeline entry `middleware` id. `spec.params` becomes the `params` dict.

Schema: `docs/schemas/runtime/fragments/infra-middleware-params.schema.yaml`.

---

## Parameters (`spec.params`)

| Param | Type | Default | Meaning |
| --- | --- | --- | --- |
| `allow_read` | boolean | `true` | Look up `cache_path` before calling the provider |
| `allow_write` | boolean | `true` | Persist `LLM_CALL` responses (`STOP`, `TOOL_CALL`, `PARALLEL_TOOL_CALLS`) |
| `raise_on_miss` | boolean | `false` | When `allow_read` is set and the key is missing, raise instead of calling the provider |
| `include_preview` | boolean | `false` | On write, store `_preview` (conversation text incl. user query) per entry |
| `cache_path` | string \| null | XDG default* | JSON file path |
| `path` | string | none | Alias for `cache_path`. Hydration only checks `cache_path`, so set `cache_path` explicitly |
| `write_mode` | `append` \| `replace` | `append` | `replace` truncates `cache_path` once per process at startup (fresh recording); `append` merges new keys into the existing file |
| `miss_log_path` | string \| null | none | On a `raise_on_miss` miss, append `{key, preview}` as one JSON line, to diff record vs replay |
| `replay_delay_min_s` / `replay_delay_max_s` | number ≥ 0 | `0` / `0` | Sleep a uniform random delay in this range on each hit, to restore live-like pacing |
| `enabled` | boolean | `true` | Legacy: `false` disables both read and write when `allow_read` / `allow_write` are omitted |

\* Hydrated at resolve when null or omitted. See [cache_path hydration](#cache_path-hydration).

`apply_middleware` calls `LlmCacheMiddleware.wrap`, which resolves the switches like this:

```python
enabled = params.get("enabled")
default_on = True if enabled is None else enabled is not False
allow_read = params.get("allow_read", default_on) is not False
allow_write = params.get("allow_write", default_on) is not False
raise_on_miss = params.get("raise_on_miss", False) is True
include_preview = params.get("include_preview", False) is True
```

The same schema also carries the fault-injection middleware knobs (`rate`, `failure_rate`, `status_codes`, `errors`, `message`). They do not apply to `llm_cache`.

### Cache entry format

Keys are SHA-256 hex strings. Values are either:

**Plain string** (backward compatible): the final text answer.

```json
{ "a1b2c3…": "Hello, world." }
```

**Structured dict**: a tool-call or annotated entry.

```json
{
  "d4e5f6…": {
    "next_step": "TOOL_CALL",
    "tool_name": "lookup",
    "tool_arguments": {"q": "POTUS"},
    "text": "",
    "finish_reason": "tool_calls",
    "_preview": "[user]\n  Who is POTUS?"
  }
}
```

`_preview` is present only when the write manifest sets `include_preview: true`. Reads ignore it. Use it to identify entries when you prune by hand.

---

## Mode recipes

| Goal | `allow_read` | `allow_write` | `raise_on_miss` |
| --- | --- | --- | --- |
| Normal read-through cache | `true` | `true` | `false` |
| Record run (always hit provider) | `false` | `true` | `false` |
| Replay run (strict, no provider on miss) | `true` | `false` | `true` |
| Warm cache (no stale reads) | `false` | `true` | `false` |
| Read-only CI (never write) | `true` | `false` | `false` |

---

## `cache_path` locations

| Approach | Example | Notes |
| --- | --- | --- |
| Next to infra YAML | `.cache/tutorial.llm-cache.json` | Resolved relative to the middleware manifest directory |
| Repo example | `cache/demo.llm-cache.json` in infra YAML | Resolves to `library-samples/infra/cache/demo.llm-cache.json` |
| XDG default | `null` or omit | `standard:llm-cache-*` refs |
| Env `MAS_LLM_CACHE` | n/a | Built-in engine cache only |

Delete the JSON file to invalidate all entries, or use `write_mode: replace` on the recording manifest. The format is `{ "<sha256-hex>": "<text>" | {…}, ... }`. See [Cache entry format](#cache-entry-format).

---

## Bundled and example manifests

| Ref / path | `allow_read` | `allow_write` | `raise_on_miss` | `cache_path` |
| --- | --- | --- | --- | --- |
| `standard:llm-cache` | `true` | `true` | `false` | XDG |
| `standard:llm-proxy-cached` | (bundle) | n/a | n/a | cache + `standard:openai` |
| `library-samples/infra/llm-cache-write.yaml` | `false` | `true` | `false` | `cache/demo.llm-cache.json` (under infra dir) |
| `library-samples/infra/llm-cache-replay.yaml` | `true` | `false` | `true` | same |

---

## Known limitations

| Gap | Status | Workaround |
| --- | --- | --- |
| Stop caching mid-run / mid-experiment | Not implemented | Use write manifest only on record runs; replay manifest on CI runs |
| Prune or invalidate single entries | Not implemented | Delete `cache_path` or edit the JSON manually; use `include_preview: true` to find keys |
| Human-readable query in cache file | Opt-in | `include_preview: true` stores `_preview` (includes user message) per entry |
| Built-in cache vs middleware keying | Different schemes | Do not mix built-in RuntimeEngine cache files with middleware `cache_path` |
| Non-deterministic tool-call IDs | Runtime behaviour | Record with a live provider or deterministic tools for strict replay |

`raise_on_miss: true` is the safeguard for **fully offline** replay. Any cache miss is a hard error, so CI never silently calls a live provider.

---

## Relation to built-in RuntimeEngine cache

| | Built-in (`runtime-engine.md`) | Infra middleware |
| --- | --- | --- |
| Config surface | `RuntimeEngine.spec.cache` + workspace `runtime_refs` | `InfraMiddleware` + `--infra-ref` |
| Shared across agents | Yes (merged runtime manifests) | Yes |
| `raise_on_miss` | No | Yes |
| Independent read/write | Yes | Yes (`allow_read` / `allow_write`) |
| Active when pipeline set | Disabled | **Active** |

---

## Implementation index

| Component | Path |
| --- | --- |
| Middleware | `library-standard/src/mas/library/standard/plugins/infra/llm_cache.py` (`LlmCacheMiddleware`) |
| Pipeline | `runtime/src/mas/runtime/engine/infra_pipeline.py` (`apply_middleware`, `wrap_bidirectional_pipeline`) |
| Serialize / deserialize | `runtime/src/mas/runtime/engine/llm_cache.py` (`middleware_cache_serialize`, `middleware_cache_deserialize`) |
| Tests | `runtime/tests/test_middleware_llm_cache.py`, `runtime/tests/test_infra_pipeline_bidirectional.py` |
| Ref merge | `ctl/src/mas/ctl/infra/resolve.py` (`resolve_infra_refs`, `_merge_pipeline`, `_hydrate_pipeline_cache_paths`) |
| Ref ordering | `ctl/src/mas/ctl/workspace/config.py` (`merge_infra_refs`) |
| Engine wiring | `ctl/src/mas/ctl/session/engine_factory.py` (`build_engine`, `_wrap_with_infra_pipeline`) |
| Session resolve | `ctl/src/mas/ctl/session/infra_resolve.py` (`resolve_session_infra`) |
| Schema | `docs/schemas/runtime/fragments/infra-middleware-params.schema.yaml` |
| Bundled YAML | `library-standard/src/mas/library/standard/libs/standard/llm-cache.yaml`, `llm-proxy-cached.yaml`, `library-samples/infra/llm-cache-*.yaml` |

---

## See also

- [llm-cache.md](../manifests/llm-cache.md): getting started and walkthrough.
- [infra.md](../manifests/infra.md): infra kinds and bundles.
- [execution.md](../manifests/execution.md): built-in engine cache.
- [user-config.md](../user-config.md): XDG paths.
- [Context management](context-management.md): cache stability and cost.
