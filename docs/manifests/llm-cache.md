<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# LLM cache

The **`llm_cache` infra middleware** records LLM responses to a JSON file and replays them later without calling the provider.
Use it to make tests and tutorials reproducible, to run offline, or to avoid paying for repeated calls.
A **trace cache** is this kind of store: a run with identical inputs reuses the stored response instead of calling the model again.
**Infra middleware** is a step in the infra pipeline that wraps the LLM provider.

Attach a `kind: InfraMiddleware` manifest with `--infra-ref` or with workspace `infra_refs`.
This manifest replays a recorded file and fails on any miss:

```yaml
apiVersion: infra/v1
kind: InfraMiddleware
metadata:
  name: llm-cache-replay
spec:
  middleware: llm_cache
  for: [llm]
  params:
    allow_read: true
    allow_write: false
    raise_on_miss: true
    cache_path: cache/demo.llm-cache.json
```

Reference (parameters, pipeline rules, implementation): [llm-cache.md](../references/llm-cache.md).
Prerequisite: Tutorial 0 (`task install`, `.venv` on `PATH`).
The commands below assume the repository root as the working directory.

## Contents

- [Overview](#overview)
- [Sample manifests](#sample-manifests)
- [Write manifest (record)](#write-manifest-record)
- [Replay manifest (playback)](#replay-manifest-playback)
- [Pipeline order](#pipeline-order)
- [Mini walkthrough](#mini-walkthrough-tutorial-1-agent-library-samples-infra)
- [Live provider (OpenAI)](#live-provider-openai)
- [Tool-using runs](#tool-using-runs)
- [Experiments and CI fixtures](#experiments-and-ci-fixtures)

## Overview

The `llm_cache` middleware caches every `LLM_CALL` that the runtime makes to the provider.
This covers initial answers, **tool-call turns** (`next_step: TOOL_CALL` or `PARALLEL_TOOL_CALLS`), and **post-tool completions**.
Each call is one cache entry.
The key is the assembled conversation preview at that point in the run.

You configure caching in a **separate infra manifest**, not on the agent.
Three parts work together:

1. **Provider** (`standard:openai`, `standard:ollama`, and others): where the model runs.
2. **Cache middleware** (`middleware: llm_cache`): the read, write, and replay policy plus `cache_path`.
3. **Pipeline**: `llm_cache` wraps the engine (one common instance). Other middleware wraps each routed provider. See [Pipeline order](#pipeline-order).

MAS also has a **built-in engine disk cache**.
Merged `RuntimeEngine` manifests (`runtime_refs` or `--runtime-ref`) set its policy, and so do the `--cache-read` and `--cache-write` flags on `mas-ctl chat`.
When the infra pipeline has any middleware step (`llm_cache`, `fault_inject`, and others), the built-in cache is off for that session.
See [references/llm-cache.md](../references/llm-cache.md#two-cache-mechanisms).

### End-to-end workflow (record, fixture, replay)

1. Pick or copy infra manifests from `library-samples/infra/`, or use the bundled `standard:llm-cache-*` refs. They come as a write and replay pair that shares `cache_path`.
2. Attach the provider and the write manifest on a chat or experiment run (`--infra-ref`).
3. Run the scenario, single-turn or multi-step with tools. Every `LLM_CALL` appends to the JSON file at `cache_path`.
4. Check in the cache file, for example `library-samples/infra/cache/demo.llm-cache.json`.
5. Replay with the replay manifest. No provider network calls happen. With `raise_on_miss: true`, any drift from the recorded trajectory fails fast.

Optionally set `include_preview: true` on the **write** manifest.
Each entry then stores the conversation preview, including the user query, for manual inspection and pruning.
See [Known limitations](../references/llm-cache.md#known-limitations).

## Sample manifests

| Manifest | Ref / path | Purpose |
| --- | --- | --- |
| Write | `library-samples/infra/llm-cache-write.yaml` | `cache/demo.llm-cache.json` (resolved under that infra dir) |
| Replay | `library-samples/infra/llm-cache-replay.yaml` | Strict replay; same `cache_path` |
| Read+write | `standard:llm-cache` | `cache_path: null` → `$XDG_CACHE_HOME/mas/llm_cache.json`; both read and write |
| Provider + cache | `standard:llm-proxy-cached` | `InfraBundle` (cache + OpenAI) |

The write and replay YAML lives in [library-samples/infra/](https://github.com/ioc-project/mas-lab/blob/main/library-samples/README.md).
The bundled `standard:llm-cache` and `standard:llm-proxy-cached` live under `library-standard/src/mas/library/standard/libs/standard/`.

## Write manifest (record)

This is `library-samples/infra/llm-cache-write.yaml`:

```yaml
apiVersion: infra/v1
kind: InfraMiddleware

metadata:
  name: llm-cache-write

spec:
  middleware: llm_cache
  for: llm
  params:
    allow_read: false
    allow_write: true
    raise_on_miss: false
    cache_path: cache/demo.llm-cache.json
```

`allow_read: false` means every call goes to the provider, and the middleware still writes the responses.
Use the **same** `cache_path` in the replay manifest.

## Replay manifest (playback)

This is `library-samples/infra/llm-cache-replay.yaml`:

```yaml
apiVersion: infra/v1
kind: InfraMiddleware

metadata:
  name: llm-cache-replay

spec:
  middleware: llm_cache
  for: llm
  params:
    allow_read: true
    allow_write: false
    raise_on_miss: true
    cache_path: cache/demo.llm-cache.json
```

On a hit, the middleware does not call the provider.
On a miss, `raise_on_miss: true` raises an error instead of falling through to a live call.
Parameter reference: [references/llm-cache.md](../references/llm-cache.md#parameters-specparams).

### Other parameters

The middleware also accepts these parameters, besides `allow_read`, `allow_write`, `raise_on_miss`, `include_preview`, and `cache_path`:

| Param | Default | Meaning |
|-------|---------|---------|
| `path` | none | Alias for `cache_path` |
| `enabled` | none | Legacy switch: `false` sets both `allow_read` and `allow_write` to `false` unless you give them |
| `write_mode` | `append` | `append` keeps existing keys and adds misses. `replace` empties `cache_path` once at process start, so a new recording is not mixed with an old one |
| `replay_delay_min_s` / `replay_delay_max_s` | `0` / `0` | On a hit, sleep a random time in this range (seconds) to mimic provider latency in demos |
| `miss_log_path` | none | On a `raise_on_miss` miss, append `{key, preview}` as one JSON line to this file, to diff what changed since recording |

Relative `cache_path`, `path`, and `miss_log_path` values resolve against the directory of the infra YAML file, not the working directory.

## Pipeline order

Only `InfraMiddleware` refs add pipeline steps.
Provider refs do not.
They supply only `api_base` and `model_access`.

Refs merge left to right: workspace `infra_refs`, then the user default, then CLI `--infra-ref`.
Agent and MAS YAML cannot add refs.
Pipeline steps append in that order. `llm_cache` is the common outermost wrap (one instance in front of the router). Other middleware is instantiated in front of each model leaf.

### One cache and one provider

Either CLI order works, because only the middleware ref adds a pipeline step:

```bash
--infra-ref standard:openai --infra-ref library-samples/infra/llm-cache-write.yaml
# same as
--infra-ref library-samples/infra/llm-cache-write.yaml --infra-ref standard:openai
```

Both merge to a common `[llm_cache]` step on the live engine, then the OpenAI leaf.

### Multiple middleware

Order matters.
The first ref is outermost:

```bash
--infra-ref standard:llm-cache --infra-ref standard:chaos-lite
```

Formal rules: [references/llm-cache.md: Pipeline model](../references/llm-cache.md#pipeline-model).

## Mini walkthrough (Tutorial 1 agent + library-samples infra)

This walkthrough reuses the [Tutorial 1](../tutorials/01-building-an-agent/README.md) agent and overlays, with no extra files under the tutorial tree.
The cache manifests come from `library-samples/infra/`.

```bash
export PATH="$(git rev-parse --show-toplevel)/.venv/bin:$PATH"
AGENT=docs/tutorials/01-building-an-agent/agent.yaml
```

**Record**

```bash
rm -f library-samples/infra/cache/demo.llm-cache.json

mas-ctl chat "$AGENT" \
  -q "Say hello in exactly three words." \
  --infra-ref standard:openai \
  --infra-ref library-samples/infra/llm-cache-write.yaml \
  --single-turn
```

**Replay** (same query; swap the middleware ref)

```bash
mas-ctl chat "$AGENT" \
  -q "Say hello in exactly three words." \
  --infra-ref standard:openai \
  --infra-ref library-samples/infra/llm-cache-replay.yaml \
  --single-turn
```

**Prove `raise_on_miss`** (a different query raises an error)

```bash
mas-ctl chat "$AGENT" \
  -q "Say goodbye in exactly three words." \
  --infra-ref standard:openai \
  --infra-ref library-samples/infra/llm-cache-replay.yaml \
  --single-turn
```

## Live provider (OpenAI)

This needs `OPENAI_API_KEY`.
The bundled refs `standard:llm-cache` and `standard:llm-proxy-cached` leave `cache_path` unset.
The runtime fills it with the user cache file `$XDG_CACHE_HOME/mas/llm_cache.json` (default `~/.cache/mas/llm_cache.json`).
These refs read and write.
The first run calls the provider, a repeat of the same conversation comes from the cache, and a miss falls through to the provider.

```bash
mas-ctl chat "$AGENT" \
  -q "Say hello in one sentence." \
  --infra-ref standard:openai \
  --infra-ref standard:llm-cache \
  --single-turn

# Equivalent: one InfraBundle with cache + OpenAI
mas-ctl chat "$AGENT" \
  -q "Say hello in one sentence." \
  --infra-ref standard:llm-proxy-cached \
  --single-turn
```

## Tool-using runs

The middleware caches every `LLM_CALL`, including responses that schedule tools:

| Turn | Typical `next_step` | Cached? |
| --- | --- | --- |
| Model requests a tool | `TOOL_CALL` or `PARALLEL_TOOL_CALLS` | Yes |
| Model answers after tool results | `STOP` | Yes |

Each turn is a **separate cache entry**, because the preview includes prior tool results.
On replay, the runtime walks the same LLM-call sequence.
With **deterministic tools** (same tool output for the same inputs), the trajectory matches the recording.
Every `LLM_CALL` then hits the cache until the run completes, with no live provider calls.

**Non-deterministic tool-call IDs** change the preview text between runs.
Strict replay (`raise_on_miss: true`) may then miss on post-tool turns.
Prefer a **live recording** for fixture-grade replay, and deterministic tools for CI.

This example uses Tutorial 1's `overlays/tools.yaml` with the same agent:

```bash
TOOLS=docs/tutorials/01-building-an-agent/overlays/tools.yaml

# Record
mas-ctl chat "$AGENT" \
  -q "Who is POTUS?" \
  --infra-ref standard:openai \
  -o "$TOOLS" \
  --infra-ref library-samples/infra/llm-cache-write.yaml \
  --single-turn

# Replay
mas-ctl chat "$AGENT" \
  -q "Who is POTUS?" \
  --infra-ref standard:openai \
  -o "$TOOLS" \
  --infra-ref library-samples/infra/llm-cache-replay.yaml \
  --single-turn
```

## Experiments and CI fixtures

Treat the cache file as a **run fixture**.
Record once, commit the file at `cache_path`, and replay it in CI with `raise_on_miss: true`, so tests never hit a live LLM.

`mas-lab benchmark run` has no `--infra-ref` flag.
Benchmark runs merge infra like `mas-ctl`: workspace `infra_refs`, `MAS_INFRA_REFS`, and user `default_infra`.
`--infra NAME` adds the experiment-local file `<experiment_dir>/infra/NAME.yaml`.
Copy the sample manifests there, and their relative `cache_path` resolves to `<experiment_dir>/infra/cache/`.
The provider (for example `standard:openai`) comes from the workspace `infra_refs` or your user `default_infra`.

```bash
cp library-samples/infra/llm-cache-write.yaml  path/to/experiment/infra/
cp library-samples/infra/llm-cache-replay.yaml path/to/experiment/infra/
```

**Record:**

```bash
mas-lab benchmark run path/to/experiment/experiment.yaml --infra llm-cache-write
```

**Replay:**

```bash
mas-lab benchmark run path/to/experiment/experiment.yaml --infra llm-cache-replay
```

### Not used yet

No API stops writing mid-experiment or prunes entries in-process.
See [Known limitations](../references/llm-cache.md#known-limitations).

See [Tutorial 3](../tutorials/03-experiments-and-analysis/README.md).

## See also

- [references/llm-cache.md](../references/llm-cache.md): exhaustive reference
- [infra.md](infra.md): infra kinds and bundles
- [runtime-engine.md](runtime-engine.md): built-in cache policy (`spec.cache` on RuntimeEngine)
- [Tutorial 1](../tutorials/01-building-an-agent/README.md): agent and overlays used above
- [library-samples/README.md](https://github.com/ioc-project/mas-lab/blob/main/library-samples/README.md): sample infra manifests
