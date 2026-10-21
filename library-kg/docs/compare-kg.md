# Comparing two knowledge graphs in a pipeline

The `compare_kg` pipeline step (`CompareKgStep` in `mas.library.kg.bench`) checks that two knowledge graphs (KGs) are structurally equivalent. It wraps [`run_compare_kg`](steps.md#run_compare_kg); the checks and Python API are in [kg-compare.md](kg-compare.md). Use it to compare a native KG with an OpenTelemetry (OTel) replay KG, or two run variants.

The checks cover agent coverage, node and edge types, delegation topology, and tool usage. They do not require byte-identical JSON.

## When to use

| Goal | Approach |
|------|----------|
| Native vs OTel-replay KG | `library-kg/pipelines/compare-two-kg.yaml` after `native-to-kg` + `native-to-kg-via-otel` |
| Native vs live plugin KGs | `library-kg/pipelines/compare-three-kg.yaml` (three-way comparison) |
| Ad-hoc two files | `compare_kg` config with `reference_kg` / `candidate_kg` paths |

## Benchmark-embedded (design time)

```yaml
run:
  post:
    - ref: kg:native-to-kg
    - ref: telemetry:native-to-kg-via-otel
    - ref: kg:compare-two-kg
```

## CLI attach (run time)

```bash
mas-lab benchmark run my/experiment.yaml \
  --pipeline run:kg:native-to-kg \
  --pipeline run:telemetry:native-to-kg-via-otel \
  --pipeline run:kg:compare-two-kg
```

## Standalone on existing runs

After benchmark output exists under `data/experiments/<name>/`:

```bash
mas-lab benchmark pipeline run \
  library-kg/pipelines/compare-two-kg.yaml \
  -o data/experiments/<name>
```

The step resolves the reference and candidate KG from its `depends_on` steps
by artifact name (defaults `reference_artifact: kg`, `candidate_artifact:
kg_otel`), or from explicit `reference_kg_path` / `candidate_kg_path`. Both KGs
must exist; otherwise override those keys in the pipeline YAML.

## Output

The step writes `kg_compare_report.json` in its step output directory, with
`{passed, checks, summary, ...}` (see [kg-compare.md](kg-compare.md)).
Config keys read by the step: `strict`, `fail_on_error`, `reference_artifact`,
`candidate_artifact`, `reference_kg_path`, `candidate_kg_path`. (The shipped
pipeline YAMLs also set `report_filename`; the step does not read it.)
Step module: `library-kg/src/mas/library/kg/bench.py`.

Batch API for offline use: `build_kg_document` in `mas.library.kg.pipeline`.
