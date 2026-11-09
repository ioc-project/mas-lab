#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for apply_experiment_overlays — experiment YAML deep-merge."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from mas.lab.benchmark.execution.experiment_overlay import apply_experiment_overlays

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_overlay(tmp_path: Path, name: str, spec: dict) -> Path:
    p = tmp_path / name
    p.write_text(
        yaml.dump({"apiVersion": "mas/v1", "kind": "ExperimentOverlay", "spec": spec}),
        encoding="utf-8",
    )
    return p


def _base() -> dict:
    return {
        "experiment": {
            "name": "my-exp",
            "execution": {
                "parallel_scenarios": 2,
                "emulation": {"runtime": {"cache": "disabled"}},
            },
            "run": {"n_runs": 3, "post": [{"ref": "pipelines/a.yaml"}]},
            "post": [{"ref": "pipelines/b.yaml"}],
            "scenarios": [{"id": "s1"}],
        }
    }


# ---------------------------------------------------------------------------
# Basic merge
# ---------------------------------------------------------------------------


def test_no_overlays_returns_unchanged(tmp_path: Path) -> None:
    base = _base()
    result = apply_experiment_overlays(base, [], base_dir=tmp_path)
    assert result == base


def test_scalar_override(tmp_path: Path) -> None:
    overlay = _write_overlay(
        tmp_path, "o.yaml", {"execution": {"emulation": {"runtime": {"cache": "content-addressed"}}}}
    )
    result = apply_experiment_overlays(_base(), [overlay])
    assert result["experiment"]["execution"]["emulation"]["runtime"]["cache"] == "content-addressed"


def test_scalar_override_does_not_affect_unrelated_keys(tmp_path: Path) -> None:
    spec = {"execution": {"emulation": {"runtime": {"cache": "content-addressed"}}}}
    overlay = _write_overlay(tmp_path, "o.yaml", spec)
    result = apply_experiment_overlays(_base(), [overlay])
    assert result["experiment"]["execution"]["parallel_scenarios"] == 2


def test_n_runs_override(tmp_path: Path) -> None:
    overlay = _write_overlay(tmp_path, "o.yaml", {"run": {"n_runs": 1}})
    result = apply_experiment_overlays(_base(), [overlay])
    assert result["experiment"]["run"]["n_runs"] == 1


# ---------------------------------------------------------------------------
# List replace semantics (RFC 7386)
# ---------------------------------------------------------------------------


def test_run_post_replaced_by_overlay(tmp_path: Path) -> None:
    """Lists replace rather than append — provide the full desired list."""
    overlay = _write_overlay(tmp_path, "o.yaml", {"run": {"post": [{"ref": "pipelines/eval.yaml"}]}})
    result = apply_experiment_overlays(_base(), [overlay])
    post = result["experiment"]["run"]["post"]
    # overlay completely replaces the base list
    assert post == [{"ref": "pipelines/eval.yaml"}]


def test_experiment_post_replaced_by_overlay(tmp_path: Path) -> None:
    overlay = _write_overlay(tmp_path, "o.yaml", {"post": [{"ref": "pipelines/plots.yaml"}]})
    result = apply_experiment_overlays(_base(), [overlay])
    post = result["experiment"]["post"]
    assert post == [{"ref": "pipelines/plots.yaml"}]


# ---------------------------------------------------------------------------
# Multiple overlays applied in order
# ---------------------------------------------------------------------------


def test_multiple_overlays_applied_left_to_right(tmp_path: Path) -> None:
    o1 = _write_overlay(tmp_path, "o1.yaml", {"execution": {"parallel_scenarios": 8}})
    o2 = _write_overlay(tmp_path, "o2.yaml", {"execution": {"parallel_scenarios": 1}})
    result = apply_experiment_overlays(_base(), [o1, o2])
    # o2 wins (applied last)
    assert result["experiment"]["execution"]["parallel_scenarios"] == 1


def test_multiple_overlays_pipelines_last_wins(tmp_path: Path) -> None:
    """Multiple overlays on the same list: last overlay wins (RFC 7386 replace)."""
    o1 = _write_overlay(tmp_path, "o1.yaml", {"run": {"post": [{"ref": "pipelines/x.yaml"}]}})
    o2 = _write_overlay(tmp_path, "o2.yaml", {"run": {"post": [{"ref": "pipelines/y.yaml"}]}})
    result = apply_experiment_overlays(_base(), [o1, o2])
    refs = [e["ref"] for e in result["experiment"]["run"]["post"]]
    assert refs == ["pipelines/y.yaml"]  # o2 wins


# ---------------------------------------------------------------------------
# Overlay format variants
# ---------------------------------------------------------------------------


def test_bare_overlay_without_spec_key(tmp_path: Path) -> None:
    p = tmp_path / "bare.yaml"
    p.write_text(yaml.dump({"run": {"n_runs": 7}}), encoding="utf-8")
    result = apply_experiment_overlays(_base(), [p])
    assert result["experiment"]["run"]["n_runs"] == 7


def test_overlay_with_experiment_key(tmp_path: Path) -> None:
    p = tmp_path / "exp.yaml"
    p.write_text(yaml.dump({"experiment": {"run": {"n_runs": 5}}}), encoding="utf-8")
    result = apply_experiment_overlays(_base(), [p])
    assert result["experiment"]["run"]["n_runs"] == 5


def test_relative_path_resolved_from_base_dir(tmp_path: Path) -> None:
    _write_overlay(tmp_path, "rel.yaml", {"run": {"n_runs": 2}})
    result = apply_experiment_overlays(_base(), [Path("rel.yaml")], base_dir=tmp_path)
    assert result["experiment"]["run"]["n_runs"] == 2


def test_missing_overlay_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        apply_experiment_overlays(_base(), [tmp_path / "missing.yaml"])


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_overlay_spec_is_noop(tmp_path: Path) -> None:
    overlay = _write_overlay(tmp_path, "empty.yaml", {})
    base = _base()
    result = apply_experiment_overlays(base, [overlay])
    assert result["experiment"] == base["experiment"]


def test_overlay_adds_new_level_key(tmp_path: Path) -> None:
    overlay = _write_overlay(tmp_path, "o.yaml", {"scenario": {"post": [{"ref": "s.yaml"}]}})
    result = apply_experiment_overlays(_base(), [overlay])
    assert result["experiment"]["scenario"]["post"] == [{"ref": "s.yaml"}]


def test_original_dict_not_mutated(tmp_path: Path) -> None:
    overlay = _write_overlay(tmp_path, "o.yaml", {"execution": {"emulation": {"runtime": {"cache": "forced"}}}})
    base = _base()
    import copy

    base_copy = copy.deepcopy(base)
    apply_experiment_overlays(base, [overlay])
    assert base == base_copy  # original unchanged


def test_bare_experiment_dict_without_experiment_wrapper(tmp_path: Path) -> None:
    # Some callers pass the inner dict directly without the outer "experiment:" key
    overlay = _write_overlay(tmp_path, "o.yaml", {"run": {"n_runs": 4}})
    bare = {"name": "x", "run": {"n_runs": 3}}
    result = apply_experiment_overlays(bare, [overlay])
    assert result["run"]["n_runs"] == 4


def test_schedule_overlay_merge(tmp_path: Path) -> None:
    overlay = _write_overlay(
        tmp_path,
        "o.yaml",
        {
            "bench_emulation": {"runtime": {"cache": "content-addressed"}},
            "schedule": {"parallel_scenarios": 8},
        },
    )
    base = {
        "experiment": {
            "name": "my-exp",
            "schedule": {"parallel_scenarios": 2, "ordering": "coverage"},
            "bench_emulation": {"runtime": {"cache": "disabled"}},
            "run": {"n_runs": 3},
        }
    }
    result = apply_experiment_overlays(base, [overlay])
    assert result["experiment"]["bench_emulation"]["runtime"]["cache"] == "content-addressed"
    assert result["experiment"]["schedule"]["parallel_scenarios"] == 8
    assert result["experiment"]["schedule"]["ordering"] == "coverage"
