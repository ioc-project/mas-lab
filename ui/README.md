<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-ui

> Web studio for designing MAS manifests, experiments, pipelines, and overlays.

Lives in **`mas-lab/ui/`** alongside the controller and runtime packages. The UI talks to
**mas-lab-controller** over HTTP for library CRUD, validation, job submit/poll, and manifest
schemas. **It does not read schema files from the repo filesystem.**

---

## Quick start (backend + UI)

### Prerequisites

- Python env with `mas-lab` installed (`uv sync` from repo root)
- Node.js 20+ and Yarn

### 1. Start the controller (backend)

From the **mas-lab repo root**:

```bash
task install          # from repo root — not bare uv sync
mas-lab serve
```

API listens on **http://localhost:8090** (`mas-lab serve --port` to change).
For the detached daemon only: `mas-lab control start` → default **9000**.

### 2. Run the UI (frontend)

In a second terminal:

```bash
cd ui
yarn install
export VITE_API_BASE_URL=http://localhost:8090
yarn dev
```

Open **http://localhost:5173** (or the port Vite prints).

If the controller is not running, the UI shows a bootstrap error — there is no offline schema
fallback.

### 3. Smoke test

1. Home (`/`) lists labs from the pinned workspace (`mas-lab serve --workspace` / `mas_lab.labs_search_paths`).
2. Open a library → Agent or MAS canvas loads.
3. Save / validate uses controller `POST /api/.../validate` plus client-side Ajv.
4. DevTools → Network: on page load you should see `GET /api/health`, then
   `GET /api/schemas/agent?resolved=1` and `GET /api/schemas/mas?resolved=1`.

---

## Architecture

```text
mas-lab-ui (browser)
    │  /api/info, /api/scopes, /api/scopes/{id}/items, /api/choices, /api/runs, /api/artifacts
    ▼
mas-lab-controller :8090  (mas-lab serve; daemon-only default is :9000)
    ▼
mas-runtime + mas-ctl + mas-lab-bench
```

The UI never reads the server filesystem. Run locally (`yarn dev` + `mas-lab serve`),
in Docker, or against a remote controller (connection screen: address + token).
`env-config.js`: `__MAS_LAB_API_BASE_URL__`, `__MAS_LAB_DEFAULT_SCOPE__` (alias
`__MAS_LAB_DEFAULT_LIBRARY__`), `__MAS_LAB_TOKEN__`.

Scripts: `yarn test`, `yarn e2e`, `yarn verify:api`, `yarn lint` (includes the
no-paths grep gate).

**Schemas:** `src/lib/loadManifestSchemas.ts` loads resolved JSON Schemas exclusively from
`GET /api/schemas/{agent,mas}?format=json&resolved=1`. The controller inlines fragment refs
via `mas.ctl.validate.schemas.load_schema` — same validation path as the CLI.

---

## Development

```bash
cd ui
yarn dev
yarn build
yarn lint
yarn test
yarn e2e
yarn verify:api
yarn verify:schemas   # requires running controller
yarn generate:types   # fetches resolved schemas from API → json2ts
yarn generate:api     # controller OpenAPI → src/api/generated
```

---

## Docker

See [docker/README.md](../docker/README.md). Quick smoke test:

```bash
cd docker
cp .env.example .env   # set OPENAI_API_KEY
docker compose up --build
```

---

## Documentation

| Document | Path |
|----------|------|
| User guide | [docs/user-guide.md](../docs/user-guide.md) |
| Controller / API | [lab/components/controller/README.md](../lab/components/controller/README.md) |
| Manifest schemas | [docs/manifest-schemas.md](../docs/manifest-schemas.md) |
| API contract tests | [lab/components/controller/tests/test_api_calls_contract.py](../lab/components/controller/tests/test_api_calls_contract.py) |
