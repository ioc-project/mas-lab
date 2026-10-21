<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# `mas-lab serve`

Starts the controller HTTP API the web UI talks to.

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
