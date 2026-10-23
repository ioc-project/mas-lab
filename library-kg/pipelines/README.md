# KG pipeline library

Reusable `kind: Pipeline` manifests shipped with **mas-library-kg**. Steps delegate
to `mas-lab-graph` (`normalize_events`, `validate_kg`, `compare_kg`, …) and
**library-kg** algorithms.

## Referencing pipelines

Install `mas-library-kg` in the workspace venv (scope-integration `task init` does
this). The package registers scheme **`library-kg`** via
`mas.runtime.manifest_libraries` — no `config.yaml` entry required.

```yaml
run:
  post:
    - ref: kg:native-to-kg
```

Shorthand id form:

```yaml
run:
  post:
    - id: native-to-kg
      library: library-kg
```

CLI attach (same ref string):

```bash
mas-lab benchmark run experiment.yaml \
  --pipeline run:kg:native-to-kg
```

## Pipeline catalog

| File | Output | Purpose |
|------|--------|---------|
| `native-to-kg.yaml` | `kg.json`, `validation_report.json` | Verify `events.jsonl` → normalize → validate KG |
| `native-plots.yaml` | `trajectory-native.html`, `trajectory-kg.html` | Trajectory plots (run after `native-to-kg`) |
| `compare-two-kg.yaml` | `parity_report.json` | Pairwise `compare_kg` (`kg` vs `kg_otel` by default) |
| `compare-three-kg.yaml` | `parity_native_vs_*.json` | Native vs otel-plugin / observe-plugin KGs |

## Compare two KGs

**Design time** — compose pipelines on the same `run.post` list (steps share names
such as `normalize-native`):

```yaml
run:
  post:
    - ref: kg:native-to-kg
    - ref: telemetry:native-to-kg-via-otel
    - ref: kg:compare-two-kg
```

**After a benchmark** — standalone pipeline on an existing output tree:

```bash
mas-lab benchmark pipeline run \
  thirdparty/mas-lab-internal/library-kg/pipelines/compare-two-kg.yaml \
  -o data/experiments/my-experiment
```

Override artifact keys via step config (`reference_artifact`, `candidate_artifact`,
`report_filename`). Implementation: `mas-lab-graph` → `CompareKGStep`.

See also [compare-kg.md](../docs/compare-kg.md).
