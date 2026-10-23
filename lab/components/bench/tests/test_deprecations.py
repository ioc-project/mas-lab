#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import logging
import textwrap
from pathlib import Path

import pytest
import yaml

from mas.lab.benchmark.dataset import Dataset
from mas.lab.benchmark.experiment import ExperimentConfig
from mas.lab.deprecations import clear_deprecation_warnings, docs_url, warn_deprecated
from mas.lab.inputs import load_run_input
from mas.lab.lab.config import LabConfig, MASExperimentConfig


def setup_function() -> None:
    clear_deprecation_warnings()


def test_docs_url_strips_md_and_keeps_fragment() -> None:
    assert (
        docs_url("manifests/dataset-migration.md")
        == "https://ioc-project.github.io/mas-lab/manifests/dataset-migration/"
    )
    assert (
        docs_url("manifests/experiment.md#applications")
        == "https://ioc-project.github.io/mas-lab/manifests/experiment/#applications"
    )


def test_warn_deprecated_once_per_where(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="mas.lab.deprecations"):
        warn_deprecated("dataset.legacy_item", where="a.yaml")
        warn_deprecated("dataset.legacy_item", where="a.yaml")
        warn_deprecated("dataset.legacy_item", where="b.yaml")
    assert caplog.text.count("dataset.legacy_item") == 2
    assert "dataset-migration" in caplog.text


def test_legacy_prompt_item_warns_with_docs_url(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="mas.lab.deprecations"):
        run = load_run_input({"id": "001", "prompt": "legacy prompt"})
    assert run.primary_prompt == "legacy prompt"
    assert "dataset.legacy_item" in caplog.text
    assert "https://ioc-project.github.io/mas-lab/manifests/dataset-migration/" in caplog.text
    assert "docs/manifests/dataset-migration.md" in caplog.text


def test_role_list_user_warns(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="mas.lab.deprecations"):
        run = load_run_input(
            {
                "id": "legacy",
                "inputs": {"user": [{"role": "user", "content": "Old shape"}]},
            }
        )
    assert run.primary_prompt == "Old shape"
    assert "dataset.role_list_user" in caplog.text
    assert "dataset-migration" in caplog.text


def test_tool_fixtures_survive_dataset_item_round_trip(tmp_path: Path) -> None:
    """The batch runner reloads RunInput from Dataset item dicts."""
    (tmp_path / "fixture.yaml").write_text("services: {}\n", encoding="utf-8")
    path = tmp_path / "dataset.yaml"
    yaml.dump(
        {
            "apiVersion": "lab/v1",
            "kind": "Dataset",
            "metadata": {"name": "d"},
            "spec": {
                "items": [
                    {"id": "x", "inputs": {"user": "Q", "tool_fixtures": "fixture.yaml"}}
                ]
            },
        },
        path.open("w"),
    )
    item = [i.to_dict() for i in Dataset.from_yaml(path)][0]

    run = load_run_input(item, base_path=tmp_path / "elsewhere")

    assert run.tool_fixtures == {"by_tool": {"*": {"services": {}}}}


def test_bare_list_dataset_warns(tmp_path: Path, caplog) -> None:
    path = tmp_path / "queries.yaml"
    yaml.dump([{"id": "q1", "prompt": "Q1"}], path.open("w"))
    with caplog.at_level(logging.WARNING, logger="mas.lab.deprecations"):
        dataset = Dataset.from_yaml(path)
    assert [item.prompt for item in dataset] == ["Q1"]
    assert "dataset.bare_list" in caplog.text
    assert "dataset.legacy_item" in caplog.text


def test_legacy_mas_key_warns(tmp_path: Path, caplog) -> None:
    mas_yaml = tmp_path / "mas.yaml"
    mas_yaml.write_text("apiVersion: mas/v1\nkind: MAS\nmetadata:\n  name: test\n")
    exp_yaml = tmp_path / "experiment.yaml"
    exp_yaml.write_text(
        textwrap.dedent(
            """\
            experiment:
              name: legacy-mas
              description: "Existing labs still use mas:"
              mas:
                manifest: ./mas.yaml
                configs_dir: ./overlays
            """
        )
    )
    with caplog.at_level(logging.WARNING, logger="mas.lab.deprecations"):
        cfg = MASExperimentConfig.from_yaml(exp_yaml)
    assert cfg.mas is not None
    assert "experiment.mas" in caplog.text
    assert "manifests/experiment/#applications" in caplog.text


def test_legacy_lab_config_fields_still_load(tmp_path: Path) -> None:
    path = tmp_path / "lab-config.yaml"
    path.write_text(
        textwrap.dedent(
            """\
            lab:
              name: legacy-lab
              default_flavour: local
              output_dir: ./legacy-runs
              scenarios:
                - id: baseline
                  user_prompt: Start with the legacy prompt
            """
        ),
        encoding="utf-8",
    )

    config = LabConfig.from_yaml(path)

    assert config.default_flavour == "local"
    assert config.output_dir == (tmp_path / "legacy-runs").resolve()
    assert config.scenarios[0].user_prompt == "Start with the legacy prompt"


def test_experiment_output_dir_remains_rejected(tmp_path: Path) -> None:
    path = tmp_path / "experiment.yaml"
    path.write_text(
        textwrap.dedent(
            """\
            experiment:
              name: removed-output-dir
              application:
                manifest: ./mas.yaml
              output_dir: ./legacy-runs
            """
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="removed key 'output_dir'"):
        MASExperimentConfig.from_yaml(path)


def test_experiment_dataset_path_without_name_still_loads(tmp_path: Path) -> None:
    dataset_path = tmp_path / "datasets" / "queries.yaml"
    dataset_path.parent.mkdir()
    dataset_path.write_text("kind: Dataset\n", encoding="utf-8")
    (tmp_path / "mas.yaml").write_text("kind: MAS\n", encoding="utf-8")
    path = tmp_path / "experiment.yaml"
    path.write_text(
        textwrap.dedent(
            """\
            experiment:
              name: legacy-dataset-path
              application:
                manifest: ./mas.yaml
              scenarios:
                - id: baseline
              dataset:
                path: ./datasets/queries.yaml
            """
        ),
        encoding="utf-8",
    )

    config = ExperimentConfig.from_yaml(path)

    assert config.dataset == dataset_path.resolve()


def test_former_ioc_dataset_still_loads_and_new_envelope_is_preferred(
    tmp_path: Path, caplog
) -> None:
    former = tmp_path / "former.yaml"
    former.write_text(
        textwrap.dedent(
            """\
            apiVersion: lab/v1
            kind: Dataset
            metadata:
              name: sre-triage-incidents
              version: v2
            spec:
              app: sre-triage@^v2
              items:
                - id: routing-policy-rollback
                  prompt: Triage the edge-gateway regression.
                  expectations:
                    correct_action:
                      service: edge-gateway
                      action: rollback
            """
        ),
        encoding="utf-8",
    )
    fixtures = tmp_path / "tool_fixtures"
    fixtures.mkdir()
    (fixtures / "routing-policy-rollback.yaml").write_text("scene: edge-gateway\n")
    modern = tmp_path / "modern.yaml"
    modern.write_text(
        textwrap.dedent(
            """\
            apiVersion: lab/v1
            kind: Dataset
            metadata:
              name: sre-triage-incidents
              version: v2
            spec:
              app: sre-triage@^v2
              items:
                - id: routing-policy-rollback
                  inputs:
                    user: Triage the edge-gateway regression.
                    tool_fixtures: tool_fixtures/routing-policy-rollback.yaml
                  expectations:
                    details:
                      correct_action:
                        service: edge-gateway
                        action: rollback
            """
        ),
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING, logger="mas.lab.deprecations"):
        former_ds = Dataset.from_yaml(former)
    assert former_ds[0].prompt == "Triage the edge-gateway regression."
    assert former_ds[0].run_input.expectations["details"]["correct_action"]["action"] == (
        "rollback"
    )
    assert "dataset.legacy_item" in caplog.text
    assert "dataset.legacy_expectations" in caplog.text

    caplog.clear()
    clear_deprecation_warnings()
    with caplog.at_level(logging.WARNING, logger="mas.lab.deprecations"):
        modern_ds = Dataset.from_yaml(modern)
    assert modern_ds[0].prompt == "Triage the edge-gateway regression."
    assert modern_ds[0].run_input.expectations["details"]["correct_action"]["service"] == (
        "edge-gateway"
    )
    assert "dataset.legacy_item" not in caplog.text
    assert "dataset.legacy_expectations" not in caplog.text


def test_docs_url_design_vs_schedule_fragment() -> None:
    assert (
        docs_url("manifests/experiment.md#design-vs-schedule")
        == "https://ioc-project.github.io/mas-lab/manifests/experiment/#design-vs-schedule"
    )


def test_warn_deprecated_execution_once_per_where(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="mas.lab.deprecations"):
        warn_deprecated("experiment.execution", where="a.yaml")
        warn_deprecated("experiment.execution", where="a.yaml")
        warn_deprecated("experiment.execution", where="b.yaml")
    assert caplog.text.count("experiment.execution") == 2
    assert "design-vs-schedule" in caplog.text


def _write_split_experiment(tmp_path: Path, extra: str) -> Path:
    path = tmp_path / "experiment.yaml"
    path.write_text(
        "experiment:\n"
        "  name: dual-read\n"
        "  application:\n"
        "    manifest: ./mas.yaml\n"
        "  run:\n"
        "    n_runs: 2\n"
        f"{extra}",
        encoding="utf-8",
    )
    return path


def test_legacy_execution_warns_once_and_maps(tmp_path: Path, caplog) -> None:
    path = _write_split_experiment(
        tmp_path,
        "  execution:\n"
        "    parallel_scenarios: 8\n"
        "    strategy: depth\n"
        "    emulation:\n"
        "      runtime:\n"
        "        cache: disabled\n",
    )
    with caplog.at_level(logging.WARNING, logger="mas.lab.deprecations"):
        cfg = MASExperimentConfig.from_yaml(path)
        MASExperimentConfig.from_yaml(path)
    assert caplog.text.count("experiment.execution") == 1
    assert "https://ioc-project.github.io/mas-lab/manifests/experiment/#design-vs-schedule" in caplog.text
    assert cfg.schedule.parallel_scenarios == 8
    assert cfg.schedule.ordering == "depth"
    assert cfg.bench_emulation.runtime.cache == "disabled"
    assert cfg.execution.strategy == "depth"


def test_new_shape_does_not_warn(tmp_path: Path, caplog) -> None:
    path = _write_split_experiment(
        tmp_path,
        "  schedule:\n"
        "    parallel_scenarios: 1\n"
        "    ordering: coverage\n"
        "  bench_emulation:\n"
        "    runtime:\n"
        "      cache: forced\n",
    )
    with caplog.at_level(logging.WARNING, logger="mas.lab.deprecations"):
        cfg = MASExperimentConfig.from_yaml(path)
    assert "experiment.execution" not in caplog.text
    assert cfg.schedule.parallel_scenarios == 1
    assert cfg.bench_emulation.runtime.cache == "forced"
