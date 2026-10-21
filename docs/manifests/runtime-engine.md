<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Runtime engine manifests (`kind: RuntimeEngine`)

A **RuntimeEngine** manifest sets kernel and LLM engine tuning for a whole deployment.
It covers queue depth, the dispatch step budget, streaming, parallel tool calls, and the built-in LLM response cache policy (read and write flags).
Write one to change these settings for every agent, for example to cap a runaway dispatch loop in CI.

A RuntimeEngine does not configure LLM endpoints.
Declare endpoints with workspace `infra_refs` such as `standard:openai`; see [Infrastructure manifests](infra.md).

```yaml
apiVersion: infra/v1
kind: RuntimeEngine
metadata:
  name: ci-runtime
spec:
  engine:
    queue_depth: 64
    max_auto_steps: 256
  stream: false
  cache:
    read: true
    write: true
```

Agent and MAS manifests cannot reference a runtime engine: they have no `runtime_refs`.
The schema is [`runtime-engine.schema.yaml`](../schemas/runtime/fragments/runtime-engine.schema.yaml), the `spec` of an `apiVersion: infra/v1`, `kind: RuntimeEngine` document.
The loader rejects unknown keys.

The bundled ref `standard:runtime-default` (from `library-standard`) sets `queue_depth: 32`, `stream: false`, and no dispatch cap.
To cap the loop, use one of two options:

- Apply overlay `pkg://mas.library.standard/overlays/with-hardened.yaml`, which sets `design_pattern.params.max_steps: 512`.
- Set `spec.engine.max_auto_steps` on a RuntimeEngine that you pass with `--runtime-ref`.

For offline CI, replay responses recorded against a live provider with [llm_cache](llm-cache.md).

## Fields

| Field | Default | Effect |
|-------|---------|--------|
| `engine.queue_depth` | `32` | Kernel engine queue depth (integer >= 1) |
| `engine.max_auto_steps` | `null` (no cap) | Dispatch-loop step cap; stops a runaway loop |
| `engine.timeout` | none | Accepted by the schema; the current kernel does not apply it |
| `cache.enabled` | none | `false` turns built-in cache read and write off |
| `cache.read` / `cache.write` | `true` / `true` | Built-in engine disk cache lookup / store |
| `stream` | `false` | Stream LLM responses |
| `parallel` | none | Overrides the design pattern's `parallel_tool_calls` for agents on this deployment |

The built-in cache stores responses in `$XDG_CACHE_HOME/mas/llm_cache.json`.
The default is `~/.cache/mas/llm_cache.json`; `cache_dir` in the user `config.yaml` moves it.
The built-in cache is off for a session whose infra pipeline has any middleware step, such as `llm_cache` or `fault_inject`.
The middleware then owns caching.
See [LLM cache: Overview](llm-cache.md#overview).

## How refs are resolved

`runtime_refs` works like `infra_refs`, but it resolves `RuntimeEngine` manifests.
The runtime reads it only from deployment config, never from agent, MAS, overlay, or flavour manifests.

| Source | Infra (`infra_refs`) | Runtime (`runtime_refs`) |
|--------|----------------------|---------------------------|
| CLI | `--infra-ref` | `--runtime-ref` (`mas-ctl chat`, `tui`, `compose`) |
| Workspace | `config.yaml` -> `infra_refs` | `config.yaml` -> `runtime_refs` |
| Env override of workspace list | `MAS_INFRA_REFS` | `MAS_RUNTIME_REFS` (comma- or space-separated) |
| User default | `default_infra` in `~/.config/mas/config.yaml` | `default_runtime` in same file |
| Search dirs | workspace root, `infra/`, `config/infra/`, anchor, `~/.config/mas/infra/` | workspace root, `runtime/`, `config/runtime/`, anchor, `~/.config/mas/runtime/` |

Refs merge in the order workspace, user default, CLI, without duplicates.
The user default applies only when the workspace lists no refs.
When several RuntimeEngine manifests merge, later ones win per key.
The `cache` block merges key by key.

A workspace file lists both kinds of ref (see `library-samples/sample-workspace/config.yaml`):

```yaml
infra_refs:
  - standard:openai
runtime_refs:
  - standard:runtime-default   # optional; omit for package defaults only
```

Omit `runtime_refs` to use the package kernel, cache, and stream defaults.
The loader rejects a `RuntimeEngine` listed in `infra_refs`.
List it in `runtime_refs` or pass it with `--runtime-ref`.

## Precedence

The loader builds `KernelConfig` (the kernel's resolved settings) and the LLM cache and stream behaviour in layers.
Later layers win:

1. Package defaults: `DEFAULT_ENGINE_QUEUE_DEPTH = 32`, no step cap, cache read and write on, streaming off.
2. Env vars `MAS_LLM_CACHE_READ`, `MAS_LLM_CACHE_WRITE`, `MAS_LLM_STREAM`. They apply only to keys that no RuntimeEngine manifest sets.
3. Merged `RuntimeEngine` manifests from the resolution table above.
4. CLI flags on `mas-ctl chat`: `--cache-read/--no-cache-read`, `--cache-write/--no-cache-write`, `--stream/--no-stream`.

## See also

- [Infrastructure manifests](infra.md): LLM proxy and middleware (`infra_refs`)
- [user-config.md](../user-config.md): workspace and XDG layout
- [LLM cache](llm-cache.md): cache middleware versus cache policy
- [Execution tuning](execution.md): fields removed from Agent manifests
- [LLM cache reference: Two cache mechanisms](../references/llm-cache.md#two-cache-mechanisms)
