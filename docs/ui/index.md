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

**Related:** [Terminal UI (TUI)](../ctl/tui.md) · [Observability](../cli/observability.md) ·
[Docker setup](https://github.com/outshift-open/mas-lab/blob/main/docker/README.md)

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

- Browse and open agent, MAS, and experiment manifests from the mounted workspace.
- Validate manifests with the same checks as `mas-ctl validate`.
- Submit benchmark jobs, equivalent to `mas-lab benchmark run experiment.yaml`.
- Inspect recent runs, open `events.jsonl` (the machine-readable trace of a run), and view pipeline outputs under `results/`.
- Edit library objects (datasets, pipelines, experiments) when you develop a lab.

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
[`library-samples/sample-workspace/config.yaml`](https://github.com/outshift-open/mas-lab/blob/main/library-samples/sample-workspace/config.yaml))
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

Developer details: [`ui/README.md`](https://github.com/outshift-open/mas-lab/blob/main/ui/README.md) in the source tree.

---

## Controller API

Headless API only (no browser):

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
