<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Web UI

The MAS Lab web UI is a browser studio. You use it to edit manifests, launch
experiments, and inspect the files a run produced.

It talks over HTTP to `mas-lab-controller`, the same controller that backs
`mas-lab serve`. To watch a running system (topology, events, chat), use the
[live dashboard](live.md). It does not replace this studio.

Use the UI to browse visually. Paper reproduction and CI use the CLI
(`mas-lab benchmark run`, `task reproduce`).

**Related:** [Command line](../cli/index.md) · [Controller API](../cli/serve.md) ·
[Live dashboard](live.md) · [TUI](../ctl/tui.md) ·
[Docker setup](https://github.com/ioc-project/mas-lab/blob/main/docker/README.md)

---

## Quick start (Docker — recommended)

From the repository root:

```bash
cd docker
cp .env.example .env    # set OPENAI_API_KEY for live LLM runs
docker compose up --build
```

| Service | URL | Role |
|---------|-----|------|
| **Web UI** | http://localhost:8080 | Manifests, benchmarks, results |
| **Controller API** | http://localhost:8090 | REST backend (`/api/health`, `/api/registry`, …) |

Equivalent from repo root: `task start` (builds images if needed, starts detached).

### What you can do in the UI

- See every discovered lab on `/` (no scope parameter). Extra roots go in
  `config.yaml` `mas_lab.labs_search_paths` (relative or absolute; a path may
  itself be a `*.lab` directory).
- Create a lab (optional local library) and create experiments inside it.
- Browse and open agent, MAS, and experiment manifests from the mounted workspace.
- Validate manifests with the same checks as `mas-ctl validate`.
- Submit benchmark jobs, equivalent to `mas-lab benchmark run experiment.yaml`.
- Inspect recent runs, open `events.jsonl` (the machine-readable trace of a run), and view pipeline outputs under `results/`.
- Edit library objects (datasets, pipelines, experiments) when you develop a lab.
  While a lab is selected, create/copy lands in that lab or its local library;
  workspace and other libraries stay in the picker.

### Workspace and data mounts

Docker mounts two host folders into the backend and UI containers:

| Container path | Host (default) | Holds |
|----------------|----------------|-------|
| `/workspace` | Repository root (`..` from `docker/`) | `labs/`, tutorials |
| `/data` | `docker/data/` | Trace cache, benchmark outputs, run registry |

Configure in `docker/.env`:

```bash
MAS_WORKSPACE_MOUNT=/path/to/your/project
MAS_DATA_MOUNT=/path/to/persistent/data
OPENAI_API_KEY=sk-...
```

Project `config.yaml` on the workspace mount (copy from
[`library-samples/sample-workspace/config.yaml`](https://github.com/ioc-project/mas-lab/blob/main/library-samples/sample-workspace/config.yaml))
takes precedence over `$XDG_CONFIG_HOME/mas/config.yaml`. Without a project file,
the Docker image uses a built-in sample. See [user-config.md](../user-config.md).

---

## Local development (UI + controller on the host)

Use this when you change the UI or controller packages:

```bash
# Terminal 1 — controller API
task install
mas-lab serve --host 127.0.0.1 --port 8090

# Terminal 2 — Vite dev server
cd ui
yarn install
export VITE_API_BASE_URL=http://localhost:8090
yarn dev
```

Open http://localhost:5173 (Vite prints the port). The UI needs a running
controller. There is no offline fallback.

Developer details: [`ui/README.md`](https://github.com/ioc-project/mas-lab/blob/main/ui/README.md) in the source tree.

---

The UI is **backend-driven**: it never builds a server file path and it
contains no list of models, patterns, or folder conventions. It works against
a local controller, Docker, or a remote `mas-lab serve` (connection screen:
address + optional bearer token).

Scopes are **libraries** and **labs**. The home page is the labs catalog.
Open a lab for experiments; its local library is nested under it in the
picker. Versioned applications are `name@vN`. Creating an item is one
backend operation. Read-only (installed) libraries offer **Copy into** a
writable scope.

To point the UI at one lab (or one workshop tree) and **only** that catalog,
pin the project folder:

```bash
mas-lab serve --workspace /path/to/labs/ioc-motivation.lab
```

That sets `MAS_WORKSPACE_ROOT`. `config.yaml` in that folder controls the
lists:

```yaml
mas_lab:
  labs_search_paths:
    - .                    # this *.lab folder only; [] = no labs
manifest_libraries:
  - part2                  # extra library roots (workshop apps)
```

`MAS_LIBRARY_PATHS` is the env equivalent of `manifest_libraries`. Setting
`labs_search_paths` (including `[]`) makes that list exclusive: the
controller does not also cwd-walk the mas-lab checkout or list installed
packages as extra scopes. `--workspace` / `MAS_WORKSPACE_ROOT` alone still
adds the project folder on top of the usual catalog. Benchmark outputs
under `paths.labs_dir` (imported blog results, `task fetch-results`) show
on the experiment page.

Then open the studio. `lab:ioc-motivation` is the lab; creating an
application while it is selected writes to `ioc_motivation_lab`. Create
into **Workspace** writes to `<workspace>/library`.

`env-config.js` keys: `__MAS_LAB_API_BASE_URL__`, `__MAS_LAB_DEFAULT_SCOPE__`
(alias `__MAS_LAB_DEFAULT_LIBRARY__` for one release), `__MAS_LAB_TOKEN__`.

Controller flags: `--cors-origin`, `--token-file`, `--state-dir`, `--workspace`, `--host`
(see [serve](../cli/serve.md)).

## Controller API

Headless API only (no browser). Flags, clients, and route groups:
[Controller API](../cli/serve.md).

```bash
mas-lab serve --host 127.0.0.1 --port 8090
```

Detached daemon variant: `mas-lab control start` (default port **9000**).

---

## Traces and results

Each benchmark run writes `traces/events.jsonl` under the lab output tree. If an
identical run is in the trace cache (the store of completed runs), MAS Lab reuses
that entry instead of calling the model again. In the UI, open a completed run
to browse its events and the pipeline CSVs and figures.

CLI equivalent:

```bash
mas-lab benchmark show last
mas-lab benchmark show last events-trace
```

See [cli/observability.md](../cli/observability.md) for trace flags on `mas-ctl chat`.

---

## When to use CLI vs UI

| Task | Prefer |
|------|--------|
| First install, scripts, CI | CLI (`mas-ctl`, `mas-lab`) |
| Exploring manifests and past runs | Web UI |
| Paper reproduction | CLI (`task reproduce`) |
| Interactive terminal sessions | [TUI](../ctl/tui.md) |

---

## Next steps

Next: [Live dashboard](live.md), or one of these.

- [Tutorial 0](../tutorials/00-environment-setup/README.md) — Docker install and API keys
- [Tutorial 3](../tutorials/03-experiments-and-analysis/README.md) — experiments the UI can launch
- [User guide](../user-guide.md) — full install paths
