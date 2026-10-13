#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for unified pipeline resolution and phase execution.

Coverage
--------
* ``resolve_pipeline_specs()`` — in isolation (mock exp objects)
* ``materialize_step_dicts()`` — phase filtering
* ``merge_pipeline_attachments()`` — dict mutation from CLI ``--pipeline`` flags
* ``MASExperimentConfig.from_data()`` — constructs exp from in-memory dict
* CLI attachment → ``from_data`` → ``all_pipeline_steps()`` end-to-end

The last two groups were missing before and allowed a bug to ship: CLI-attached
pipelines were merged into the raw validation dict but the exp object was
constructed from the YAML file, so ``all_pipeline_steps()`` never saw them.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from mas.lab.benchmark.execution.pipeline_attach import merge_pipeline_attachments
from mas.lab.benchmark.schedule.pipeline import materialize_step_dicts
from mas.lab.benchmark.schedule.pipeline_resolve import resolve_pipeline_specs
from mas.lab.lab.config import PipelineStepSpec
from mas.lab.lab.config.mas_experiment import MASExperimentConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MINIMAL_EXPERIMENT = {
    "experiment": {
        "name": "test-exp",
        "application": {"manifest": "mas.yaml"},
    }
}


# ---------------------------------------------------------------------------
# resolve_pipeline_specs — mock exp objects
# ---------------------------------------------------------------------------

def test_resolve_inline_specs_from_experiment_dict():
    class _Exp:
        name = "demo"
        pipeline = [
            PipelineStepSpec(type="extract_trace_stats", name="trace-stats", phase="post"),
            PipelineStepSpec(type="service_start", name="start", phase="pre"),
        ]

        def all_pipeline_steps(self):
            return list(self.pipeline)

    specs = resolve_pipeline_specs(_Exp(), Path("/tmp/experiment.yaml"))
    assert len(specs) == 2
    assert {s.phase for s in specs} == {"pre", "post"}


def test_resolve_does_not_infer_sibling_pipeline_yaml(tmp_path: Path):
    sibling = tmp_path / "pipeline.yaml"
    sibling.write_text(
        yaml.dump(
            {
                "pipeline": {
                    "name": "post",
                    "steps": [
                        {"name": "stats", "type": "extract_trace_stats", "phase": "post"},
                    ],
                }
            }
        ),
        encoding="utf-8",
    )

    class _Exp:
        name = "demo"
        pipeline = []

        def all_pipeline_steps(self):
            return []

    specs = resolve_pipeline_specs(_Exp(), tmp_path / "experiment.yaml")
    assert specs == []


def test_resolve_named_pipeline_ref(tmp_path: Path):
    dest = tmp_path / "post.yaml"
    dest.write_text(
        yaml.dump(
            {
                "pipeline": {
                    "name": "post",
                    "steps": [
                        {
                            "name": "eval-quality",
                            "type": "eval_mce",
                            "phase": "post",
                            "scope": "run",
                            "in": "trace",
                            "out": "metrics",
                        },
                    ],
                }
            }
        ),
        encoding="utf-8",
    )

    class _Exp:
        name = "demo"
        pipeline = []
        pipeline_ref = str(dest)

        def all_pipeline_steps(self):
            return []

    specs = resolve_pipeline_specs(_Exp(), tmp_path / "experiment.yaml")
    assert len(specs) == 1
    assert specs[0].type == "eval_mce"
    assert specs[0].scope == "run"
    assert specs[0].inputs == ["trace"]
    assert specs[0].outputs == ["metrics"]


def test_materialize_filters_by_phase():
    specs = [
        PipelineStepSpec(type="service_start", name="start", phase="pre"),
        PipelineStepSpec(type="extract_trace_stats", name="stats", phase="post"),
    ]
    pre = materialize_step_dicts(specs, phase="pre", scenario_ids=["s1"], infra_name=None, step_overrides={})
    post = materialize_step_dicts(specs, phase="post", scenario_ids=["s1"], infra_name=None, step_overrides={})
    assert [s["name"] for s in pre] == ["start"]
    assert [s["name"] for s in post] == ["stats"]


# ---------------------------------------------------------------------------
# merge_pipeline_attachments — dict structure
# ---------------------------------------------------------------------------

def test_merge_injects_ref_into_correct_level_and_phase():
    """merge_pipeline_attachments must place {ref:} entries under experiment.<level>.<phase>."""
    import copy
    raw = copy.deepcopy(_MINIMAL_EXPERIMENT)
    result = merge_pipeline_attachments(raw, [
        "run:post:pipelines/stats.yaml",
        "scenario:pre:pipelines/setup.yaml",
    ])
    assert result["experiment"]["run"]["post"] == [{"ref": "pipelines/stats.yaml"}]
    assert result["experiment"]["scenario"]["pre"] == [{"ref": "pipelines/setup.yaml"}]


def test_merge_appends_to_existing_level_phase():
    """CLI attachments must append, not overwrite, existing pipeline entries."""
    import copy
    raw = copy.deepcopy(_MINIMAL_EXPERIMENT)
    raw["experiment"]["run"] = {"post": [{"type": "extract_trace_stats", "name": "existing"}]}
    result = merge_pipeline_attachments(raw, ["run:post:pipelines/extra.yaml"])
    assert len(result["experiment"]["run"]["post"]) == 2
    assert result["experiment"]["run"]["post"][1] == {"ref": "pipelines/extra.yaml"}


def test_merge_phase_defaults_to_post():
    """LEVEL:REF (no phase) must inject into post."""
    import copy
    raw = copy.deepcopy(_MINIMAL_EXPERIMENT)
    result = merge_pipeline_attachments(raw, ["run:pipelines/stats.yaml"])
    assert result["experiment"]["run"]["post"] == [{"ref": "pipelines/stats.yaml"}]


# ---------------------------------------------------------------------------
# MASExperimentConfig.from_data — level pipeline visibility
# ---------------------------------------------------------------------------

def test_from_data_sees_inline_level_pipeline_steps():
    """from_data must populate exp.levels from the supplied dict, not re-read the file."""
    data = {
        "experiment": {
            "name": "test-exp",
            "application": {"manifest": "mas.yaml"},
            "run": {"post": [{"type": "extract_trace_stats", "name": "stats"}]},
            "scenario": {"pre": [{"type": "service_start", "name": "start"}]},
        }
    }
    exp = MASExperimentConfig.from_data(data, Path("/tmp/exp.yaml"))
    steps = exp.all_pipeline_steps()
    assert len(steps) == 2
    by_type = {s.type: s for s in steps}
    assert by_type["extract_trace_stats"].scope == "run"
    assert by_type["extract_trace_stats"].phase == "post"
    assert by_type["service_start"].scope == "scenario"
    assert by_type["service_start"].phase == "pre"


# ---------------------------------------------------------------------------
# merge + from_data end-to-end  (the bug scenario)
# ---------------------------------------------------------------------------

def test_cli_pipeline_attachment_reaches_all_pipeline_steps(tmp_path: Path):
    """CLI --pipeline attachments must appear in exp.all_pipeline_steps().

    This test covers the bug where merge_pipeline_attachments patched the raw
    validation dict but the exp object was constructed from the YAML file, so
    CLI-attached pipelines were silently dropped.
    """
    # Create a real pipeline file the attachment will reference
    pipeline_yaml = tmp_path / "stats.yaml"
    pipeline_yaml.write_text(
        yaml.dump({
            "pipeline": {
                "name": "stats",
                "steps": [{"name": "s", "type": "extract_trace_stats", "phase": "post"}],
            }
        }),
        encoding="utf-8",
    )

    import copy
    raw = copy.deepcopy(_MINIMAL_EXPERIMENT)
    merged = merge_pipeline_attachments(raw, [f"run:post:{pipeline_yaml}"])

    exp = MASExperimentConfig.from_data(merged, tmp_path / "exp.yaml")
    steps = exp.all_pipeline_steps()

    assert len(steps) == 1, (
        "CLI-attached pipeline step must be visible in all_pipeline_steps(). "
        "If this fails, from_data is not using the merged dict."
    )
    assert steps[0].type == "extract_trace_stats"
    assert steps[0].scope == "run"
    assert steps[0].phase == "post"


def test_multiple_cli_attachments_across_levels(tmp_path: Path):
    """Multiple --pipeline flags across different levels must all appear."""
    def _make_pipeline(name: str, step_type: str) -> Path:
        p = tmp_path / f"{name}.yaml"
        p.write_text(
            yaml.dump({
                "pipeline": {
                    "name": name,
                    "steps": [{"name": name, "type": step_type, "phase": "post"}],
                }
            }),
            encoding="utf-8",
        )
        return p

    run_pipe = _make_pipeline("run-stats", "extract_trace_stats")
    scenario_pipe = _make_pipeline("scenario-stats", "extract_mealy_stats")

    import copy
    raw = copy.deepcopy(_MINIMAL_EXPERIMENT)
    merged = merge_pipeline_attachments(raw, [
        f"run:post:{run_pipe}",
        f"scenario:post:{scenario_pipe}",
    ])

    exp = MASExperimentConfig.from_data(merged, tmp_path / "exp.yaml")
    steps = exp.all_pipeline_steps()

    assert len(steps) == 2
    types = {s.type for s in steps}
    assert "extract_trace_stats" in types
    assert "extract_mealy_stats" in types
    scopes = {s.scope for s in steps}
    assert scopes == {"run", "scenario"}


def test_yaml_and_cli_pipelines_compose_into_same_structure(tmp_path: Path):
    """YAML-declared pipelines + CLI --pipeline flags = union of all steps.

    Whether steps come from the YAML, from the CLI, or a mix, the result must
    be identical to declaring all of them in one place.  This test verifies
    that the 'one in-memory structure' invariant holds.
    """
    cli_pipe = tmp_path / "cli.yaml"
    cli_pipe.write_text(
        yaml.dump({
            "pipeline": {
                "name": "cli",
                "steps": [{"name": "cli-step", "type": "extract_mealy_stats", "phase": "post"}],
            }
        }),
        encoding="utf-8",
    )

    # Experiment YAML declares 2 inline steps at different levels
    import copy
    raw = copy.deepcopy(_MINIMAL_EXPERIMENT)
    raw["experiment"]["run"] = {
        "post": [{"type": "extract_trace_stats", "name": "yaml-run"}]
    }
    raw["experiment"]["scenario"] = {
        "post": [{"type": "service_start", "name": "yaml-scenario"}]
    }

    # CLI adds a third step (also at run level)
    merged = merge_pipeline_attachments(raw, [f"run:post:{cli_pipe}"])

    exp = MASExperimentConfig.from_data(merged, tmp_path / "exp.yaml")
    steps = exp.all_pipeline_steps()

    assert len(steps) == 3, (
        "All steps — whether from YAML or CLI — must appear in all_pipeline_steps(). "
        f"Got: {[(s.type, s.scope, s.phase) for s in steps]}"
    )
    types = {s.type for s in steps}
    assert types == {"extract_trace_stats", "service_start", "extract_mealy_stats"}

    # YAML-declared run steps come before CLI-appended ones (append semantics)
    run_steps = [s for s in steps if s.scope == "run"]
    assert len(run_steps) == 2
    assert run_steps[0].type == "extract_trace_stats"   # YAML first
    assert run_steps[1].type == "extract_mealy_stats"   # CLI appended after


def test_level_post_id_wrapper_loads_pipeline_yaml(tmp_path: Path):
    pipeline_yaml = tmp_path / "l9-live-progress.yaml"
    pipeline_yaml.write_text(
        yaml.dump({
            "pipeline": {
                "name": "l9-live-progress",
                "steps": [{"name": "progress", "type": "eval_progress", "phase": "post"}],
            }
        }),
        encoding="utf-8",
    )
    data = {
        "experiment": {
            "name": "test-exp",
            "application": {"manifest": "mas.yaml"},
            "run": {"post": [{"id": str(pipeline_yaml)}]},
        }
    }
    exp = MASExperimentConfig.from_data(data, tmp_path / "exp.yaml")
    steps = exp.all_pipeline_steps()
    assert len(steps) == 1
    assert steps[0].type == "eval_progress"
    assert steps[0].scope == "run"

