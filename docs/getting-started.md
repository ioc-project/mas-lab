<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Install

<p class="eyebrow">Install, configure, run once</p>

Install MAS Lab, give it an LLM key, and record a first run. Pick the path that
matches how you work. The left menu has the full Docker and source-checkout
steps. This page is the short path and the first command.

| Path | Use it for | You need |
| --- | --- | --- |
| [Docker](getting-started/docker.md) | Running without a local Python environment | Docker, optional [go-task](https://taskfile.dev/) |
| [From source](getting-started/developer.md) | Editing MAS Lab itself | Python ≥ 3.11, `uv`, `task` |
| PyPI + uv (not yet published) | A released package | Python ≥ 3.11, `uv` |

If unsure, pick Docker. All paths use the same YAML manifests.

!!! note "PyPI publication pending"
    Package names are prepared, but the public packages are not uploaded yet.
    Use Docker or a source checkout until the first PyPI release is complete.

```bash
# Once published:
uv venv
uv pip install mas-lab mas-library-standard mas-library-samples
export PATH="$PWD/.venv/bin:$PATH"
mas-lab init
export OPENAI_API_KEY=<your-key>
```

## What you need

- A clone of the repository: `git clone https://github.com/ioc-project/mas-lab.git && cd mas-lab`
- An `OPENAI_API_KEY` for live LLM runs. Offline, only `mas-ctl validate` works.

Keep secrets out of YAML. The infra manifest names the environment variable
(`api_key_env`); the key itself lives in `docker/.env` or a gitignored `.env`.

## First run

After install, run the trip-planner sample and inspect the trace.

```bash
mas-ctl run-mas samples:apps/trip-planner/mas.yaml \
  --infra-ref standard:openai \
  --events-file "$PWD/traces/trip-planner.jsonl" \
  -q "Plan a trip from Celestia to Verdantia"

mas-lab telemetry show traces/trip-planner.jsonl
mas-lab plot trajectory traces/trip-planner.jsonl \
  --format html -o traces/trip-planner-trajectory.html
```

From a source checkout you can pass
`library-samples/apps/trip-planner/mas.yaml` instead of `samples:…`.
From Docker, prefix the same commands with
`docker compose --profile tools run --rm --no-deps cli`.

`traces/trip-planner.jsonl` is one JSON event per line. The plot is a
trajectory you can open in a browser.

Check the install without an LLM:

```bash
mas-ctl validate docs/tutorials/01-building-an-agent/agent.yaml
```

## Then continue

| Path | Link |
| --- | --- |
| How it is built | [Architecture](architecture.md) |
| Repository layout | [Repo structure](what-is/repo.md) |
| CLIs and UIs | [Surfaces](what-is/surfaces.md) · [Command line](cli/index.md) |
| Hands-on tutorials, labs, IoC integrations | [Discover](discover/index.md) |
| Tutorial 0 in full | [Environment setup](tutorials/00-environment-setup/README.md) |
| Day-to-day use | [User guide](user-guide.md) |
