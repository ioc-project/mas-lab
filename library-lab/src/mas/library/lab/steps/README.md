# Pipeline step library (OSS)

One **step type** = one **Python module** under a category folder. This
library ships step *implementations* only — the pipeline execution engine
(`Pipeline`, `PipelineExecutor`, `PipelineStep` base class, caching,
dependency resolution) lives in `mas-lab-bench`
(`mas.lab.benchmark.pipeline`), which this library depends on but does not
duplicate.

| Category | Path | Step types |
|----------|------|------------|
| **extract** | `extract/` | `extract_trace_stats`, `extract_mealy_stats`, `extract_sys_stats`, `extract_trajectories` |
| **eval** | `eval/` | `eval_mce`, `metrics_to_dataframe`, `eval_adversarial`, `annotate_metrics`, `collect_metrics`, `compute_ci`, `compute_drift`, `validate_outputs` |
| **viz** | `viz/` | `plot`, `plot_trajectory`, `plot_multilevel_trajectory`, `plot_communication_flow`, `plot_message_graph`, `ggplot`, `plotnine`, `ci_plot`, `metrics_comparison_plot`, `pipeline_diagram` |
| **data** | `data/` | `dataset`, `experiment`, `analysis`, `to_dataframe`, `join_dataframe`, `collect_dataframe`, `gather_level`, `diff_trajectories`, `embed_trajectories`, `generate_dataset`, `serialize`, `deserialize`, `processor` |
| **services** | `services/` | `service_start`, `service_stop`, `export_otel`, `file_tail`, `shell` |

Shared utilities (not registered step types) stay in the bench engine:
`mas.lab.benchmark.pipeline.lib.data_source`, `mas.lab.benchmark.pipeline.lib.plot_lib`.

Registration is declarative, through the runtime plugin registry — see
[`library.yaml`](../../../../../library.yaml)'s `types:`/`plugins:` block
and [`runtime/docs/plugin-registry-manifests.md`](../../../../../../runtime/docs/plugin-registry-manifests.md).
There is no bench-local step registry; `mas-lab-bench`'s pipeline executor
resolves step types by asking `mas.runtime.registry.get_registry()`
directly.

## Internal extensions

Steps that depend on corporate infra or KG pipelines live in **`mas-lab-internal/lab-components/bench-steps`** and are declared in manifest YAML the same way, resolved by the same runtime registry. Examples: `embed_states`, `list_clickhouse_sessions`.

## Adding a step

1. Add `steps/<category>/<name>.py` with a single `PipelineStep` subclass; set `type = "<step_id>"`.
2. Export the class from `steps/__init__.py` and declare `type: step` / `name:` / `module:` / `class:` in `library.yaml`'s `plugins:` list.
3. Document the step in `lab/docs/pipeline-steps.md`.
