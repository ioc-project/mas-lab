# Development

This page explains how to set up a development environment for `mas-lab`. Read it before you [contribute](CONTRIBUTING.md).

Smallest working command after cloning: `uv sync --all-packages`.

Package map: [docs/libraries.md](docs/libraries.md). Documentation layout: [doc map](docs/doc-map.md).

## Getting started

You need Python 3.11+ and [uv](https://docs.astral.sh/uv/). [go-task](https://taskfile.dev/) is optional. It runs the same gates as CI.

```bash
git clone https://github.com/ioc-project/mas-lab.git
cd mas-lab
uv sync --all-packages
export PATH="$PWD/.venv/bin:$PATH"
```

Verify the install:

```bash
mas-ctl --help
mas-lab --help
```

`mas-runtime` is the kernel library. Its console script only prints a pointer to `mas-ctl`. Use `mas-ctl` and `mas-lab` for day-to-day work.

Instead of `uv sync`, you can run `task install-dev`. It installs the packages in editable mode into `.venv`, with the test dependencies.

Configure LLM access for live runs as [Tutorial 0](docs/tutorials/00-environment-setup/README.md) describes: run `mas-lab init`, then export the API key.

## Development workflow

1. Open an issue, or comment on an existing one, before a large change.
2. Create a feature branch from `main`.
3. Make focused changes. Add tests where behavior changes.
4. Run the relevant test suites before you open a PR.
5. Sign off your commits (`git commit -s`). Use a `type(scope): subject` message with a short bullet list of the changes.
6. Open a pull request with a clear description and test plan.

## Running tests

```bash
# Core packages
uv run pytest runtime/tests/ -v
uv run pytest ctl/tests/ -v
uv run pytest lab/tests/ -v
uv run pytest library-standard/tests/ -v

# Tutorial integration tests (optional, may require API keys)
uv run pytest tests/tutorials/ -v
```

Three libraries (`library-skills`, `library-telemetry`, `library-kg`) have their own `tests/conftest.py`. Run each in a separate `pytest` invocation, for example `uv run pytest library-skills/tests/ -q`.

With go-task installed, use these tasks:

| Task | What it runs |
| --- | --- |
| `task verify-lint` | Lockfile, path literals, Ruff on changed Python. Run this before a push. |
| `task verify-unit` | Core unit tests (runtime, ctl, lab core/bench) plus import and manifest gates |
| `task verify-coverage` | `verify-unit` with `coverage.xml`, then the 90% changed-line gate |
| `task verify-library` | One library: `LIB=library-standard COV=mas.library.standard` |
| `task verify-controller` | Controller unit tests |
| `task verify-lab-smoke` | Lab smoke + envelope parity + cache invalidation |
| `task verify-golden-runs` | Golden `events.jsonl` fingerprints |
| `task verify-tutorials` | Tutorial CLI tests |
| `task validate` | Experiment dry-runs |
| `task verify-hitl-gate` | Scripted HITL gate |
| `task ci-fast` | `verify-lint` then `verify-unit` |
| `task ci` | Optional sequential run of every `verify-*` task. GitHub does not run this. |
| `task install-hooks` | Pre-push hook that runs `task verify-lint` |
| `task docs-gen` | Regenerate `docs/packages-reference.md`, `docs/plugins-reference.md` and path snippets; commit the result when you touch packages, plugins or path defaults |
| `task docs-serve` | Serve the documentation site locally |

GitHub Actions runs those `verify-*` tasks as separate jobs, not `task ci`. Lint starts immediately. Jobs wait for lint and run in parallel. Path groups (`scripts/ci_paths.py`) skip jobs the diff does not need: a docs PR does not run unit tests; a runtime PR does not run A2A/MCP or other libraries; each `library-*` tree has its own lint, pytest, and changed-line coverage job. MCP and A2A also run on a weekday nightly and on a published release. Python 3.11 runs on `main` (when unit tests run) and on nightly. Pushes to `main` also enforce the package 90% coverage report on the core unit job.

The merge proof is the GitHub check named **ci-summary** (the repository ruleset on `main` requires that exact name). A path flag of true means that job must succeed; skipped is a failure. A path flag of false allows skipped. `changes` and `lint` must succeed.

Before you push, run `task verify-lint` (`task install-hooks` installs that as a pre-push hook). GitHub Actions runs the individual `verify-*` tasks as parallel jobs. `task ci` is optional and sequential; there is no local stamp that substitutes for the GitHub run.

## Code style

- Use Python 3.11+ with type hints where practical.
- Keep lines to 120 characters (ruff).
- Match the naming and module layout of each package.
- Keep secrets in environment variables. Never commit credentials.

## Package layout

| Directory | Package |
| --- | --- |
| `runtime/` | `mas-runtime` |
| `ctl/` | `mas-ctl` |
| `lab/` | `mas-lab` (meta-package) |
| `lab/components/{core,bench,controller}/` | `mas-lab-core`, `mas-lab-bench`, `mas-lab-controller` |
| `library-standard/` | `mas-library-standard` |
| `library-samples/`, `library-lab/`, `library-eval/` | `mas-library-samples`, `mas-library-lab`, `mas-library-eval` |
| `library-skills/`, `library-ioa/`, `library-kg/`, `library-telemetry/` | `mas-library-skills`, `mas-library-ioa`, `mas-library-kg`, `mas-library-telemetry` |

The generated [packages reference](docs/packages-reference.md) lists every package with its dependencies and extras.

## Secrets and credentials

MAS Lab configuration holds environment variable names (for example `api_key_env: OPENAI_API_KEY`), not secret values. Never commit API keys, tokens, or private keys.

## Dependency updates

Update workspace dependencies in `pyproject.toml`, then run `uv sync`. Commit `uv.lock` with the change. `task verify-lock` checks that it is in sync.

Report supply-chain concerns through the private process in [SECURITY.md](SECURITY.md).

## License

Your contributions are licensed under the Apache License 2.0. See [LICENSE](LICENSE).
