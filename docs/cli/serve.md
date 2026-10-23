<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Controller API

`mas-lab serve` starts the lab controller: the HTTP API that the
[web UI](../ui/index.md) and the [live dashboard](../ui/live.md) talk to.

This is not [`mas-ctl serve`](mas-ctl.md#mas-ctl-serve-agent), which exposes
one agent over A2A.

```bash
mas-lab serve --host 127.0.0.1 --port 8090
```

| Flag | Meaning |
|------|---------|
| `--host` | Bind address. Loopback stays CORS-permissive; non-loopback binds send no CORS headers unless `--cors-origin` is set. |
| `--port` | Listen port (default 8090). |
| `--cors-origin URL` | Repeatable. Allowed browser origin for a remote UI. |
| `--token-file PATH` | Bearer token. Clients send `Authorization: Bearer`. None = open (warning on non-loopback). |
| `--state-dir PATH` | Persist jobs, runs, and uploads (SQLite + upload files). Default: the controller state directory. |
| `--workspace PATH` | Project folder for `config.yaml`, labs, libraries, and the writable Workspace library. Sets `MAS_WORKSPACE_ROOT`. An explicit `mas_lab.labs_search_paths` in that folder makes the catalog exclusive. |

Pin a lab or a workshop tree:

```bash
mas-lab serve --workspace ./labs/ioc-motivation.lab
mas-lab serve --workspace /workspace
```

Remote example:

```bash
mas-lab serve --host 0.0.0.0 --port 8090 \
  --token-file ./token \
  --cors-origin https://ui.example \
  --state-dir ./controller-state \
  --workspace ./labs/ioc-motivation.lab
```

Detached daemon: `mas-lab control start` (default port **9000**). Workers:
`mas-lab worker`.

Docker: `docker compose up` publishes the API on http://localhost:8090 and the
studio on http://localhost:8080. See [Web UI](../ui/index.md).

---

## Who calls it

| Client | Role |
|--------|------|
| [Web UI](../ui/index.md) | Authoring studio (manifests, labs, jobs) |
| [Live dashboard](../ui/live.md) (`task demo-ui`) | Topology, events, chat, metrics over one WebSocket |
| `mas-lab demo-ui export` | Writes a static dashboard; does not keep the API running |
| Scripts | `GET /api/health`, then the scope and run routes |

The UI renders only what the controller returns. It never builds a server
file path. Identity is a **scope id** (`library:<name>` / `lab:<name>`) plus
an **item id** (`name` or `name@version`). The UI never derives an id from
`metadata.name` and never displays a server path. A lab is not a library:
lab-local libraries are separate library scopes with `origin: lab`. Writes go
through `KindLayout`; runs persist under `--state-dir`; artifacts are read by
opaque id.

---

## HTTP surface

Auth is optional bearer (`--token-file`).

| Group | Examples |
|-------|----------|
| Health | `GET /api/health` |
| Catalog | `GET /api/scopes`, `GET /api/kinds`, `GET /api/scopes/{scope_id}/items` |
| Choices | `GET /api/choices/…` (models, tools, overlays, skills, step types) |
| Schemas | `GET /api/schemas`, `GET /api/schemas/{id}` |
| Runs | `GET/POST /api/runs`, `GET /api/runs/{id}`, events and artifacts |
| Live | WebSocket on the same origin as `serve` (live dashboard) |

Older `/api/libraries/**`, `/api/jobs/**`, and `/api/experiments/**` routes
remain as adapters for one release.

The process serves OpenAPI at `/openapi.json` (and `/docs` unless disabled).
A committed snapshot is `docs/schemas/controller-openapi.json` in the source
tree. Flags above win over the man page in `lab/docs/cli/serve.md`.

---

## Related

- [Command line](index.md)
- [mas-lab index](mas-lab.md)
- [Web UI](../ui/index.md)
- [Live dashboard](../ui/live.md)
