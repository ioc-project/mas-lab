<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# User-Level Configuration

This page lists where MAS Lab reads configuration and writes data, and how to
change those locations. Read it when you set up a machine or a project.

The first steps are in [Quick Start](#quick-start). Other docs use `$XDG_*` and
`MAS_*` names instead of hard-coded home paths. The XDG variables come from the
XDG Base Directory Specification.

Field-by-field `config.yaml` keys (including `mas_ctl.trace`) are in the
[config.yaml reference](references/config.yaml.md). CLI flags are in
[mas-ctl.md](cli/mas-ctl.md).

## Path variable reference

| Symbol | Default layout | Role |
|--------|----------------|------|
| `config.yaml` | project root | Workspace config (`paths:`, `infra_refs:`, …) |
| `$XDG_CONFIG_HOME` | `~/.config` | Base for user config (see row below) |
| `$XDG_CONFIG_HOME/mas/config.yaml` | under `$XDG_CONFIG_HOME` | Global config fallback |
| `$XDG_DATA_HOME` | `~/.local/share` | Base for persistent lab data |
| `$XDG_DATA_HOME/mas/labs` | under `$XDG_DATA_HOME` | Benchmark / lab output root |
| `$XDG_DATA_HOME/mas/runs` | under `$XDG_DATA_HOME` | Agent session run folders |
| `$XDG_DATA_HOME/mas` | under `$XDG_DATA_HOME` | Controller `MAS_HOME` default |
| `$XDG_DATA_HOME/mas/controller.sock` | under `$XDG_DATA_HOME` | Controller Unix socket |
| `$XDG_DATA_HOME/mas/memory/{agent_id}.sqlite` | under `$XDG_DATA_HOME` | Semantic memory SQLite (overlays) |
| `$XDG_CACHE_HOME` | `~/.cache` | Base for caches |
| `$XDG_CACHE_HOME/mas/traces` | under `$XDG_CACHE_HOME` | Content-addressed trace cache |
| `$XDG_CACHE_HOME/mas/artifacts` | under `$XDG_CACHE_HOME` | Pipeline step cache |
| `$XDG_CACHE_HOME/mas/llm_cache.json` | under `$XDG_CACHE_HOME` | LLM cache — built-in: [llm-cache.md](manifests/llm-cache.md) · [ref](references/llm-cache.md) |
| `$XDG_STATE_HOME` | `~/.local/state` | Base for state files |
| `$XDG_STATE_HOME/mas/last-run.json` | under `$XDG_STATE_HOME` | Last benchmark run pointer |
| `MAS_LABS_ROOT` | — | Env override for labs root |
| `MAS_RUNS_ROOT` | — | Env override for runs root |
| `MAS_DATA_ROOT` / `MAS_LAB_DATA` | — | Env override for data root |
| `MAS_TRACE_CACHE` | — | Env override for trace cache |
| `MAS_DATA_CACHE` | — | Env override for pipeline cache |
| `MAS_LLM_CACHE` | — | Env override for the LLM response cache file path |
| `MAS_LLM_CACHE_READ` / `MAS_LLM_CACHE_WRITE` | — | Built-in engine cache — [llm-cache.md](manifests/llm-cache.md). Infra middleware: [references/llm-cache.md](references/llm-cache.md) |
| `MAS_LLM_MAX_TOKENS` / `MAS_LLM_MAX_COMPLETION_TOKENS` | — (no limit sent) | Output-token budget for model rows that set none; below `spec.models[]` overrides and above infra `generation` — [Output-token limits](manifests/agent.md#output-token-limits) |
| `MAS_LLM_ON_TRUNCATION` | — | `ignore` \| `warn` \| `error` \| `escalate`; default action below `spec.models[].on_truncation` |
| `MAS_HOME` | `$XDG_DATA_HOME/mas` | Env override for controller data root |
| `MAS_CONTROLLER_SOCKET` | `$XDG_DATA_HOME/mas/controller.sock` | Env override for controller socket |

MAS Lab and `mas-ctl` read storage paths from the active config file (see
[Tutorial 0](tutorials/00-environment-setup/README.md)). Infra manifests (which
say where the LLM endpoint and tool servers are) come from workspace refs,
`$XDG_CONFIG_HOME/mas/infra/`, or explicit `--infra-ref` paths.

`config.yaml` may define top-level `aliases:` that override runtime plugin
resolution. See [runtime/docs/plugin-aliases.md](https://github.com/ioc-project/mas-lab/blob/main/runtime/docs/plugin-aliases.md)
for the discovery order and name mapping.

`plugins:` lists **runtime** plugins to add (gdb, checkpoint stores). It is off
unless you uncomment it. Observability and governance stay in the agent spec.
See the
[config.yaml reference](references/config.yaml.md#plugins-workspace-overlays).

**Config file names**

| Location | File | Role |
|----------|------|------|
| Project root | `config.yaml` | Workspace config (`paths:`, `infra_refs:`, …) |
| User home | `$XDG_CONFIG_HOME/mas/config.yaml` | Global fallback when no project file is found |

Default data paths follow the [XDG Base Directory Specification](https://specifications.freedesktop.org/basedir-spec/basedir-spec-latest.html):

| XDG variable | Default | MAS usage |
|--------------|---------|-----------|
| `XDG_CONFIG_HOME` | `~/.config` | `$XDG_CONFIG_HOME/mas/config.yaml`, `…/infra/` |
| `XDG_DATA_HOME` | `~/.local/share` | `$XDG_DATA_HOME/mas/labs`, `…/runs`, `…/data` |
| `XDG_CACHE_HOME` | `~/.cache` | `$XDG_CACHE_HOME/mas/traces`, `…/artifacts`, `…/llm_cache.json` |
| `XDG_STATE_HOME` | `~/.local/state` | `$XDG_STATE_HOME/mas/last-run.json` |
| `XDG_RUNTIME_DIR` | `/run/user/<uid>` (Linux); else `/var/run`; else temp | `$XDG_RUNTIME_DIR/mas-ctl` control advertisements (`MAS_CONTROL_DIR` overrides) |

## Quick Start

### 1. Create user config directory

```bash
mkdir -p "${XDG_CONFIG_HOME:-$HOME/.config}/mas/infra"
```

For a self-contained project directory, initialize project-local configuration
instead of user-level configuration:

```bash
cd /path/to/project
mas-lab init --local
```

This writes `config.yaml` and `infra/<name>.yaml` below the project root. The
runtime discovers that workspace file before the XDG fallback, and Docker sees
the same files when the project is mounted at `/workspace`.

### 2. Install a default infra manifest (optional)

```bash
cp config/infra/openai.example.yaml "${XDG_CONFIG_HOME:-$HOME/.config}/mas/infra/default.yaml"
```

Workspace checkouts use the sample at
[`library-samples/sample-workspace/config.yaml`](https://github.com/ioc-project/mas-lab/blob/main/library-samples/sample-workspace/config.yaml)
(copy it to your project root; commands find it by walking up from the
current directory).

For first-time setup via `mas-lab init`, generated files come from templates
bundled in the `mas-lab` package:
[`lab/src/mas/lab/templates/init/config.yaml`](https://github.com/ioc-project/mas-lab/blob/main/lab/src/mas/lab/templates/init/config.yaml)
and [`lab/src/mas/lab/templates/infra/llmprovider.yaml`](https://github.com/ioc-project/mas-lab/blob/main/lab/src/mas/lab/templates/infra/llmprovider.yaml).

### 3. Set API key

```bash
export OPENAI_API_KEY=sk-...
```

### 4. Run without --infra-ref

```bash
# Uses workspace infra_refs or $XDG_CONFIG_HOME/mas/infra/default.yaml
mas-ctl chat agent.yaml -q "What is 2+2?"
```

### Human exchange log (`mas_ctl.trace`)

Stdout carries the conversation. The agent, LLM, and tool transcript goes to
stderr as a human-readable log. Set `mas_ctl.trace: summary` in `config.yaml` to
get it without `--trace`. Colour stays opt-in (`--trace-color` or
`trace_color: true`).

Complete tables: [config.yaml](references/config.yaml.md#mas_ctl) ·
[mas-ctl flags](cli/mas-ctl.md#exchange-log).

## Configuration Discovery

The runtime searches for infra manifests in this order:

1. **CLI flag**: `--infra-ref <path>`
2. **Workspace**: `infra_refs` in `config.yaml`
3. **User default**: `$XDG_CONFIG_HOME/mas/infra/default.yaml` (if no CLI flag)

Runtime engine manifests (`kind: RuntimeEngine`) resolve via optional
`runtime_refs` in `config.yaml`, `--runtime-ref`, or user
`default_runtime` in `$XDG_CONFIG_HOME/mas/config.yaml`. They are **not**
declared on Agent or MAS manifests. Omit `runtime_refs` to use package defaults
only. See [runtime-engine.md](manifests/runtime-engine.md).

Model override for a single `mas-ctl chat` / `mas-ctl tui` run (overrides manifest
`spec.models`): `--model ID`, e.g. `gpt-4o-mini` on direct OpenAI, or a
provider-prefixed id (e.g. `azure/gpt-4o-mini`) through an OpenAI-compatible
proxy gateway. For MAS runs and benchmarks, declare models in the manifest or
pin them in the experiment (`model:` / `models:`).

### Data paths

Lab and benchmark output locations use the unified ladder documented in
`mas.lab.paths` (see `mas-lab config` for effective values). Set them under
`paths:` in `config.yaml` (`labs_dir`, `runs_dir`, `cache_dir`); the data root
is the parent of `labs_dir`.

When `paths.cache_dir` is set in `config.yaml`, trace cache defaults to
`<cache_dir>/traces`. Otherwise trace cache is `$XDG_CACHE_HOME/mas/traces`.

### Environment overrides (last resort)

Configure MAS Lab with manifests, `config.yaml`, and CLI flags. They are
versioned with the project and give the same result on every machine.

The variables below are last-resort overrides for test harnesses and one-off
debugging. Do not put them in `.env` files or shell profiles. They apply
silently to every run in that environment, including the long-lived controller
daemon, which keeps the environment it started with. Model and infra or runtime
ref overrides log a warning when applied.

| Variable | Overrides |
|----------|-----------|
| `MAS_CTL_MODEL` / `MAS_LLM_MODEL` | `spec.models` (use `--model` or the experiment's `models:`) |
| `MAS_INFRA_REFS` / `MAS_RUNTIME_REFS` | Workspace `infra_refs` / `runtime_refs` (use `--infra-ref` / `--runtime-ref`) |
| `MAS_WORKSPACE_ROOT` | Workspace discovery from the current directory |
| `MAS_LABS_ROOT`, `MAS_RUNS_ROOT` | `paths.labs_dir`, `paths.runs_dir` |
| `MAS_DATA_ROOT` / `MAS_LAB_DATA` | Data root (derived from `paths.labs_dir`) |
| `MAS_TRACE_CACHE`, `MAS_DATA_CACHE` | Trace / pipeline step cache (use `--trace-cache` / `--data-cache`) |
| `MAS_LLM_CACHE`, `MAS_LLM_CACHE_READ` / `MAS_LLM_CACHE_WRITE` | Built-in engine cache — [llm-cache.md](manifests/llm-cache.md) |
| `MAS_LLM_HTTP_RETRIES` | Extra LLM HTTP retries (`attempts = retries + 1`). Prefer `spec.control.retry.llm` — [reliability.md](references/reliability.md) |
| `MAS_LLM_HTTP_RETRY_BACKOFF` | LLM HTTP base backoff seconds (also disables jitter) |
| `MAS_HOME`, `MAS_CONTROLLER_SOCKET` | Controller data root / socket |

Secrets (`OPENAI_API_KEY`, proxy credentials) are the exception: they belong
in the environment, never in YAML.

Relative paths in any config file (`config.yaml` or `$XDG_CONFIG_HOME/mas/config.yaml`)
resolve from **that file's directory** — e.g. with user config at
`~/.config/mas/config.yaml`, `labs_dir: custom/labs` → `~/.config/mas/custom/labs`.

## Resolving `name:path` and infra refs

In a library ref `name:path` (tools, overlays, infra bundles, …), the prefix is
always a **library name**. MAS Lab searches lab-local libraries first, then
workspace `manifest_libraries:`, then installed libraries.

- List local library directories in `lab-config.yaml` under `lab.libraries`. Each needs a `library.yaml`.
- List extra checkouts in `config.yaml` under `manifest_libraries:`. It is a list of paths. The name is the directory stem.

Guide: [labs-and-libraries.md](labs-and-libraries.md). Search order and
`LookupError`: [library-discovery.md](library-discovery.md).

An infra ref that is not `name:path` resolves to `$XDG_CONFIG_HOME/mas/infra/{ref}`
or to a path relative to the manifest directory.

### Examples

```bash
# Library bundle ref
mas-ctl chat agent.yaml --infra-ref standard:openai -q "Hello"

# User config file
mas-ctl chat agent.yaml --infra-ref "$XDG_CONFIG_HOME/mas/infra/default.yaml" -q "Hello"

# Workspace-relative example file
mas-ctl chat agent.yaml --infra-ref config/infra/openai.example.yaml -q "Hello"
```

## Migrating older agent / MAS manifests

`mas-ctl validate` rejects infra and runtime wiring on Agent, MAS, and overlay
manifests. It also rejects `spec.execution` on agents. Use the workspace file
instead:

| Removed from manifests | Use instead |
| --- | --- |
| `spec.infra_refs`, `spec.runtime_refs`, `infra_interceptors` | `infra_refs` / `runtime_refs` in `config.yaml`, `--infra-ref` / `--runtime-ref` |
| `spec.execution` (cache, stream, queue depth, parallel tools, …) | `kind: RuntimeEngine` refs (see [runtime-engine.md](manifests/runtime-engine.md)) |
| Offline LLM turns (no live provider) | [llm_cache replay](manifests/llm-cache.md) (`raise_on_miss`) recorded against a live provider |

The `design:`, `schedule:`, and `bench_emulation:` blocks of an experiment
(formerly `experiment.execution`) are a different thing. They control the grid
of runs, batch orchestration, and trace emulation
([experiment.md](manifests/experiment.md#design-vs-schedule)).

## Example Configurations

### OpenAI

See `library-standard/src/mas/library/standard/libs/standard/openai.yaml` and
`config/infra/openai.example.yaml`.

### Offline replay

Record with a live provider, then replay from disk (`raise_on_miss: true`).
See [llm-cache.md](manifests/llm-cache.md).

## Doc authors

Path strings live in `mas.runtime.constants` and `mas.runtime.xdg`. They are
generated into `docs/includes/mas-paths.md` for MkDocs snippets. After you
change a default, run `task docs-gen` and commit the updated include file.

Next: [Tutorial 0 — Environment setup](tutorials/00-environment-setup/README.md).
