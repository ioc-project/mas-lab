<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Tutorial 0 — Environment Setup

> **Packages:** `mas-runtime`, `mas-ctl`, `mas-lab` (Docker or source checkout; PyPI upload pending)
> **Time:** ~10 min (Docker) · ~20 min (developer)

You install MAS Lab, give it access to an LLM, and run your first `mas-ctl` commands.
`mas-ctl` runs one agent or one multi-agent system (MAS). `mas-lab` runs experiments and manages configuration.

## What you need

- A clone of the repository: `git clone https://github.com/ioc-project/mas-lab.git && cd mas-lab`
- An `OPENAI_API_KEY` for live LLM runs. Offline, only `mas-ctl validate` works.

## Choose your path

| Path | Best for | You need |
| ---- | -------- | -------- |
| [Path 0 — PyPI](#path-0-pypi-packages) (not yet published) | Running a published release | Python ≥ 3.11, `uv` |
| [Path 1 — Docker](#path-1-docker) | Users, demos, benchmarks | Docker, optional [go-task](https://taskfile.dev/) |
| [Path 2 — Developer](#path-2-developers-uv-task) | Patching runtime, ctl, or lab | Python ≥ 3.11, `uv`, `task`, [direnv](https://direnv.net/) (recommended) |

If unsure, pick Docker. All paths use the same manifests. Then do [Verify your install](#verify-your-install).

## Path 0 — PyPI packages

The packages are not uploaded yet. Use Docker or the developer path until the first release.

```bash
uv venv .venv
uv pip install mas-lab mas-library-standard mas-library-samples
export PATH="$PWD/.venv/bin:$PATH"
mas-lab init
export OPENAI_API_KEY=<your-key>
```

`mas-lab` installs `mas-runtime` and `mas-ctl`. To pin a release, use `mas-lab==0.2.0`.
The tutorial manifests live in the repository, not in the packages. Clone it to follow Tutorial 1.

## Path 1 — Docker

### Step 1 — Configure LLM access

LLM access has three parts. Keep secrets out of YAML.

| Piece | What it is | Where it lives |
| ----- | ---------- | -------------- |
| **Infra manifest** | Endpoint settings: API base URL, allowed models, `api_key_env` name (no secret) | `infra_refs` in `config.yaml`, or `--infra-ref` |
| **Credential** | API key or token | `docker/.env` (Docker) or a gitignored `.env` (developers) |
| **Flavour** | Named runtime environment; `local` is the default | `mas_ctl.flavour` / `mas_lab.flavour`, or the manifest |

The bundled `standard:openai` and `standard:ollama` infra need no `mas-lab init`. Set the key:

```bash
cp docs/tutorials/00-environment-setup/.env.example docker/.env
# Edit docker/.env — set OPENAI_API_KEY=sk-...
```

For an OpenAI-compatible proxy, pass `--infra-ref standard:llm-proxy` and set `LLM_PROXY_API_BASE` next to the key.
Schema: [`config.schema.yaml`](../../schemas/config.schema.yaml). Reference: [user-config.md](../../user-config.md).

### Step 2 — Build the images

```bash
cd docker
docker compose build backend   # CLI + controller API; first build takes several minutes
# UI (optional): docker compose build ui
```

For a clean rebuild, run `task docker-rebuild`.

### Step 3 — Run the CLI

Prefix every `mas-ctl` / `mas-lab` command with the `cli` service (profile `tools`):

```bash
docker compose --profile tools run --rm --no-deps cli mas-lab config
docker compose --profile tools run --rm --no-deps cli mas-ctl validate \
  docs/tutorials/01-building-an-agent/agent.yaml
docker compose --profile tools run --rm --no-deps cli mas-ctl chat \
  docs/tutorials/01-building-an-agent/agent.yaml -q "What is the capital of France?"
```

The first two commands run offline. `chat` needs the key or an [llm_cache](../../manifests/llm-cache.md) replay.
Use `cli`, not `backend` (the long-running API service).

Two more checks, both from `docker/`. The dry run plans an experiment without calling the LLM. `run-mas` runs a multi-agent system and needs a key or a cache replay.

```bash
docker compose --profile tools run --rm --no-deps cli mas-lab benchmark run \
  labs/design-space.lab/01-design-patterns/experiment.yaml --dry-run
docker compose --profile tools run --rm --no-deps cli mas-ctl run-mas path/to/mas.yaml -q "Hello"
```

In the container, the working directory `/opt/mas-lab` is a copy of the repository baked into the image.
Your host checkout is mounted at `/workspace`. Use `/workspace/<path>` for files you edited after the build.

| Container path | Host (default) | Purpose |
| -------------- | -------------- | ------- |
| `/workspace` | Repository root | `labs/`, `infra/`, tutorials |
| `/data` | `docker/data/` | Trace cache, benchmark outputs |

Override the mounts in `docker/.env` with `MAS_WORKSPACE_MOUNT` and `MAS_DATA_MOUNT`.
Configuration priority: `/workspace/config.yaml`, then the mounted `~/.config/mas/config.yaml`, then the baked `/opt/mas-lab/config.yaml`.

### Step 4 — Start the web UI (optional)

```bash
cd docker && docker compose up -d      # or from the repo root: task start
```

Open the UI at <http://localhost:8080>. The controller API is at <http://localhost:8090/api/health>. See the [Web UI guide](../../ui/index.md).

| Task | Purpose |
| ---- | ------- |
| `task start` / `task restart` | Start the stack; rebuild after `docker/.env` or code changes |
| `task verify-env` | Confirm `OPENAI_API_KEY` inside the backend container |

Put secrets in `docker/.env` only. A shell `export` does not reach containers. Details: `docker/README.md`.

## Path 2 — Developers (uv + task)

Use this path to change the Python sources in `runtime/`, `ctl/`, or `lab/`.

### Step 1 — Install

Install Python ≥ 3.11, `uv` (`brew install uv`), `task` (`brew install go-task`), and optionally `direnv`.

```bash
git clone https://github.com/ioc-project/mas-lab.git && cd mas-lab
direnv allow          # recommended; uses the committed .envrc -> ./.venv
uv sync               # workspace packages install editable
task install-dev      # editable packages plus test and sample dependencies
```

Without direnv, run `uv venv .venv` once, then `uv sync`. `uv run <cmd>` runs a command in that environment.
`task install-all` adds every optional library.

Check the install. `command -v` prints three paths, and `--help` lists the commands. `task verify-unit` runs unit and package-boundary tests.

```bash
command -v mas-lab mas-ctl mas-runtime
mas-lab --help
task verify-unit
```

### Step 2 — Set the key

```bash
cp docs/tutorials/00-environment-setup/.env.example .env
# Edit .env and set OPENAI_API_KEY=sk-...
```

`mas-ctl` and `mas-lab benchmark run` load `.env` from the current directory. `mas-ctl` also searches
from the manifest's directory up to the repository root. Use `mas-ctl --env FILE …` to name one. Never commit keys.

### Step 3 — Set the user config

```bash
mas-lab init          # writes ~/.config/mas/config.yaml (and optionally infra/<name>.yaml)
mas-lab config        # show effective paths
```

The user config holds two keys: `infra_refs` (manifests to register, such as `standard:openai`) and
`default_infra` (used when you pass no `--infra-ref`). To write it by hand, start from
[`config.example.yaml`](config.example.yaml) (user config) or
[`config.openai.example.yaml`](config.openai.example.yaml) (project config).

`mas-lab init --local` writes `config.yaml` and `infra/<name>.yaml` in the project root instead. Those take priority.

### Step 4 — Make a self-contained project (optional)

To hand a project to someone unchanged, keep all paths inside it:

```bash
mkdir my-project && cd my-project
mas-lab init --local --yes
```

Add a `paths:` block ([`config.local-example.yaml`](config.local-example.yaml)) and `data/` to `.gitignore`:

```yaml
paths:
  labs_dir: ./data/labs
  cache_dir: ./data/cache
  runs_dir: ./data/runs
```

Precedence: CLI flag, `MAS_*` variable, `paths:`, user config, XDG defaults. See [user-config.md § Data paths](../../user-config.md#data-paths).

### Step 5 — Common tasks

Run `task --list` for all tasks. The useful ones:

| Task | Purpose |
| ---- | ------- |
| `task verify` | Pre-commit gate: version and env checks, then `task ci` |
| `task verify-lint` | Lockfile, literals, Ruff on changed Python |
| `task ci-fast` | Lint then unit |
| `task verify-tutorials` | Replay each tutorial's `demo/scenario.yaml` commands |
| `task docs-serve` | Local MkDocs site at `http://127.0.0.1:8000` |

After `git pull`, run `task install` only if dependencies changed.

## Verify your install

Run this from the repository root (prefix with `uv run` or the Docker command if needed):

```bash
mas-ctl validate docs/tutorials/01-building-an-agent/agent.yaml
mas-ctl chat docs/tutorials/01-building-an-agent/agent.yaml -q "What is the capital of France?"
```

`validate` works offline and prints a success line. `chat` prints an answer that names Paris.

`mas-ctl` reads and writes the **LLM response cache** by default, so a repeat of
the same query can return in tens of milliseconds with identical wording. Pass
`-v` to log cache hits and other internals (summarization, session hints).
Disable reads with `--no-cache-read` or `MAS_LLM_CACHE_READ=false` (PowerShell:
`$env:MAS_LLM_CACHE_READ = "false"`). See [llm-cache.md](../../manifests/llm-cache.md).

## Where things go

A trace is the record of a run. `events.jsonl` is its machine-readable form: one JSON event per line.
The **trace cache** stores whole-run traces so experiment reruns skip execution.
The **LLM response cache** is separate: it stores individual completions for
`mas-ctl chat` and `run-mas` (on by default; see the note above).

| Store | Default location |
| ----- | ---------------- |
| Trace cache | `$XDG_CACHE_HOME/mas/traces/` (default `~/.cache/mas/traces/`) |
| LLM response cache | `$XDG_CACHE_HOME/mas/llm_cache.json` |
| Lab outputs | `$XDG_DATA_HOME/mas/labs/<lab>/<experiment>/…/itemN/rN/traces/events.jsonl` |
| `mas-ctl chat` traces | `traces/events.jsonl` next to the manifest |

Run `mas-lab config` to confirm paths. To change them:

| Tool | Flag | Effect |
| ---- | ---- | ------ |
| `mas-lab benchmark run` | `-o` / `--output-dir PATH` | Write the experiment under `PATH` |
| `mas-lab benchmark run` | `--trace-cache PATH` | Use a separate trace cache |
| `mas-ctl chat` | `--events-file PATH` | Write `events.jsonl` to `PATH` |

For per-project defaults, set `paths.labs_dir`, `paths.cache_dir`, and `paths.runs_dir` in the workspace `config.yaml`.
On `mas-ctl`, `-o` / `--overlay` applies an overlay (a small YAML file that changes a manifest at run time). It is not an output path.

## Infrastructure: LLM endpoints

The infra reference says where to call the LLM. The key lives in `.env` or `docker/.env`.

| Bundle | Use when |
| ------ | -------- |
| `standard:openai` | OpenAI API (`OPENAI_API_KEY`) |
| `standard:ollama` | Local Ollama at `http://localhost:11434/v1` (no key) |
| `standard:production` | Tutorial default. Wraps `standard:openai`. |
| `standard:development` | Local Ollama bundle |

List them with `mas-ctl list-bundles`. Set the default with `default_infra: standard:production` in the user config,
or `infra_refs:` in a project `config.yaml`. `--infra-ref` overrides it for one run, including an inline URL:

```bash
mas-ctl chat docs/tutorials/01-building-an-agent/agent.yaml \
  --infra-ref http://localhost:11434/v1 -q "Hello"
```

Use `--model ID` on `chat` / `tui` to override the agent model. `MAS_*` override variables exist for test harnesses only:
see [Environment overrides](../../user-config.md#environment-overrides-last-resort).

## Verify this tutorial (CI)

```bash
pytest tests/tutorials/test_scenario_commands.py -v -k "tuto-00 and Developer"   # no Docker
pytest tests/tutorials/test_scenario_commands.py -v -k tuto-00                   # Docker (skipped without a daemon)
TUTORIAL_ONLINE=1 pytest tests/tutorials/test_scenario_commands.py -v -k tuto-00 # live LLM
```

## Common errors

| Symptom | Fix |
| ------- | --- |
| `command not found: mas-lab` | Run `direnv allow` or `source .venv/bin/activate`, then `task install`. |
| `401` from the LLM | Set `OPENAI_API_KEY` in `docker/.env` then `task restart` (Docker), or in the repo `.env` (source). Check `default_infra` with `mas-lab config`. |
| Docker build fails | Run `docker compose` from `docker/`. The first `uv sync` in the image needs network access. |
| Wrong benchmark output path | Run `mas-lab config`. Outputs go to `$XDG_DATA_HOME/mas/labs/` unless the user config says otherwise. |

## What you learned

- MAS Lab needs an infra manifest (where to call), a credential (the key), and a flavour (environment).
- `mas-ctl validate` works offline; `chat` and `run-mas` need a provider or a cache replay.
- `mas-lab config` shows where traces, cache, and results go.

## Next step

[Tutorial 1 — Building an Agent](../01-building-an-agent/README.md). On Docker, prefix its commands with `docker compose --profile tools run --rm --no-deps cli`.
