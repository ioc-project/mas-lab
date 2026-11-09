#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for registry-backed library discovery."""

from __future__ import annotations

from pathlib import Path

import yaml


def test_discover_registry_apps_includes_samples():
    from mas.lab.controller.lab_registry import get_lab_registry

    libs = get_lab_registry().library_paths()
    assert "trip-planner" in libs or "library-samples" in libs


def test_iter_experiment_files_supports_lab_layout(tmp_path: Path):
    from mas.lab.controller.lab_registry import LabRegistry

    reg = LabRegistry()
    lab = tmp_path / "demo.lab"
    lab.mkdir()
    (lab / "experiment.yaml").write_text(
        "experiment:\n  name: root-exp\n  description: root\n",
        encoding="utf-8",
    )
    exp_dir = lab / "experiments"
    exp_dir.mkdir()
    (exp_dir / "nested.yaml").write_text(
        "experiment:\n  name: nested\n  description: nested\n",
        encoding="utf-8",
    )

    names = {p.name for p in reg._iter_experiment_files(lab)}
    assert "experiment.yaml" in names
    assert "nested.yaml" in names


def test_collect_mas_resources_versioned_app(tmp_path: Path):
    from mas.lab.controller.lab_registry import LabRegistry

    lib = tmp_path / "example-library"
    app = lib / "apps" / "trip-planner" / "v2"
    agents = app / "agents"
    agents.mkdir(parents=True)
    (lib / "library.yaml").write_text(
        "apiVersion: mas/v1\nkind: Library\nname: mas-example-library\napps:\n  trip-planner: apps/trip-planner\n",
        encoding="utf-8",
    )
    (agents / "planner.yaml").write_text("metadata:\n  name: planner\nspec: {}\n", encoding="utf-8")
    (app / "mas.yaml").write_text(
        "apiVersion: mas/v1\nkind: MAS\nmetadata:\n  name: trip-planner\n"
        "spec:\n  agency:\n    agents:\n      - id: planner\n        ref: agents/planner.yaml\n",
        encoding="utf-8",
    )

    reg = LabRegistry()
    resources = reg._collect_mas_resources(lib)
    assert "trip-planner@v2" in resources
    assert resources["trip-planner@v2"]["path"] == "apps/trip-planner/v2/mas.yaml"
    assert "planner" in resources["trip-planner@v2"]["agents"]


def test_collect_mas_resources_from_app_root(tmp_path: Path):
    from mas.lab.controller.lab_registry import LabRegistry

    reg = LabRegistry()
    app = tmp_path / "sample-app"
    agents = app / "agents" / "worker"
    agents.mkdir(parents=True)
    (agents / "agent.yaml").write_text(
        "metadata:\n  name: worker\nspec: {}\n",
        encoding="utf-8",
    )
    mas = {
        "kind": "MAS",
        "metadata": {"name": "sample-mas"},
        "spec": {
            "agency": {
                "agents": [{"id": "worker", "ref": "agents/worker/agent.yaml"}],
            }
        },
    }
    (app / "mas.yaml").write_text(yaml.dump(mas), encoding="utf-8")

    resources = reg._collect_mas_resources(app)
    assert "sample-mas" in resources
    assert "worker" in resources["sample-mas"]["agents"]
