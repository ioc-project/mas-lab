#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from types import SimpleNamespace

from mas.lab.benchmark.metadata import BenchmarkMetadata, BenchmarkStatus
from mas.lab.benchmark.schedule.run_batch.finalize import update_metadata


def test_update_metadata_failed_when_pipeline_failed_even_if_executions_pass(tmp_path):
    mas_meta = BenchmarkMetadata.create_new(
        experiment_name="smoke",
        experiment_description="d",
        experiment_yaml_path="e.yaml",
        total_scenarios=2,
        run_dir=tmp_path,
    )
    prepared = SimpleNamespace(
        loaded_ids=["baseline", "with-guardrail"],
        dataset_items=[{"id": "a"}, {"id": "b"}],
        output_dir=tmp_path,
    )
    loaded = SimpleNamespace(n_runs=1, exp=SimpleNamespace(execution=None))
    execution = SimpleNamespace(total_ok=4, total_fail=0)
    update_metadata(
        mas_meta,
        prepared,
        loaded,
        execution,
        success=False,
        pipeline_error="Unknown step type: 'extract_trace_stats'",
    )
    assert mas_meta.status is BenchmarkStatus.FAILED
    assert mas_meta.failed_scenarios == 0
    assert mas_meta.metadata["pipeline_error"] == "Unknown step type: 'extract_trace_stats'"
    assert "extract_trace_stats" in mas_meta.metadata["error"]


def test_update_metadata_completed_when_executions_and_pipeline_pass(tmp_path):
    mas_meta = BenchmarkMetadata.create_new(
        experiment_name="smoke",
        experiment_description="d",
        experiment_yaml_path="e.yaml",
        total_scenarios=2,
        run_dir=tmp_path,
    )
    prepared = SimpleNamespace(
        loaded_ids=["baseline", "with-guardrail"],
        dataset_items=[{"id": "a"}, {"id": "b"}],
        output_dir=tmp_path,
    )
    loaded = SimpleNamespace(n_runs=1, exp=SimpleNamespace(execution=None))
    execution = SimpleNamespace(total_ok=4, total_fail=0)
    update_metadata(mas_meta, prepared, loaded, execution, success=True)
    assert mas_meta.status is BenchmarkStatus.COMPLETED
    assert mas_meta.failed_scenarios == 0
    assert "error" not in mas_meta.metadata
    assert "pipeline_error" not in mas_meta.metadata


def test_n_tests_counts_declared_pairs_even_when_items_skip_scenarios(tmp_path):
    mas_meta = BenchmarkMetadata.create_new(
        experiment_name="x",
        experiment_description="d",
        experiment_yaml_path="e.yaml",
        total_scenarios=2,
        run_dir=tmp_path,
    )
    prepared = SimpleNamespace(
        loaded_ids=["baseline", "guarded"],
        dataset_items=[{"id": "a"}, {"id": "control", "scenarios": ["baseline"]}],
        output_dir=tmp_path,
    )
    loaded = SimpleNamespace(n_runs=1, exp=SimpleNamespace(execution=None))
    update_metadata(mas_meta, prepared, loaded, SimpleNamespace(total_ok=3, total_fail=0), success=True)
    assert mas_meta.n_tests == 4  # show/list derive items per scenario from it
