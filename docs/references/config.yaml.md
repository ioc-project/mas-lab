<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# `config.yaml` reference

`config.yaml` sets workspace and user defaults for **`mas-ctl`** and **`mas-lab`**. A *workspace* is the project folder where you run the CLIs. The schema is [`docs/schemas/config.schema.yaml`](../schemas/config.schema.yaml). It sets `additionalProperties: false` on the workspace file, so unknown keys fail validation.

Example: a minimal workspace file.

```yaml
mas_ctl:
  flavour: local
  env: .env
  trace: summary
```

**Secrets never belong in this file.** Put API keys in the dotenv file named by `mas_ctl.env` (default `.env`), or in the process environment. Infra manifests reference the env **name** (`api_key_env`), not the value.

Paths and XDG layout: [user-config.md](../user-config.md). CLI flags that override these keys: [mas-ctl.md](../cli/mas-ctl.md).

---

## Which file is loaded

| File | Role |
|------|------|
| `<project>/config.yaml` | Workspace. The CLI walks up from the current directory, or uses `MAS_WORKSPACE_ROOT`. |
| `$XDG_CONFIG_HOME/mas/config.yaml` | User fallback. `mas-lab init` writes it. |

Canonical samples:

- [`library-samples/sample-workspace/config.yaml`](https://github.com/outshift-open/mas-lab/blob/main/library-samples/sample-workspace/config.yaml)
- Tutorials 1–3 under `docs/tutorials/*/config.yaml` (Tutorial 0 ships `config.*example.yaml` user-file samples)
- Init template: [`lab/src/mas/lab/templates/init/config.yaml`](https://github.com/outshift-open/mas-lab/blob/main/lab/src/mas/lab/templates/init/config.yaml)

### `mas_ctl` merge (exchange log and friends)

The `mas_ctl.trace*` keys, and the rest of the `mas_ctl` map used by chat and run-mas, merge in this order (first wins):

1. CLI flags
2. Workspace `config.yaml`
3. User `$XDG_CONFIG_HOME/mas/config.yaml`
4. Built-ins. `trace` is off unless set. When on, it shows a summary with timestamps and no color.

---

## Top-level keys (workspace schema)

| Key | Type | Purpose |
|-----|------|---------|
| `mas_ctl` | object | Defaults for `mas-ctl chat` / `run-mas` / validate |
| `mas_lab` | object | Defaults for `mas-lab` benchmark / demo |
| `infra_refs` | string or list | Infra bundles, merged left to right. An *infra bundle* says where things are (LLM endpoint, tool servers). Env `MAS_INFRA_REFS` (comma- or space-separated) overrides without editing the file. CLI `--infra-ref` still wins. |
| `runtime_refs` | string or list | `RuntimeEngine` refs. Omit for package defaults. Env `MAS_RUNTIME_REFS` (comma- or space-separated). Per-run override: `--runtime-ref`. |
| `infra_interceptors` | string or list | Optional interceptor bundle refs. |
| `manifest_libraries` | list of paths | Extra library search folders: a library root or a parent of sibling library folders, same scan as `lab.libraries` / `MAS_LIBRARY_PATHS` (name = directory stem). A legacy name→path map is flattened to its paths. |
| `aliases` | map | Runtime plugin alias to canonical URN |
| `defaults` | object | Overrides for runtime `defaults.yaml` (`model`, `design_pattern`, `context_manager`, `assembler`) |
| `paths` | object | `labs_dir`, `cache_dir`, `runs_dir` (else XDG / `MAS_*`) |
| `plugins` | list of URNs or URN-to-bool map | Workspace overlay plugins. **Off unless listed.** Not agent spec slots. Uncomment a catalog line in the init template to enable one. Labs use `lab.enable_plugins`. |

---

## `mas_ctl`

| Key | Type | Default | Purpose |
|-----|------|---------|---------|
| `flavour` | string | `local` | Flavour name. A *flavour* is a deployment preset. Found in `library-standard` or the workspace `flavours/` folder. |
| `env` | string | `.env` | Gitignored secrets **filename** relative to the workspace |
| `deployment` | string | none | Deployment manifest path, relative to the workspace |
| `runtime_id` | string | none | Kernel or runtime id override |
| `runtime_profile` | string | none | Path to a runtime-profile manifest |
| `trace` | `off` · `summary` · `full` or bool | `off` | Human exchange log on **stderr**. `true` means `summary`. `full` dumps payloads. Color is **not** implied. |
| `trace_timestamps` | bool | `true` when tracing | UTC time and elapsed time on each exchange |
| `trace_color` | bool | `false` | ANSI color. Opt-in. Same as `--trace-color`. |
| `trace_engine` | bool | `false` | Raw engine I/O JSON. Same as `--trace-engine` or `-vv`. |

```yaml
mas_ctl:
  flavour: local
  env: .env
  trace: summary          # off | summary | full
  # trace_timestamps: true
  # trace_color: false    # never implied by trace: summary
  # trace_engine: false
```

On the CLI, `--trace` is the one-shot form of `trace: summary`. `--trace full` is `trace: full`. `--no-trace` disables a config default.

The exchange log is for humans. Tools read `events.jsonl` and `mas-lab telemetry` instead. See [mas-ctl.md](../cli/mas-ctl.md#exchange-log) and [observability.md](../cli/observability.md).

---

## `mas_lab`

| Key | Type | Default | Purpose |
|-----|------|---------|---------|
| `flavour` | string | `local` | Lab / demo flavour |
| `benchmark_flavour` | string | none | Flavour for `mas-lab benchmark run` |
| `benchmark.clean_stale_outputs` | bool | `false` | Drop output folders for scenarios no longer in `experiment.yaml` |
| `benchmark.clean_stale_trace_cache` | bool | `true` | When cleaning stale outputs, also drop unused trace-cache entries. The trace cache stores completed run traces. |
| `labs_search_paths` | string or list | none | Dirs (relative to the config file, or absolute) scanned for `*.lab` folders. An entry may itself be a `*.lab` directory. When this key is present (including an empty list), it is the exclusive lab catalog — installed packages and cwd-walk of the mas-lab checkout stay out. When the key is omitted, those sources stay additive. Not `paths.labs_dir` (benchmark output). `POST /api/scopes` writes a new `{name}.lab` to the first entry. |

---

## `defaults`

| Key | Purpose |
|-----|---------|
| `model` | Fill-in for any remaining Agent, MAS, or experiment `any` after `experiment.models`. Not written into committed specs. |
| `design_pattern` | Default plugin id for `spec.design_pattern` |
| `context_manager` | Default plugin id for `spec.context_manager` |
| `assembler` | Default plugin id for `spec.assembler` |

See [runtime/docs/agent-defaults.md](https://github.com/outshift-open/mas-lab/blob/main/runtime/docs/agent-defaults.md).

---

## `paths`

| Key | Purpose |
|-----|---------|
| `labs_dir` | Benchmark run output root |
| `cache_dir` | Cache root (trace cache defaults to `<cache_dir>/traces` when set) |
| `runs_dir` | Agent session run folders |

Env overrides: [user-config.md](../user-config.md). Relative paths resolve from **the directory of the config file that declared them**.

---

## `plugins` (workspace overlays)

Agent YAML still chooses `design_pattern`, tools, models, `spec.observability`, `spec.governance`, and checkpoint cadence. This list turns on gdb, checkpoint stores, and the control wire from the workspace. A *plugin* is a unit of behaviour selected by name.

Omitted, `[]`, or a commented catalog means **nothing extra is on**. To enable a plugin, uncomment `plugins:` and any line in the [init template](https://github.com/outshift-open/mas-lab/blob/main/lab/src/mas/lab/templates/init/config.yaml). Specs that already name the same plugin keep their own config.

Labs enable the same URNs under `lab.enable_plugins` in `lab-config.yaml`.

```yaml
# plugins:
#   - mas.runtime.debug_script             # gdb breakpoints
#   - mas.checkpoint_store.hybrid          # persist memory + disk
#   - mas.control_protocol.rpc             # JSON-lines control wire
```

Put OTel (OpenTelemetry) settings, governance, design patterns, and system tools in agent YAML, not here.

---

## User-file extras (`mas-lab init` / Tutorial 0)

`$XDG_CONFIG_HOME/mas/config.yaml` is a hybrid. It may include the workspace keys above **and** these fields used by user-config loading. Not all are in the workspace schema.

| Key | Purpose |
|-----|---------|
| `apiVersion` / `kind` | `mas.config/v1` / `UserConfig` header in the Tutorial 0 example |
| `default_infra` | Infra used when no `--infra-ref` or workspace `infra_refs` is set |
| `default_runtime` | Default `RuntimeEngine` when no `--runtime-ref` or `runtime_refs` is set |
| `cache_dir` | Top-level alias for `paths.cache_dir` |
| `runs_dir` / `labs_dir` | Top-level path aliases in the Tutorial 0 user example |

Example: [`docs/tutorials/00-environment-setup/config.example.yaml`](../tutorials/00-environment-setup/config.example.yaml).

---

## Example (workspace)

```yaml
mas_ctl:
  flavour: local
  env: .env
  trace: summary

mas_lab:
  flavour: local
  benchmark_flavour: local-benchmark

# infra_refs:
#   - standard:openai

# plugins:
#   - mas.runtime.debug_script
```

Offline CI pairs `standard:openai` with `tests/fixtures/llm-cache/ci-replay.yaml` through `--infra-ref`. The YAML still holds no secrets.

---

## See also

- [User configuration](../user-config.md): paths and XDG layout.
- [`mas-ctl` reference](../cli/mas-ctl.md)
- [Compiled defaults](defaults.md)
- [Schema index](schemas.md)
- [Tutorial 0: environment setup](../tutorials/00-environment-setup/README.md)
