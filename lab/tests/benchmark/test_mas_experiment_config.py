#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for MASExperimentConfig.from_yaml — dataset parsing and lab smoke checks.

Covers:
  - dataset: path: <file>        (classic form, must keep working)
  - dataset: name: <name>        (shorthand, was broken — KeyError: 'path')
  - dataset: name: + mode:       (extensions.lab dataset shorthand)
  - dataset: absent              (optional dataset, valid)
  - Smoke-parse of every real experiment.yaml in labs/
  - Dry-run of every reproduce command (validates config + pipeline end-to-end)
"""
import json
import subprocess
import sys
import textwrap
import warnings
from pathlib import Path

import pytest
import yaml
from mas.lab.benchmark.schedule.run_batch.load import (
    _select_dataset_items,
    _select_scenario_ids,
)
from mas.lab.deprecations import clear_deprecation_warnings
from mas.lab.lab.config import MASExperimentConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_LABS_DIR = Path(__file__).parents[3] / "labs"
"""Root of the labs/ tree, relative to the repo root."""


def _write_experiment(tmp_path: Path, dataset_block: str, extra: str = "") -> Path:
    """Write a minimal well-formed experiment.yaml with the given dataset block."""
    # Create a placeholder mas.yaml so path resolution does not raise
    mas_yaml = tmp_path / "mas.yaml"
    mas_yaml.write_text("apiVersion: mas/v1\nkind: MAS\nmetadata:\n  name: test-mas\n")

    # Build dataset section — indent every line by 2 spaces to nest under experiment:
    dataset_indented = "\n".join("  " + line for line in dataset_block.splitlines())
    extra_section = ("\n" + "\n".join("  " + line for line in extra.splitlines())) if extra else ""

    exp = (
        "experiment:\n"
        "  name: test-experiment\n"
        '  description: "Unit test"\n'
        "\n"
        "  application:\n"
        "    manifest: ./mas.yaml\n"
        "    configs_dir: ./overlays\n"
        "\n"
        f"{dataset_indented}\n"
        f"{extra_section}\n"
    )
    exp_yaml = tmp_path / "experiment.yaml"
    exp_yaml.write_text(exp)
    return exp_yaml


def _make_dataset_file(base: Path, filename: str) -> None:
    """Create a minimal dataset YAML at base/datasets/<filename>."""
    datasets_dir = base / "datasets"
    datasets_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "apiVersion": "lab/v1",
        "kind": "Dataset",
        "metadata": {"name": Path(filename).stem, "version": "1.0"},
        "spec": {"items": [{"id": "q1", "prompt": "Test query"}]},
    }
    (datasets_dir / filename).write_text(
        yaml.dump(manifest, allow_unicode=True, sort_keys=False)
    )


# ---------------------------------------------------------------------------
# dataset: path: (classic form — must keep working)
# ---------------------------------------------------------------------------

def test_dataset_path_form(tmp_path):
    """dataset: path: <relative> resolves correctly."""
    _make_dataset_file(tmp_path, "my-queries.yaml")
    exp_yaml = _write_experiment(
        tmp_path,
        "dataset:\n  path: ./datasets/my-queries.yaml",
    )
    cfg = MASExperimentConfig.from_yaml(exp_yaml)
    assert cfg.dataset == (tmp_path / "datasets" / "my-queries.yaml").resolve()


# ---------------------------------------------------------------------------
# dataset: name: (shorthand — was the bug)
# ---------------------------------------------------------------------------

def test_dataset_name_only_resolves_to_datasets_folder(tmp_path):
    """dataset: name: <foo> → ./datasets/foo.yaml relative to experiment dir."""
    _make_dataset_file(tmp_path, "extensions-queries.yaml")
    exp_yaml = _write_experiment(
        tmp_path,
        "dataset:\n  name: extensions-queries",
    )
    cfg = MASExperimentConfig.from_yaml(exp_yaml)
    assert cfg.dataset == (tmp_path / "datasets" / "extensions-queries.yaml").resolve()


def test_dataset_name_with_extra_fields(tmp_path):
    """dataset: name: + mode: (real extensions.lab shape) no longer raises KeyError."""
    _make_dataset_file(tmp_path, "extensions-queries.yaml")
    exp_yaml = _write_experiment(
        tmp_path,
        "dataset:\n  name: extensions-queries\n  mode: sequential",
    )
    # This was crashing with KeyError: 'path' before the fix
    cfg = MASExperimentConfig.from_yaml(exp_yaml)
    assert cfg.dataset is not None
    assert cfg.dataset.name == "extensions-queries.yaml"


def test_dataset_name_with_limit(tmp_path):
    """dataset: name: + limit: are both parsed without error."""
    _make_dataset_file(tmp_path, "queries.yaml")
    exp_yaml = _write_experiment(
        tmp_path,
        "dataset:\n  name: queries\n  limit: 5",
    )
    cfg = MASExperimentConfig.from_yaml(exp_yaml)
    assert cfg.dataset_limit == 5


def test_dataset_name_with_group_filter(tmp_path):
    """dataset: name: + group: shorthand populates dataset_filter correctly."""
    _make_dataset_file(tmp_path, "queries.yaml")
    exp_yaml = _write_experiment(
        tmp_path,
        "dataset:\n  name: queries\n  group: single_agent",
    )
    cfg = MASExperimentConfig.from_yaml(exp_yaml)
    assert cfg.dataset_filter == {"group": "single_agent"}


def test_dataset_source_overlay_is_loaded_and_applied(tmp_path):
    """experiment.dataset.source is parsed and merged onto Dataset spec.source."""
    datasets_dir = tmp_path / "datasets"
    datasets_dir.mkdir(parents=True, exist_ok=True)
    (datasets_dir / "rows.jsonl").write_text(
        json.dumps({"id": "a", "question": "A"})
        + "\n"
        + json.dumps({"id": "b", "question": "B"})
        + "\n"
        + json.dumps({"id": "c", "question": "C"})
        + "\n",
        encoding="utf-8",
    )
    manifest = {
        "apiVersion": "lab/v1",
        "kind": "Dataset",
        "metadata": {"name": "mmlu", "version": "v1"},
        "spec": {
            "source": {
                "kind": "jsonl",
                "path": "rows.jsonl",
                "map": {"id": "id", "inputs.user": "question"},
            }
        },
    }
    (datasets_dir / "mmlu.yaml").write_text(
        yaml.dump(manifest, allow_unicode=True, sort_keys=False)
    )
    exp_yaml = _write_experiment(
        tmp_path,
        "dataset:\n  path: ./datasets/mmlu.yaml\n  source:\n    limit: 1",
    )
    cfg = MASExperimentConfig.from_yaml(exp_yaml)
    assert cfg.dataset_source == {"limit": 1}

    from mas.lab.benchmark.schedule.run_batch.load import _load_dataset_items

    items = _load_dataset_items(cfg)
    assert [item["id"] for item in items] == ["a"]
    assert items[0]["inputs"]["user"] == "A"


def test_dataset_limit_slices_yaml_items(tmp_path):
    """experiment.dataset.limit keeps the first N envelope items."""
    datasets_dir = tmp_path / "datasets"
    datasets_dir.mkdir(parents=True, exist_ok=True)
    (datasets_dir / "queries.yaml").write_text(
        yaml.dump(
            {
                "apiVersion": "lab/v1",
                "kind": "Dataset",
                "metadata": {"name": "queries", "version": "v1"},
                "spec": {
                    "items": [
                        {"id": "1", "inputs": {"user": "one"}},
                        {"id": "2", "inputs": {"user": "two"}},
                        {"id": "3", "inputs": {"user": "three"}},
                    ]
                },
            },
            allow_unicode=True,
            sort_keys=False,
        )
    )
    exp_yaml = _write_experiment(
        tmp_path,
        "dataset:\n  name: queries\n  limit: 2",
    )
    cfg = MASExperimentConfig.from_yaml(exp_yaml)
    assert cfg.dataset_limit == 2
    from mas.lab.benchmark.schedule.run_batch.load import (
        _load_dataset_items,
        _view_dataset_items,
    )

    items = _view_dataset_items(
        _load_dataset_items(cfg),
        dataset_limit=cfg.dataset_limit,
    )
    assert [item["id"] for item in items] == ["1", "2"]


def test_selectors_narrow_benchmark_to_one_scenario_and_item():
    items = [{"id": "one"}, {"id": "two"}]

    assert _select_scenario_ids(["baseline", "react"], "react") == ["react"]
    assert _select_dataset_items(items, "two") == [{"id": "two"}]


def test_selectors_reject_unknown_ids():
    with pytest.raises(ValueError, match="Scenario ID not found: missing"):
        _select_scenario_ids(["baseline"], "missing")
    with pytest.raises(ValueError, match="Dataset item ID not found: missing"):
        _select_dataset_items([{"id": "one"}], "missing")


def test_load_dataset_items_raises_on_bad_source(tmp_path):
    """A present dataset file that cannot materialize must not fall back to a dummy prompt."""
    datasets_dir = tmp_path / "datasets"
    datasets_dir.mkdir(parents=True, exist_ok=True)
    (datasets_dir / "mmlu.yaml").write_text(
        yaml.dump(
            {
                "apiVersion": "lab/v1",
                "kind": "Dataset",
                "metadata": {"name": "mmlu", "version": "v1"},
                "spec": {"source": {"kind": "jsonl"}},
            },
            allow_unicode=True,
            sort_keys=False,
        )
    )
    exp_yaml = _write_experiment(
        tmp_path,
        "dataset:\n  path: ./datasets/mmlu.yaml",
    )
    cfg = MASExperimentConfig.from_yaml(exp_yaml)
    from mas.lab.benchmark.schedule.run_batch.load import _load_dataset_items

    with pytest.raises(ValueError, match="path is required"):
        _load_dataset_items(cfg)


def test_plots_key_rejected(tmp_path):
    """Top-level plots: is deprecated — use pipeline post steps instead."""
    exp_yaml = _write_experiment(
        tmp_path,
        "",
        extra="plots:\n  latency:\n    type: latency_by_scenario",
    )
    with pytest.raises(ValueError, match="plots"):
        MASExperimentConfig.from_yaml(exp_yaml)


def test_pipeline_bind_key_rejected(tmp_path):
    """pipeline_bind is removed; hooks live under run/item/scenario/post."""
    exp_yaml = _write_experiment(
        tmp_path,
        "",
        extra="pipeline_bind: run",
    )
    with pytest.raises(ValueError, match="pipeline_bind"):
        MASExperimentConfig.from_yaml(exp_yaml)


# ---------------------------------------------------------------------------
# No dataset (optional)
# ---------------------------------------------------------------------------

def test_legacy_mas_key_still_loads(tmp_path):
    """Pre-applications mas: {manifest, configs_dir} must still parse."""
    mas_yaml = tmp_path / "mas.yaml"
    mas_yaml.write_text("apiVersion: mas/v1\nkind: MAS\nmetadata:\n  name: test\n")
    exp_yaml = tmp_path / "experiment.yaml"
    exp_yaml.write_text(textwrap.dedent("""\
        experiment:
          name: legacy-mas
          description: "Existing labs still use mas:"
          mas:
            manifest: ./mas.yaml
            configs_dir: ./overlays
    """))
    cfg = MASExperimentConfig.from_yaml(exp_yaml)
    assert cfg.mas is not None
    assert cfg.mas.manifest == mas_yaml.resolve()


def test_no_dataset_is_valid(tmp_path):
    """Omitting dataset entirely is a valid experiment (scenarios-driven)."""
    mas_yaml = tmp_path / "mas.yaml"
    mas_yaml.write_text("apiVersion: mas/v1\nkind: MAS\nmetadata:\n  name: test\n")
    exp_yaml = tmp_path / "experiment.yaml"
    exp_yaml.write_text(textwrap.dedent("""\
        experiment:
          name: no-dataset
          description: "No dataset — scenarios only"
          application:
            manifest: ./mas.yaml
            configs_dir: ./overlays
    """))
    cfg = MASExperimentConfig.from_yaml(exp_yaml)
    assert cfg.dataset is None


def test_canonicalize_deprecated_aliases(tmp_path):
    """applications:/application.post/test: still load, mapped to CLI names."""
    mas_yaml = tmp_path / "mas.yaml"
    mas_yaml.write_text("apiVersion: mas/v1\nkind: MAS\nmetadata:\n  name: test\n")
    exp_yaml = tmp_path / "experiment.yaml"
    exp_yaml.write_text(textwrap.dedent("""\
        experiment:
          name: deprecated-aliases
          applications:
            - manifest: ./mas.yaml
              configs_dir: ./overlays
          dataset:
            path: ./datasets/my-queries.yaml
          test:
            artifacts:
              df: dataframe
          application:
            post:
              - name: gather-experiment
                type: gather_level
    """))
    _make_dataset_file(tmp_path, "my-queries.yaml")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        cfg = MASExperimentConfig.from_yaml(exp_yaml)
    messages = " ".join(str(w.message) for w in caught)
    assert "applications:" in messages or "deprecated" in messages.lower()
    assert "item" in cfg.levels
    assert "experiment" in cfg.levels
    assert cfg.mas is not None


def test_canonical_application_and_item_keys(tmp_path):
    """Preferred vocabulary: application: + item: + post:."""
    mas_yaml = tmp_path / "mas.yaml"
    mas_yaml.write_text("apiVersion: mas/v1\nkind: MAS\nmetadata:\n  name: test\n")
    exp_yaml = tmp_path / "experiment.yaml"
    exp_yaml.write_text(textwrap.dedent("""\
        experiment:
          name: canonical
          application:
            manifest: ./mas.yaml
            configs_dir: ./overlays
          item:
            artifacts:
              df: dataframe
          post:
            - name: gather-experiment
              type: gather_level
    """))
    cfg = MASExperimentConfig.from_yaml(exp_yaml)
    assert cfg.mas is not None
    assert "item" in cfg.levels
    assert "experiment" in cfg.levels
    assert cfg.output_schema == {}


def test_output_schema_loaded(tmp_path):
    mas_yaml = tmp_path / "mas.yaml"
    mas_yaml.write_text("apiVersion: mas/v1\nkind: MAS\nmetadata:\n  name: test\n")
    exp_yaml = tmp_path / "experiment.yaml"
    exp_yaml.write_text(textwrap.dedent("""\
        experiment:
          name: with-schema
          application:
            manifest: ./mas.yaml
          output_schema:
            required_files:
              - results/ci_summary.csv
            required_columns:
              results/ci_summary.csv: [scenario, mean]
    """))
    cfg = MASExperimentConfig.from_yaml(exp_yaml)
    assert cfg.output_schema["required_files"] == ["results/ci_summary.csv"]
    assert cfg.output_schema["required_columns"]["results/ci_summary.csv"] == ["scenario", "mean"]


# ---------------------------------------------------------------------------
# Smoke-parse: every real experiment.yaml in labs/
# ---------------------------------------------------------------------------

def _collect_lab_experiments() -> list[tuple[str, Path]]:
    """Return (label, path) pairs for all experiment.yaml files under labs/."""
    if not _LABS_DIR.exists():
        return []
    return [
        (str(p.relative_to(_LABS_DIR)), p)
        for p in sorted(_LABS_DIR.rglob("experiment*.yaml"))
    ]


@pytest.mark.parametrize("label,exp_yaml", _collect_lab_experiments(), ids=[p[0] for p in _collect_lab_experiments()])
def test_lab_experiment_yaml_parses(label, exp_yaml):
    """Every experiment.yaml in labs/ must parse without errors.

    This catches schema regressions (missing fields, unexpected keys,
    KeyErrors like the 'path' bug) before contributors discover them at
    run time.  The test does NOT execute the experiment — it only validates
    that MASExperimentConfig.from_yaml() succeeds.

    Envelope deprecations (experiment.execution, experiment.mas, …) fail;
    those shipped files must already be on design/schedule/bench_emulation.
    """
    clear_deprecation_warnings()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)
        cfg = MASExperimentConfig.from_yaml(exp_yaml)
    leftover = [
        str(w.message)
        for w in caught
        if issubclass(w.category, DeprecationWarning) and str(w.message).startswith("Deprecated:")
    ]
    assert leftover == [], f"{label}: shipped experiment still uses a deprecated shape: {leftover}"

    assert cfg.name, f"{label}: experiment name must be non-empty"
    # If a dataset block is present the path must have been resolved
    if cfg.dataset is not None:
        assert isinstance(cfg.dataset, Path), f"{label}: dataset must resolve to a Path"


# ---------------------------------------------------------------------------
# Dry-run: every reproduce command must pass `mas-lab benchmark run --dry-run`
#
# This is the strongest validation short of actually running the experiments:
# it exercises the full config-loading, scenario discovery, dataset resolution,
# and pipeline planning code path.  Safe to run without any API keys.
# ---------------------------------------------------------------------------

#: Experiments listed in `task reproduce` — the exact commands an experimenter runs.
_REPRODUCE_EXPERIMENTS = [
    "labs/design-space.lab/01-design-patterns/experiment.yaml",
    "labs/design-space.lab/02-topologies/experiment.yaml",
    "labs/lifecycle-control.lab/experiment.yaml",
    "labs/extensions.lab/experiment.yaml",
]

_REPO_ROOT = Path(__file__).parents[3]
"""Absolute path to the repository root (outshift-open/mas-lab)."""

_MAS_LAB = Path(sys.executable).parent / "mas-lab"
"""The ``mas-lab`` console script installed in *this* venv.

Resolved relative to ``sys.executable`` rather than left to ``PATH`` lookup,
so the test can't pick up an unrelated ``mas-lab`` shadowing this repo's own
venv earlier on ``PATH`` (see the same pattern in ``tests/test_reproduction.py``).
"""


@pytest.mark.parametrize("rel_path", _REPRODUCE_EXPERIMENTS)
def test_reproduce_command_dry_run(rel_path: str):
    """``mas-lab benchmark run <experiment> --dry-run`` must exit 0 and report valid config.

    Runs the actual CLI in a subprocess so the test exercises the same code
    path as an experimenter, including config loading, path resolution,
    dataset discovery, and pipeline planning.  No LLM calls are made.
    """
    exp_yaml = _REPO_ROOT / rel_path
    assert exp_yaml.exists(), f"Experiment YAML not found: {exp_yaml}"
    if not _MAS_LAB.is_file():
        pytest.skip("mas-lab CLI not in venv")

    result = subprocess.run(
        [str(_MAS_LAB), "benchmark", "run", str(exp_yaml), "--dry-run"],
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
    )

    output = result.stdout + result.stderr
    assert result.returncode == 0, (
        f"`mas-lab benchmark run {rel_path} --dry-run` exited with code "
        f"{result.returncode}.\n\nOutput:\n{output}"
    )
    assert "Configuration valid" in output, (
        f"`--dry-run` did not print 'Configuration valid' for {rel_path}.\n\nOutput:\n{output}"
    )
