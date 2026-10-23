<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# MAS Lab — Docker

This page shows how to run the lab UI, the controller API, benchmarks, and the agent runtime in Docker. Read it if you want to use MAS Lab without a local Python or uv install.

Use Docker for day-to-day use. Use `uv run` and `task install` when you develop the framework itself.

## Quick start

```bash
cd docker
cp .env.example .env          # set OPENAI_API_KEY
docker compose up --build
```

- **UI:** http://localhost:8080 (host port `MAS_UI_PORT`)
- **API:** http://localhost:8090 (host port `MAS_BACKEND_PORT`; `/api/health`, `/api/info`, `/api/scopes`, …)

For a UI on another origin, pass `--cors-origin` and `--token-file` to the
controller and set `MAS_LAB_TOKEN` / `MAS_LAB_DEFAULT_SCOPE` for
`env-config.js` (see `docker/ui/entrypoint-ui.sh`).

Docker setup in the context of the tutorials:
[Tutorial 0](../docs/tutorials/00-environment-setup/README.md).

## Volume mounts

Every backend and cli container mounts two host folders and an optional read-only user config:

| Container path | Env var (host path) | Purpose |
|----------------|---------------------|---------|
| `/workspace` | `MAS_WORKSPACE_MOUNT` (default: `..`) | `labs/`, `infra/`, project `.env` (optional `config.yaml`) |
| `/data` | `MAS_DATA_MOUNT` (default: `./data`) | Trace cache, benchmark outputs, run artifacts |
| `/root/.config/mas` | `MAS_XDG_CONFIG_MOUNT` (default: `$HOME/.config/mas`) | Read-only user config and infra fallback |

Example `.env` for a custom project:

```bash
MAS_WORKSPACE_MOUNT=/home/you/my-mas-project
MAS_DATA_MOUNT=/home/you/mas-lab-data
OPENAI_API_KEY=sk-...
```

`docker/data/` is the default data directory. Docker creates it automatically. Git ignores its contents; only `.gitkeep` is tracked.

### Viewing precalculated experiment output

You can browse experiment output computed elsewhere in the UI without re-running anything. You need no eval pipeline and no `uv install`. The Experiment page reads files from disk. `GET /api/experiments` iterates `MAS_LAB_ROOT/labs`, and the file endpoint is a plain `FileResponse`.

1. Put the output on the host at `<host-folder>/labs/<experiment-name>/`. Use the layout the benchmark writes: `<experiment>/<scenario>/…/traces/events.jsonl`, plus `metadata.yaml` and `results.csv`. Native runs already live at `~/.local/share/mas/labs/<experiment>/`.
2. Point the data mount at that host folder and pin the lab root at the mounted `/data`:

   ```bash
   # docker/.env
   MAS_DATA_MOUNT=~/test-mas-lab      # host folder that has labs/<experiment>/ under it
   MAS_LAB_ROOT=/data                 # controller reads experiments from /data/labs
   ```

3. Run `docker compose up`. Recreate the container if it already runs, because `.env` changes need a fresh container. The experiments appear read-only on the Experiment page, straight from the mounted files.

Place several `labs/<name>/` folders side by side under the same `MAS_DATA_MOUNT`. They all show up.

### Workspace config priority

Inside the container, `MAS_WORKSPACE_ROOT=/workspace` is set. The runtime loads `config.yaml` in this order:

1. The mounted workspace, when it has one.
2. The read-only mounted `$XDG_CONFIG_HOME/mas/config.yaml`.
3. The baked copy of [`library-samples/sample-workspace/config.yaml`](../library-samples/sample-workspace/config.yaml) at `/opt/mas-lab/config.yaml`.

Project config wins over the `$XDG_CONFIG_HOME/mas/config.yaml` fallback.

To use a different host user config, override `MAS_XDG_CONFIG_MOUNT` in `docker/.env`. The default mount already exposes the host XDG config tree:

```bash
MAS_XDG_CONFIG_MOUNT=/path/to/mas-config
```

Run `mas-lab init` on the host to update this shared user config. For a self-contained project, run `mas-lab init --local` from the project root. It writes `config.yaml` and `infra/<name>.yaml` into the mounted workspace. That workspace then takes precedence in Docker.

## Environment variables

| Variable | Default (container) | Purpose |
|----------|---------------------|---------|
| `OPENAI_API_KEY` | — | LLM API key in `docker/.env` (canonical; shell exports are ignored) |
| `MAS_WORKSPACE_ROOT` | `/workspace` | Project folder for `config.yaml` and the writable Workspace library |
| `MAS_DATA_ROOT` | `/data` | Root for lab data |
| `MAS_TRACE_CACHE` | `/data/trace-cache` | Benchmark trace cache |
| `MAS_LABS_ROOT` | `/data/labs` | Experiment outputs under labs hierarchy |
| `MAS_LAB_ROOT` | `/workspace` (set it to `/data` to browse precalculated output, see above) | Directory whose `labs/` the controller's Experiment page reads |
| `MAS_RUNS_ROOT` | `/data/runs` | Agent session folders |
| `MAS_CONTROLLER_PORT` | `8090` | Controller HTTP port inside the container |
| `MAS_BACKEND_PORT` / `MAS_UI_PORT` | `8090` / `8080` | Host ports published by `docker compose` |
| `VITE_API_BASE_URL` | `http://localhost:8090` | API URL injected into the UI (browser-side) |

Put secrets in `docker/.env`. Git ignores it. A shell `export OPENAI_API_KEY=…` is not passed into containers. This avoids stale keys from another terminal or project. After editing `docker/.env`, run `task restart`. Never commit API keys.

For one-off overrides (CI), use: `docker compose --env-file /path/to/secrets.env up`.

## Run modes

The backend image installs the `mas-lab` and `mas-ctl` CLIs.

### Controller + UI (default)

```bash
docker compose up --build
```

### One-off CLI commands

Use `docker compose run` with the `tools` profile. You can also use `run backend` with an explicit command. Both share the same image and mounts:

```bash
# Show effective config / paths
docker compose --profile tools run --rm cli mas-lab config

# Benchmark (batch experiment)
docker compose --profile tools run --rm cli mas-lab benchmark run \
  labs/design-space.lab/01-design-patterns/experiment.yaml --progress

# Single agent conversation
docker compose --profile tools run --rm cli mas-ctl chat \
  library-samples/apps/qa-mas/agents/qa-agent.yaml -q "Hello"

# MAS orchestration
docker compose --profile tools run --rm cli mas-ctl run-mas \
  library-samples/apps/qa-mas/mas.yaml -q "Hello"

# Validate manifests
docker compose --profile tools run --rm cli mas-ctl validate \
  library-samples/apps/qa-mas/mas.yaml
```

Paths are relative to the repo root, which is mounted at `/workspace`. Use an absolute `/workspace/...` path to target the live mount explicitly.

Equivalent without the `cli` service:

```bash
docker compose run --rm backend mas-lab config
```

### Configure from inside Docker

1. Copy [`library-samples/sample-workspace/config.yaml`](../library-samples/sample-workspace/config.yaml)
   to your project root, or edit an existing copy under `MAS_WORKSPACE_MOUNT`.
2. Put secrets in `docker/.env` or `/workspace/.env`.
3. Restart: `task restart` from the repo root (rebuild + recreate both services).

Check effective paths:

```bash
docker compose run --rm cli mas-lab config
```

## Development with live code mounts

```bash
docker compose -f compose.yaml -f compose.dev.yaml up --build
```

`compose.dev.yaml` bind-mounts Python package sources into `/opt/mas-lab`. Code edits then apply without a rebuild. Workspace and data mounts are unchanged.

## Adding or removing a bundled library

The backend image bakes in libraries as uv workspace members. It copies each `library-*/` source tree and runs `uv sync` at build time.

You need this wiring only for a library that ships installable Python: custom step or plugin code under its `src/`, for example a pipeline step registered in `library.yaml`.

A pure-YAML library needs no image wiring. Pure YAML means apps, agents, overlays, datasets, experiments, and pipelines. The runtime reads it from the mounted `/workspace`. Keep it under `MAS_WORKSPACE_MOUNT`.

### Add a library `library-<name>` to the image

Edit three files, then rebuild:

1. **`pyproject.toml`** (repo root), 3 entries:
   - `[project].dependencies`: `"mas-library-<name>",`
   - `[tool.uv.workspace].members`: `"library-<name>",`
   - `[tool.uv.sources]`: `mas-library-<name> = { workspace = true }`
   (`mas-library-<name>` is the package name from the library's own `pyproject.toml`.)
2. **`docker/backend/Dockerfile`**: 2 `COPY` lines, next to the other libraries:
   ```dockerfile
   # with the per-package pyproject copies (layer-cache stage):
   COPY library-<name>/pyproject.toml library-<name>/
   # with the source-tree copies:
   COPY library-<name>/ library-<name>/
   ```
3. **`uv.lock`**: regenerate it, never hand-edit it:
   ```bash
   uv lock
   ```

Then `docker compose up --build` (or `docker compose build --no-cache` for a clean image).

### Remove a library

Do the inverse. Delete the 3 `pyproject.toml` entries and the 2 `Dockerfile` `COPY` lines, then run `uv lock`. If you skip any of them, the build fails:

- A leftover `COPY` errors with `"/library-<name>": not found`.
- A leftover `pyproject.toml` or `uv.lock` entry makes `uv sync` fail on the missing workspace member.

### Local (non-Docker) alternative

To use a library's step plugins with a local `uv` or `task install` setup, without making it a permanent workspace member, install just that package in editable mode:

```bash
uv pip install -e library-<name>
```

This needs no `pyproject.toml` or `Dockerfile` edit. It is not baked into the image. For Docker, use the 3-file wiring above.

## Layout

```
docker/
  compose.yaml          # UI + controller stack
  compose.dev.yaml      # optional live source mounts
  .env.example
  data/                 # default MAS_DATA_MOUNT target
  backend/
    Dockerfile
    entrypoint.sh
  ui/
    Dockerfile
    nginx.conf
    entrypoint-ui.sh
```

## Release images and local builds

Tagged releases publish the UI and backend images to GHCR via
`.github/workflows/docker.yml` and `.github/workflows/build-push-ghcr.yaml`.
The image names are:

```bash
ghcr.io/ioc-project/mas-lab/ui:tag
ghcr.io/ioc-project/mas-lab/backend:tag
```

To build from this repository for local development or CI validation:

```bash
cd docker
cp .env.example .env
docker compose build
```

Clean rebuild (remove existing local tags first):

```bash
# from repo root:
task docker-rebuild
```

Or manually:

```bash
cd docker
docker compose down --rmi local
docker rmi mas-lab-backend:local mas-lab-ui:local 2>/dev/null || true
docker compose build --no-cache
```
