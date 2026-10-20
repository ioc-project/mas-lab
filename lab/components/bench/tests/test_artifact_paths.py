#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Steps write inside their level folder: helper, load-time validator, executor."""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from mas.lab.benchmark.pipeline import Pipeline, PipelineStep, StepOutput
from mas.lab.benchmark.pipeline.artifact_paths import (
    StepPathError,
    resolve_step_artifact_path,
    resolve_step_input_path,
    resolve_step_named_file,
    step_config_label,
    validate_step_config_paths,
)
from mas.lab.benchmark.pipeline.executor import PipelineExecutor
from mas.lab.deprecations import clear_deprecation_warnings


def _ctx(root: Path) -> SimpleNamespace:
    return SimpleNamespace(output_dir=root)


# --------------------------------------------------------------------------
# helper
# --------------------------------------------------------------------------


def test_default_name_and_subdir(tmp_path: Path) -> None:
    out = resolve_step_artifact_path(
        _ctx(tmp_path), None, "s", "trace_stats.csv", {}, default_subdir="results"
    )
    assert out == tmp_path / "results" / "trace_stats.csv"


def test_level_folder_follows_framework_scenario_test_run(tmp_path: Path) -> None:
    cfg = {"scenario": "base", "test": "item1", "run": "r1"}
    assert resolve_step_artifact_path(_ctx(tmp_path), None, "s", "a.csv", cfg) == (
        tmp_path / "base" / "item1" / "r1" / "a.csv"
    )
    assert resolve_step_artifact_path(_ctx(tmp_path), "scenario", "s", "a.csv", cfg) == (
        tmp_path / "base" / "a.csv"
    )
    assert resolve_step_artifact_path(_ctx(tmp_path), "experiment", "s", "a.csv", cfg) == (
        tmp_path / "a.csv"
    )


def test_subdir_nested_is_allowed(tmp_path: Path) -> None:
    out = resolve_step_artifact_path(
        _ctx(tmp_path), None, "s", "a.csv", {"subdir": "results/figures"}
    )
    assert out == tmp_path / "results" / "figures" / "a.csv"


def test_name_collision_filename_is_allowed(tmp_path: Path) -> None:
    out = resolve_step_artifact_path(_ctx(tmp_path), None, "s", "a.csv", {"filename": "a_2.csv"})
    assert out == tmp_path / "a_2.csv"
    out = resolve_step_artifact_path(_ctx(tmp_path), None, "s", "a.csv", {"name": "a_3.csv"})
    assert out == tmp_path / "a_3.csv"


def test_step_config_label_covers_str_dict_and_object() -> None:
    assert step_config_label("plain") == "plain"
    assert step_config_label({"type": "writer"}) == "writer"
    assert step_config_label({"name": "w", "type": "writer"}) == "w (type writer)"
    assert step_config_label({"name": "writer", "type": "writer"}) == "writer"
    assert step_config_label(SimpleNamespace(name="w", type="writer")) == "w (type writer)"


def test_mkdir_creates_parent_only(tmp_path: Path) -> None:
    out = resolve_step_artifact_path(
        _ctx(tmp_path), None, "s", "a.csv", {}, default_subdir="results", mkdir=True
    )
    assert out == tmp_path / "results" / "a.csv"
    assert out.parent.is_dir()
    assert not out.exists()


def test_named_file_uses_filename_and_suffix(tmp_path: Path) -> None:
    out = resolve_step_named_file(
        _ctx(tmp_path),
        "s",
        {"filename": "flow"},
        "communication_flow",
        default_subdir="flows",
        suffix=".html",
    )
    assert out == tmp_path / "flows" / "flow.html"
    assert out.parent.is_dir()


def test_output_relative_path_is_allowed(tmp_path: Path) -> None:
    out = resolve_step_artifact_path(
        _ctx(tmp_path), None, "s", "a.csv", {"output": "results/ci.csv"}
    )
    assert out == tmp_path / "results" / "ci.csv"


@pytest.mark.parametrize(
    "bad",
    ["/etc/passwd", "../x.csv", "a/../../x.csv", "~/x.csv", "C:\\x.csv", "C:/x.csv", "\\\\srv\\x"],
)
def test_helper_rejects_escaping_output(tmp_path: Path, bad: str) -> None:
    with pytest.raises(StepPathError) as err:
        resolve_step_artifact_path(_ctx(tmp_path), None, "my-step", "a.csv", {"output": bad})
    msg = str(err.value)
    assert "my-step" in msg and "'output'" in msg and "Allowed:" in msg


@pytest.mark.parametrize("bad", ["a/b.csv", "../b.csv", "/b.csv", "~", ".."])
def test_helper_rejects_non_simple_filename(tmp_path: Path, bad: str) -> None:
    with pytest.raises(StepPathError):
        resolve_step_artifact_path(_ctx(tmp_path), None, "s", "a.csv", {"filename": bad})


def test_helper_rejects_escaping_subdir(tmp_path: Path) -> None:
    for bad in ("/tmp", "..", "a/../..", "~"):
        with pytest.raises(StepPathError):
            resolve_step_artifact_path(_ctx(tmp_path), None, "s", "a.csv", {"subdir": bad})


def test_helper_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "out"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, root / "link")
    with pytest.raises(StepPathError) as err:
        resolve_step_artifact_path(_ctx(root), None, "s", "a.csv", {"output": "link/a.csv"})
    assert "symlink" in str(err.value)
    # a symlink that stays inside is fine
    (root / "real").mkdir()
    os.symlink(root / "real", root / "alias")
    assert resolve_step_artifact_path(
        _ctx(root), None, "s", "a.csv", {"output": "alias/a.csv"}
    ) == root / "alias" / "a.csv"


def test_helper_rejects_escape_from_template_var(tmp_path: Path) -> None:
    cfg = {"output": "/tmp/elsewhere/a.csv"}  # what {dest}/a.csv becomes with --var dest=/tmp/elsewhere
    with pytest.raises(StepPathError):
        resolve_step_artifact_path(_ctx(tmp_path), None, "s", "a.csv", cfg)


def test_helper_rejects_traversing_scenario_names(tmp_path: Path) -> None:
    with pytest.raises(StepPathError):
        resolve_step_artifact_path(_ctx(tmp_path), None, "s", "a.csv", {"scenario": "../x"})


def test_input_path_is_relative_to_level(tmp_path: Path) -> None:
    got = resolve_step_input_path(_ctx(tmp_path), "s", {"data": "results/x.csv"}, "data")
    assert got == tmp_path / "results" / "x.csv"
    assert resolve_step_input_path(_ctx(tmp_path), "s", {}, "data") is None
    with pytest.raises(StepPathError):
        resolve_step_input_path(_ctx(tmp_path), "s", {"data": "/abs/x.csv"}, "data")


# --------------------------------------------------------------------------
# load-time validator
# --------------------------------------------------------------------------


def test_validator_rejects_with_actionable_message() -> None:
    with pytest.raises(StepPathError) as err:
        validate_step_config_paths("extract-stats (type extract_trace_stats)", {"output": "/tmp/x.csv"})
    msg = str(err.value)
    assert "extract-stats" in msg and "'output'" in msg and "absolute" in msg
    assert "results/trace_stats.csv" in msg


@pytest.mark.parametrize("key", ["output", "output_runs", "summary_out", "trace_stats", "runs_dir", "data"])
@pytest.mark.parametrize("bad", ["/x/y.csv", "../y.csv", "~/y.csv", "D:\\y.csv"])
def test_validator_rejects_path_keys(key: str, bad: str) -> None:
    with pytest.raises(StepPathError):
        validate_step_config_paths("s", {key: bad})


def test_validator_keeps_references_and_vars() -> None:
    cfg = {"data": "@compute-ci", "output": "results/{run_tag}/fig.png", "subdir": "plots/{run_tag}"}
    assert validate_step_config_paths("s", cfg) == cfg


def test_validator_rejects_framework_owned_keys() -> None:
    with pytest.raises(StepPathError):
        validate_step_config_paths("s", {"level_dir": "/x"})
    with pytest.raises(StepPathError):
        validate_step_config_paths("s", {"output_dir": "/x"})
    # injected by the framework at run time: allowed
    validate_step_config_paths("s", {"output_dir": "/x", "run_dir": "/y"}, framework_injected=True)


def test_validator_checks_nested_layers() -> None:
    with pytest.raises(StepPathError):
        validate_step_config_paths("s", {"layers": [{"data": "/etc/x.csv"}]})


def test_legacy_output_dir_template_warns_and_normalises(caplog: pytest.LogCaptureFixture) -> None:
    clear_deprecation_warnings()
    with caplog.at_level("WARNING"), pytest.warns(DeprecationWarning, match="pipeline.output_dir_template"):
        cfg = validate_step_config_paths(
            "legacy", {"output": "{output_dir}/results/trace_stats.csv", "runs_dir": "{output_dir}"}
        )
    assert cfg == {"output": "results/trace_stats.csv", "runs_dir": "."}
    assert any("output_dir_template" in r.message for r in caplog.records)


def test_legacy_output_dir_output_dir_key_is_dropped() -> None:
    clear_deprecation_warnings()
    with pytest.warns(DeprecationWarning):
        cfg = validate_step_config_paths("legacy", {"output_dir": "{output_dir}", "x": 1})
    assert cfg == {"x": 1}


def test_legacy_prefix_only_as_prefix() -> None:
    clear_deprecation_warnings()
    with pytest.raises(StepPathError):
        validate_step_config_paths("s", {"output": "x/{output_dir}/y.csv"})
    with pytest.raises(StepPathError):
        validate_step_config_paths("s", {"output": "{output_dir}x/y.csv"})


def test_legacy_prefix_followed_by_escape_is_rejected() -> None:
    clear_deprecation_warnings()
    with pytest.raises(StepPathError):
        validate_step_config_paths("s", {"output": "{output_dir}/../x.csv"})


def test_double_brace_template_is_rejected() -> None:
    with pytest.raises(StepPathError) as err:
        validate_step_config_paths("tutorial", {"output": "{{run.output_dir}}/results/trace_stats.csv"})
    assert "double-brace" in str(err.value)
    assert "never supported" in str(err.value)


# --------------------------------------------------------------------------
# load / run integration
# --------------------------------------------------------------------------


class _Writer(PipelineStep):
    type = "_confined_writer"

    async def execute(self, ctx) -> StepOutput:  # noqa: ANN001
        out = resolve_step_artifact_path(
            ctx, None, self.name, "writer.txt", self.config, default_subdir="results", mkdir=True
        )
        out.write_text(self.name, encoding="utf-8")
        return StepOutput(data={"path": str(out)}, files=[out])


@pytest.fixture()
def _registered_writer():
    from mas.lab.benchmark.pipeline import register_step_type

    register_step_type("_confined_writer", _Writer)
    yield


def _write_pipeline(tmp_path: Path, steps: list[dict]) -> Path:
    path = tmp_path / "pipeline.yaml"
    path.write_text(yaml.safe_dump({"pipeline": {"name": "confined", "steps": steps}}), encoding="utf-8")
    return path


def test_pipeline_load_rejects_escape(tmp_path: Path, _registered_writer) -> None:
    path = _write_pipeline(
        tmp_path,
        [{"name": "w", "type": "_confined_writer", "config": {"output": "../outside.txt"}}],
    )
    with pytest.raises(StepPathError) as err:
        Pipeline.from_yaml(path)
    assert "'w" in str(err.value) and "'output'" in str(err.value)


@pytest.mark.asyncio
async def test_executor_runs_tiny_pipeline_inside_output_dir(
    tmp_path: Path, _registered_writer
) -> None:
    clear_deprecation_warnings()
    path = _write_pipeline(
        tmp_path,
        [
            {"name": "default", "type": "_confined_writer", "config": {}},
            {"name": "second", "type": "_confined_writer", "config": {"filename": "writer_2.txt"}},
            {"name": "nested", "type": "_confined_writer", "config": {"subdir": "a/b"}},
            {
                "name": "legacy",
                "type": "_confined_writer",
                "config": {"output": "{output_dir}/results/legacy.txt"},
            },
            {
                "name": "templated",
                "type": "_confined_writer",
                "config": {"output": "results/{tag}.txt"},
            },
        ],
    )
    out = tmp_path / "out"
    with pytest.warns(DeprecationWarning, match="output_dir_template"):
        pipeline = Pipeline.from_yaml(path)
    result = await PipelineExecutor(pipeline, output_dir=out).run(template_vars={"tag": "tagged"})
    assert result.success, result
    assert (out / "results" / "writer.txt").read_text() == "default"
    assert (out / "results" / "writer_2.txt").read_text() == "second"
    assert (out / "a" / "b" / "writer.txt").read_text() == "nested"
    assert (out / "results" / "legacy.txt").read_text() == "legacy"
    assert (out / "results" / "tagged.txt").read_text() == "templated"
    written = {
        p.relative_to(out).as_posix() for p in out.rglob("*.txt") if ".cache" not in p.parts
    }
    assert len(written) == 5


@pytest.mark.asyncio
async def test_executor_rejects_var_that_escapes(tmp_path: Path, _registered_writer) -> None:
    path = _write_pipeline(
        tmp_path,
        [{"name": "w", "type": "_confined_writer", "config": {"output": "{dest}/x.txt"}}],
    )
    pipeline = Pipeline.from_yaml(path)
    outside = tmp_path / "elsewhere"
    result = await PipelineExecutor(pipeline, output_dir=tmp_path / "out").run(
        template_vars={"dest": str(outside)}
    )
    assert not result.success
    assert not (outside / "x.txt").exists()
    assert "absolute path" in result.step_errors["w"]


# --------------------------------------------------------------------------
# mas-lab validate
# --------------------------------------------------------------------------


def _experiment(steps: list[dict]) -> dict:
    return {"experiment": {"name": "e", "post": steps}}


def test_manifest_check_names_step_key_and_form() -> None:
    from mas.lab.benchmark.pipeline.artifact_paths import collect_manifest_path_issues

    violations, deprecations = collect_manifest_path_issues(
        _experiment(
            [
                {"name": "bad", "type": "extract_trace_stats", "config": {"output": "../x.csv"}},
                {"name": "old", "type": "extract_trace_stats", "config": {"output": "{output_dir}/a.csv"}},
                {"name": "fine", "type": "extract_trace_stats", "config": {"subdir": "results"}},
            ]
        )
    )
    assert len(violations) == 1
    assert "bad" in violations[0] and "'output'" in violations[0] and "'..'" in violations[0]
    assert len(deprecations) == 1 and "old" in deprecations[0]


def test_validate_manifest_rejects_escaping_step_output() -> None:
    from mas.lab.manifests.validator import ManifestValidationError, validate_manifest

    data = {
        "pipeline": {
            "name": "p",
            "steps": [{"name": "s", "type": "extract_trace_stats", "config": {"output": "/tmp/x.csv"}}],
        }
    }
    with pytest.raises(ManifestValidationError) as err:
        validate_manifest(data, source="p.yaml", kind="pipeline", resolve_refs=False)
    assert "absolute path" in str(err.value)


def test_artifact_path_template_is_confined() -> None:
    from mas.lab.lab.config.pipeline import ArtifactSpec

    spec = ArtifactSpec.from_entry("fig", {"type": "plot", "path": "{run_dir}/figs/a.png"})
    assert spec.relative_path() == Path("figs/a.png")
    with pytest.raises(StepPathError):
        ArtifactSpec.from_entry("fig", {"type": "plot", "path": "/tmp/a.png"})
    clear_deprecation_warnings()
    with pytest.warns(DeprecationWarning):
        spec = ArtifactSpec.from_entry("fig", {"type": "plot", "path": "{output_dir}/a.png"})
    assert spec.path == "a.png"
