<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-controller

`mas-lab-controller` is the control plane for MAS Lab experiments. It runs long-lived work (benchmarks, agent runs, pipelines) in background workers, exposes a REST API for the web UI, and provides Unix-socket inter-process communication (IPC) for the CLI.

Use it when you start experiments through `mas-lab benchmark run` or the `ui/` web app; both talk to this daemon.

```bash
mas-lab serve    # foreground controller; HTTP API on http://localhost:8090
```

## Overview

| Piece | Role |
|-------|------|
| **Daemon** | Long-lived process: worker registry, HTTP server, Unix socket IPC |
| **Workers** | `benchmark`, `application`, `pipeline` jobs with captured stdout/stderr |
| **HTTP API** | FastAPI on port 9000 — libraries, CRUD, job submit/poll |
| **ManifestStore** | Reads/writes lab library YAML (experiments, pipelines, overlays, …) |
| **ControllerClient** | CLI helper: auto-start daemon, submit jobs, poll workers |

The CLI and UI share one job model: submit, receive a `job_id` / `worker_id`, then poll until the state is `completed`, `failed` or `cancelled`. Polling uses REST.


## Installation

```bash
# With the full mas-lab workspace
uv pip install -e ./lab/components/core \
               -e ./lab/components/bench \
               -e ./lab/components/controller \
               -e ./lab

# Or as part of mas-lab[all]
uv pip install -e "mas-lab[all]"
```

---

## Quick start

### 1. Start the controller (backend)

**Foreground** (recommended for development — logs in terminal):

```bash
task install              # from repo root — installs mas-lab-controller into .venv
mas-lab serve
# HTTP API → http://localhost:8090  (use --port to change)
# Unix socket → $XDG_DATA_HOME/mas/controller.sock
```

**Background daemon** (default HTTP port 9000):

```bash
mas-lab control start
mas-lab control status    # confirm running + port
```

Stop: `mas-lab control stop`

### 2. Connect mas-lab-ui (frontend)

In the `ui/` directory at the mas-lab repo root:

```bash
yarn install
export VITE_API_BASE_URL=http://localhost:8090
yarn dev
# Open http://localhost:5173 (Vite default)
```

The UI calls `/api/libraries/*`, `/api/jobs/*`, `/api/schemas/*`, and related routes.
Manifest schemas are served from installed packages (`mas-runtime`, `mas-lab-bench`,
`mas-lab-core`) — not vendored inside the controller. Contract tests mirror the UI
surface (`lab/components/controller/tests/test_api_calls_contract.py`).

### 3. Run a benchmark via the daemon

All `mas-lab benchmark run` invocations go through the controller:

```bash
# Blocking: submit + poll + stream stdout/stderr until done
mas-lab benchmark run my-lab/experiments/smoke.yaml --dry-run

# Detached: print worker id and exit
mas-lab benchmark run my-lab/experiments/smoke.yaml --dry-run -b

# Follow a detached worker
mas-lab worker follow <worker_id>
```

---

## Architecture

```text
┌─────────────┐     Unix socket      ┌──────────────────┐
│  mas-lab    │ ───────────────────► │  Controller      │
│  CLI        │   submit_benchmark   │  daemon          │
│             │   get_worker (poll)  │                  │
└─────────────┘                      │  WorkerRegistry  │
                                     │  WorkerRunner    │
┌─────────────┐     HTTP :9000       │  ManifestStore   │
│  mas-lab-ui │ ───────────────────► │  FastAPI app     │
│  (React)    │   /api/jobs/{id}     │  schema_registry │
└─────────────┘   /api/schemas/*     └────────┬─────────┘
     polls ──────────────────────────────────┤
                                              ▼
                                    benchmark / application / pipeline workers
                                    (stdout/stderr → pollable buffers)
```

| Transport | Used by | Typical calls |
|-----------|---------|---------------|
| Unix socket | CLI | `submit_benchmark`, `get_worker`, `cancel_worker`, `ping` |
| HTTP REST | UI | `POST /api/libraries/{lib}/benchmark/run`, `GET /api/jobs/{id}` |

Environment variables (defaults):

| Variable | Default | Purpose |
|----------|---------|---------|
| `MAS_HOME` | `$XDG_DATA_HOME/mas` | Config and socket parent dir |
| `MAS_CONTROLLER_SOCKET` | `$XDG_DATA_HOME/mas/controller.sock` | Unix socket path |
| `MAS_CONTROLLER_PORT` | `9000` | HTTP API port |

---

## CLI commands

```bash
mas-lab control start|stop|status|restart   # daemon lifecycle
mas-lab worker list [--kind benchmark]      # list workers
mas-lab worker show <id>                    # JSON detail
mas-lab worker follow <id>                  # poll + stream until done
mas-lab worker cancel <id>
mas-lab serve                               # foreground controller + HTTP
```

Benchmark and application runs are submitted through the daemon automatically;
see [mas-lab-bench](../bench/README.md) for experiment YAML format.

---

## Package layout

```
components/controller/src/mas/lab/controller/
├── daemon.py           # Main loop: socket IPC + HTTP
├── fastapi_app.py      # REST API (mas-lab-ui contract)
├── api.py              # ControllerAPI (libraries, workers, manifests)
├── client.py           # ControllerClient, follow_worker, start_daemon
├── workers.py          # Benchmark / application / pipeline workers
├── io_capture.py       # Redirect stdout/stderr into worker records
├── manifest_store.py   # Lab library CRUD on disk
├── registry.py         # WorkerRegistry
├── cli.py              # mas-lab control / worker commands
├── schema_registry.py  # Resolve schemas from mas-runtime / bench / core packages
└── schemas/            # Controller-local pipeline step-type registry only
```

---

## Documentation

### User documentation

| Document | Description |
|----------|-------------|
| [../../docs/user-guide.md](../../docs/user-guide.md) | End-to-end MAS Lab user guide |
| [../../docs/benchmark.md](../../docs/benchmark.md) | Benchmark quickstart and lifecycle |

### Developer documentation

| Document | Description |
|----------|-------------|
| [../../../docs/references/schemas.md](../../../docs/references/schemas.md) | Package-owned schemas, `/api/schemas`, UI vs runtime |
| [../../../runtime/docs/dev/schemas/agent-manifest.md](../../../runtime/docs/dev/schemas/agent-manifest.md) | Runtime agent manifest reference |
| [../../../runtime/docs/dev/schemas/mas-topology.md](../../../runtime/docs/dev/schemas/mas-topology.md) | MAS topology / workflow reference |
| [../bench/README.md](../bench/README.md) | Experiment and post-processing pipeline YAML |

### Related components

| Component | README |
|-----------|--------|
| `mas-lab-core` | [../core/README.md](../core/README.md) |
| `mas-lab-bench` | [../bench/README.md](../bench/README.md) |
| `mas-lab-ui` | [mas-lab-ui README](https://github.com/mas-framework/mas-lab-ui) |
