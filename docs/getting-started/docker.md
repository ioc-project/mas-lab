<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Install with Docker

<p class="eyebrow">No local Python environment</p>

Use this path for demos, benchmarks, and running the CLI without installing
Python packages on the host. Back to [Getting started](../getting-started.md)
for the first-run commands.

## 1 — Configure LLM access

LLM access has three parts. Keep secrets out of YAML.

| Piece | What it is | Where it lives |
| --- | --- | --- |
| **Infra manifest** | Endpoint settings: API base URL, allowed models, `api_key_env` name (no secret) | `infra_refs` in `config.yaml`, or `--infra-ref` |
| **Credential** | API key or token | `docker/.env` |
| **Flavour** | Named runtime environment; `local` is the default | `mas_ctl.flavour` / `mas_lab.flavour` |

The bundled `standard:openai` and `standard:ollama` infra need no `mas-lab init`. Set the key:

```bash
cp docs/tutorials/00-environment-setup/.env.example docker/.env
# Edit docker/.env — set OPENAI_API_KEY=<your-key>
```

For an OpenAI-compatible proxy, pass `--infra-ref standard:llm-proxy` and set
`LLM_PROXY_API_BASE` next to the key.

## 2 — Build the images

```bash
cd docker
docker compose build backend   # CLI + controller API; first build takes several minutes
# UI (optional): docker compose build ui
```

For a clean rebuild, run `task docker-rebuild`.

## 3 — Run the CLI

Prefix every `mas-ctl` / `mas-lab` command with the `cli` service (profile `tools`):

```bash
docker compose --profile tools run --rm --no-deps cli mas-lab config
docker compose --profile tools run --rm --no-deps cli mas-ctl validate \
  docs/tutorials/01-building-an-agent/agent.yaml
```

The first two commands run offline. `chat` and `run-mas` need the key or an
llm-cache replay. Use `cli`, not `backend` (the long-running API service).

In the container, `/opt/mas-lab` is a copy of the repository baked into the
image. Your host checkout is mounted at `/workspace`. Use `/workspace/<path>`
for files you edited after the build.

| Container path | Host (default) | Purpose |
| --- | --- | --- |
| `/workspace` | Repository root | `labs/`, tutorials |
| `/data` | `docker/data/` | Trace cache, benchmark outputs |

## 4 — Start the web UI (optional)

```bash
cd docker && docker compose up -d      # or from the repo root: task start
```

Open the UI at <http://localhost:8080>. Put secrets in `docker/.env` only. A
shell `export` does not reach containers.

Next: the [first run](../getting-started.md#first-run) on Getting started, or
[Tutorial 0](../tutorials/00-environment-setup/README.md) for extra checks.
