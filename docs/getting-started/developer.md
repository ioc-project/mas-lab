<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Install from source

<p class="eyebrow">Editable checkout</p>

Use this path to change the Python sources in `runtime/`, `ctl/`, or `lab/`.
Back to [Getting started](../getting-started.md) for the first-run commands.

## 1 — Install

Install Python ≥ 3.11, `uv` (`brew install uv`), `task` (`brew install go-task`),
and optionally `direnv`.

```bash
git clone https://github.com/ioc-project/mas-lab.git && cd mas-lab
direnv allow          # recommended; uses the committed .envrc -> ./.venv
uv sync               # workspace packages install editable
task install-dev      # editable packages plus test and sample dependencies
```

Without direnv, run `uv venv .venv` once, then `uv sync`. `uv run <cmd>` runs a
command in that environment. `task install-all` adds every optional library.

```bash
command -v mas-lab mas-ctl mas-runtime
mas-lab --help
task verify-unit
```

## 2 — Set the key

```bash
cp docs/tutorials/00-environment-setup/.env.example .env
# Edit .env and set OPENAI_API_KEY=<your-key>
```

`mas-ctl` and `mas-lab benchmark run` load `.env` from the current directory.
Never commit keys.

## 3 — Set the user config

```bash
mas-lab init          # writes ~/.config/mas/config.yaml
mas-lab config        # show effective paths
```

The user config holds `infra_refs` (manifests to register, such as
`standard:openai`) and `default_infra` (used when you pass no `--infra-ref`).
`mas-lab init --local` writes `config.yaml` in the project root instead.

## 4 — Common tasks

| Task | Purpose |
| --- | --- |
| `task verify` | Pre-commit gate |
| `task ci` | CI-parity suite |
| `task docs-serve` | Local MkDocs site at `http://127.0.0.1:8000` |

After `git pull`, run `task install` only if dependencies changed.

Next: the [first run](../getting-started.md#first-run) on Getting started, or
[Tutorial 0](../tutorials/00-environment-setup/README.md) for project-local
config and data paths.
