<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-core

`mas-lab-core` is the foundation component of mas-lab. It validates multi-agent system (MAS) specifications, checks manifests for configuration hygiene, verifies contracts against traces, exports agent manifests, and pushes traces to an OpenTelemetry (OTel) collector.

Use it when you author a `mas.yaml` and want to check it, or when you extend the benchmark pipeline with custom steps.

```bash
mas-lab check library-samples/apps/trip-planner/mas.yaml
```

What it provides:

- Structural validation of MAS specifications (`mas.yaml`).
- Configuration hygiene checks across all manifests.
- Contract verification (Hook Interception, Protocol Validation, Governance Enforcement).
- Agent manifest export to OASF, AgentCard and AgentSpec.
- OTLP push from trace files to an OTel collector.
- A Processor API for registering custom post-processing steps in the pipeline system.

## Installation

```bash
# As a satellite of mas-lab
uv pip install -e "mas-lab[all]"

# Standalone
uv pip install -e mas-lab/components/core
```

---

## More examples

```bash
# Check configuration hygiene (no model names in the wrong files)
mas-lab check-config library-samples/apps/trip-planner
```

## Commands

### `check`

Validates a MAS specification for structural correctness: required fields, agent
references, flavour exports, overlay `kind` declarations, file references, and
dependency constraints.

```bash
mas-lab check path/to/mas.yaml
mas-lab check path/to/mas.yaml -v
mas-lab check path/to/mas.yaml --base-dir ./library-samples/apps/trip-planner
```

| Flag | Default | Description |
|------|---------|-------------|
| `-v / --verbose` | off | `-v` all checks, `-vv` debug, `-vvv` full debug |
| `--base-dir` | CWD | Base dir for resolving relative file references |

---

### `check-config`

Enforces the declarative-config separation rule: model names, endpoints, and
provider settings must live exclusively in flavour YAMLs. Any violation causes
`exit 1`.

**Rules enforced:**

| Rule | Triggered when |
|------|---------------|
| `MODEL_IN_NON_AGENT` | `model:` key appears in flavour / overlay / infra / mas.yaml |
| `ACCESS_IN_NON_FLAVOUR` | `api_base` / `api_key_env` / `provider` appear outside a flavour |

```bash
mas-lab check-config .
mas-lab check-config path/to/project
```

---

### `export`

Converts a MAS manifest to an external agent schema, for publishing agent capabilities to registries or discovery endpoints.

```bash
# List available schemas
mas-lab export --list

# Export to OASF
mas-lab export path/to/mas.yaml --schema oasf -o out.oasf.json

# Export to AgentCard (Google agent-remote compatible)
mas-lab export path/to/mas.yaml --schema agentcard --url https://agents.example.com/trip-planner

# YAML output enriched with a workflow
mas-lab export path/to/mas.yaml --schema oasf --format yaml \
  --workflow path/to/workflow.yaml -o out.oasf.yaml
```

| Flag | Default | Description |
|------|---------|-------------|
| `--schema` | — | `oasf` / `agentcard` / `agentspec` (omit to list) |
| `--format` | `json` | `json` or `yaml` |
| `-o / --output` | stdout | Output file path |
| `--url` | — | Deployed agent endpoint URL (required for `agentcard`) |
| `--workflow` | — | Path to a `kind: Workflow` YAML for skill enrichment |
| `--list` | off | List registered schemas and exit |

---

### `telemetry`

Converts a MAS trace file (`events.jsonl`) to the OpenTelemetry Protocol (OTLP) format and pushes the spans to a collector endpoint. For the full conversion and verification toolchain, see [library-telemetry](../../../library-telemetry/README.md).

```bash
mas-lab telemetry push events.jsonl --endpoint http://localhost:4318
mas-lab telemetry push events.jsonl --endpoint http://otel:4318 --service my-app
```

---

### `config`

Shows effective mas-lab configuration: XDG data paths, active `lab-config.yaml`,
and environment variable overrides.

```bash
mas-lab config
mas-lab config --json
```

---

## Contract verification framework

`mas-lab-core` includes a contract verification engine derived from the MaskAT paper. It supports three contract types:

| Contract | What it checks |
|----------|----------------|
| **Hook Interception** | Every LLM call passes through a registered `on_pre_llm_call` hook |
| **Protocol Validation** | Agent-to-agent messages conform to the declared semantic protocol |
| **Governance Enforcement** | No policy violation is produced at the MAS orchestrator level |

### Python API

```python
from mas.lab.contracts.executor import ContractExecutor, ContractType
from mas.lab.contracts.loader import load_contracts_from_yaml

contracts = load_contracts_from_yaml("components/core/contracts/bounded-recursion.yaml")
executor = ContractExecutor()
report = executor.run(contracts, trace_path="events.jsonl")

for result in report.results:
    print(result.contract_id, result.outcome)  # PASS / FAIL / AMBIGUOUS
```

---

## Processor API

The Processor API registers custom post-processing steps for use in `mas-lab benchmark pipeline` YAML files.

```python
from mas.lab.processor import Processor, ProcessorResult

class MyProcessor(Processor):
    step_type = "my_step"

    def run(self, config: dict, context: dict) -> ProcessorResult:
        ...
        return ProcessorResult(outputs={"result": ...})
```

Register in your package's entry points:

```toml
# pyproject.toml
[project.entry-points."mas.lab.processors"]
my_step = "my_package.processors:MyProcessor"
```

---

## Package layout

```
components/core/src/mas/lab/
├── artifacts.py              Output artifact helpers
├── config_hygiene.py         check-config rule engine
├── paths.py                  XDG data path resolution
├── processor.py              Processor ABC + registry
├── contracts/       # example YAML contracts
│   ├── executor.py           ContractExecutor (3 contract types)
│   ├── generator.py          Contract scaffolding from spec
│   ├── loader.py             YAML contract loader
│   └── reporter.py           Report formatter
├── schemas/
│   └── workflow.py           Workflow YAML schema
├── telemetry/
│   └── otlp_push.py          OTLP converter + HTTP push
└── utils/
    └── output_formatter.py   Table / JSON output helpers
```
