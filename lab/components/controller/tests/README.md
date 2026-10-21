# Controller Tests

Tests for the `mas-lab-controller` package — the daemon, HTTP API, IPC,
manifest discovery, session management, and worker infrastructure.

## Running

```bash
task verify-controller
# or directly:
.venv/bin/pytest lab/components/controller/tests/ -q --tb=line
```

## Test files

### Daemon, IPC, and sessions

| File                       | Scope                                        |
| -------------------------- | -------------------------------------------- |
| `test_daemon.py`           | Daemon IPC and lifecycle                     |
| `test_client.py`           | `ControllerClient` with mocked Unix socket   |
| `test_ipc_and_workers.py`  | IPC client, worker registry, `ControllerAPI` |
| `test_sessions.py`         | Session acquire/release and idle shutdown    |
| `test_workers_extended.py` | Worker and registry edge cases               |

### Controller API and runners

| File                           | Scope                                                   |
| ------------------------------ | ------------------------------------------------------- |
| `test_controller.py`           | `ApplicationRunner` registry and `ControllerAPI` basics |
| `test_api_extended.py`         | Extended `ControllerAPI` coverage                       |
| `test_runners.py`              | `ApplicationRunner` types                               |
| `test_runtime_runners_api.py`  | Listing runtime runner plugins                          |
| `test_agent_chat.py`           | In-process agent chat helpers                           |
| `test_benchmark_cli_daemon.py` | Benchmark CLI submission via controller daemon          |

### Discovery and manifests

| File                          | Scope                                                   |
| ----------------------------- | ------------------------------------------------------- |
| `test_manifest_store.py`      | `ManifestStore` CRUD and queries                        |
| `test_manifest_validation.py` | In-process manifest validation (no subprocess)          |
| `test_lab_discovery.py`       | Nested lab artifact discovery and MAS schema validation |
| `test_lab_registry.py`        | Unified `LabRegistry`                                   |
| `test_library_discovery.py`   | Registry-backed library discovery                       |

### HTTP / FastAPI route tests (UI surface)

These test the controller's REST API surface — the same endpoints the UI
(`ui/src/api/apiCalls.ts`) calls. They use `starlette.testclient.TestClient`
(in-process, no running server needed).

| File                         | Scope                                                                                                                  |
| ---------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| `test_fastapi_app.py`        | Core CRUD endpoints: libraries, experiments, pipelines, overlays, datasets, tools, skills, config files, MAS resources |
| `test_fastapi_extended.py`   | Jobs, validate, overlays conflict, topologies, scenarios                                                               |
| `test_api_calls_contract.py` | Contract tests mirroring every call in `apiCalls.ts` — ensures the UI HTTP surface stays compatible                    |
| `test_scopes_api.py`         | `/api/scopes` for versioned/lab/readonly/root-app fixtures                                                             |
| `test_items_read.py` / `test_items_write.py` | Item list/get/create/fork/rename/delete, no paths in JSON |
| `test_layout_locate.py` / `test_write_safety.py` | Catalog locate + name/symlink safety |
| `test_choices_api.py`        | Models, design patterns, tools/skills/overlays with refs                                                               |
| `test_runs_api.py` / `test_artifacts_api.py` / `test_uploads_api.py` | Runs, artifacts, uploads |
| `test_auth_cors.py`          | `--token-file` and CORS                                                                                                |
| `test_openapi_contract.py`   | Committed `docs/schemas/controller-openapi.json`                                                                       |
| `test_deprecated_routes.py`  | Old `/api/libraries` adapters still work and are deprecated                                                            |
| `test_job_store.py`          | Persistent job store                                                                                                   |

Scope fixtures: `fixtures/versioned_lib`, `lab_with_local_lib`, `lab_without_library`, `readonly_lib`, `root_app_lib`.
| `test_schema_registry.py`    | `/api/schemas` endpoint and schema resolution across packages                                                          |

### Other

| File                    | Scope                                             |
| ----------------------- | ------------------------------------------------- |
| `test_coverage_gaps.py` | Additional coverage for controller infrastructure |

## Fixtures

### `demo_lab` — stable golden data

`fixtures/demo_lab/` is a self-contained, version-controlled lab tree copied from
`library-samples`. It is copied into `tmp_path` for each test so CRUD operations
never mutate the checked-in fixtures.

Used by: `test_fastapi_app.py`, `test_fastapi_extended.py`, `test_api_calls_contract.py`.

```
fixtures/demo_lab/
├── library.yaml
├── apps/trip-planner/          # MAS app with mas.yaml + agent manifests
│   ├── mas.yaml
│   └── agents/
├── experiments/                # trip-planner-design-pattern-experiment.yaml
├── pipelines/                  # pipeline-test.yaml
├── overlays/                   # cot-moderator, react-moderator, reflection-moderator, ...
├── datasets/                   # benchmark.yaml, qa-agent-default.yaml, trip-planner/, ...
├── tools/                      # calc, web-search, and other .tool.yaml files
├── skills/answer-formatting/   # SKILL.md
└── infra/tool-providers.yaml
```

Why not use `library-samples` directly? `library-samples` is a living library
that changes over time. Pinning to a local copy prevents test flakiness when
upstream manifests are updated.

### `sample_lab` — minimal scaffolding

Defined in `conftest.py`. Creates a bare-bones lab directory structure in
`tmp_path` with minimal inline YAML manifests. Used by tests that only need a
valid directory layout and don't care about realistic content.

### `templates` — YAML templates for POST/PUT operations

`fixtures/templates/` contains YAML manifests used as request bodies in CRUD tests.
These are **not** part of the `demo_lab` tree — they represent content the UI would
submit when creating new resources.

| Template                   | Used for                                                                |
| -------------------------- | ----------------------------------------------------------------------- |
| `new-experiment.yaml`      | Creating experiments (validated against `experiment.schema.yaml`)       |
| `new-mas.yaml`             | Creating MAS resources                                                  |
| `new-mas-coordinator.yaml` | Coordinator agent for new MAS                                           |
| `new-mas-researcher.yaml`  | Researcher agent for new MAS                                            |
| `validate-agent.yaml`      | Agent validation (intentionally missing `metadata.name`)                |
| `validate-mas.yaml`        | MAS manifest validation                                                 |
| `validate-overlay.yaml`    | Overlay validation                                                      |
| `validate-pipeline.yaml`   | Pipeline validation (validated against `pipeline-manifest.schema.json`) |

## Schema validation in tests

Where possible, the HTTP route tests validate round-tripped manifests against the
real JSON schemas in `docs/schemas/` rather than asserting individual fields. This
is more thorough (catches any schema violation) and more maintainable.

```python
from mas.ctl.validate.validator import validate_data
result = validate_data(parsed, kind="experiment", strict=True, resolve_refs=False)
assert result.ok
```

`resolve_refs=False` skips file-path reference checking (e.g. verifying overlay
files exist on disk) but still validates the full document structure via
`jsonschema.Draft7Validator`.

The pipeline validate endpoint (`/api/libraries/{lib}/pipelines/validate`) runs
real schema validation against `pipeline-manifest.schema.json` without mocking.

### Schema locations

- **Runtime**: `docs/schemas/runtime/` — agent, mas, overlay, workflow, infra, flavour, tool, ...
- **Lab/Bench**: `docs/schemas/lab/` — experiment, pipeline, dataset, lab-config, ...
- **Top-level**: `docs/schemas/` — deployment, checkpoint, memory-seed, library, ...

## Mocks

| Mock fixture            | What it stubs                                      | Why                                                         |
| ----------------------- | -------------------------------------------------- | ----------------------------------------------------------- |
| `mock_submit_job`       | `mas.lab.controller.jobs.submit_job`               | Prevents spawning real subprocesses for run/benchmark jobs  |
| `mock_overlay_validate` | `mas.lab.controller.deps.validate_overlay_content` | Overlay validation uses async subprocess calls to `mas-ctl` |
| `mock_cli`              | `mas.lab.controller.routes.*.run_cli_command`      | Prevents shelling out to `mas-lab` CLI                      |

Pipeline and experiment schema validation run without mocks — the templates
are designed to pass real validation.

## TODO

The goal is to make `demo_lab` the single source of golden data for all
controller tests and eventually **remove `sample_lab` entirely**.

Progress so far:
- [x] `test_fastapi_app.py` migrated to `demo_lab`
- [x] `test_fastapi_extended.py` migrated to `demo_lab`
- [x] `test_api_calls_contract.py` migrated to `demo_lab`
- [ ] Migrate remaining tests that use `sample_lab` to `demo_lab`
- [ ] Remove `sample_lab` fixture from `conftest.py`

As `demo_lab` is enhanced to cover the full manifest surface:
- Any external library (e.g. `library-samples`) should contain a **subset** of
  what is already present in `demo_lab`, not the other way around.
- New manifest kinds, fields, or endpoints should be added to `demo_lab` first,
  with corresponding test coverage.
- Templates in `fixtures/templates/` should be validated against their schemas
  and match what the UI actually produces.

This ensures:
- **Complete test coverage** — every manifest kind and API operation is exercised
  against realistic, schema-valid data.
- **Early detection of breaking changes** — schema or API changes break tests
  immediately, before they reach `library-samples` or production.
