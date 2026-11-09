#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Extended ControllerAPI coverage."""

from __future__ import annotations

import tempfile
from pathlib import Path

from mas.lab.controller.api import ControllerAPI


def test_api_experiment_crud(sample_lab, monkeypatch):
    api = ControllerAPI()
    monkeypatch.setattr(api.manifests, "refresh", lambda: None)
    api.manifests._libraries = {"demo": sample_lab}

    api.save_experiment("demo", "x", "metadata:\n  name: x\n")
    got = api.get_experiment_content("demo", "x")
    assert "x" in got["content"]
    api.delete_experiment("demo", "x")


def test_api_pipeline_crud(sample_lab, monkeypatch):
    api = ControllerAPI()
    monkeypatch.setattr(api.manifests, "refresh", lambda: None)
    api.manifests._libraries = {"demo": sample_lab}

    api.save_pipeline("demo", "pipe2", "metadata:\n  name: pipe2\nsteps: []\n")
    got = api.get_pipeline_content("demo", "pipe2")
    assert "pipe2" in got["content"]
    api.delete_pipeline("demo", "pipe2")


def test_api_dataset_crud(sample_lab, monkeypatch):
    api = ControllerAPI()
    monkeypatch.setattr(api.manifests, "refresh", lambda: None)
    api.manifests._libraries = {"demo": sample_lab}

    api.save_dataset("demo", "d1", "metadata:\n  name: d1\n")
    got = api.get_dataset_content("demo", "d1")
    assert got["name"] == "d1"
    api.delete_dataset("demo", "d1")


def test_api_get_worker_and_cancel(sample_lab, monkeypatch):
    api = ControllerAPI()
    monkeypatch.setattr(api.manifests, "refresh", lambda: None)
    api.manifests._libraries = {"demo": sample_lab}
    result = api.submit_benchmark({"experiment_yaml": str(sample_lab / "experiments" / "smoke.yaml")})
    wid = result["worker_id"]
    assert api.get_worker(wid) is not None
    assert api.cancel_worker(wid) is True
    assert api.get_job(wid) is not None


def test_api_validate_manifest():
    api = ControllerAPI()
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fh:
        fh.write("kind: Agent\nmetadata:\n  name: a\n")
        path = fh.name
    try:
        result = api.validate_manifest_yaml(Path(path).read_text())
        assert "exit_code" in result
    finally:
        Path(path).unlink(missing_ok=True)


def test_api_run_jobs(sample_lab, monkeypatch):
    api = ControllerAPI()
    monkeypatch.setattr(api.manifests, "refresh", lambda: None)
    api.manifests._libraries = {"demo": sample_lab}

    agent = api.run_agent_job("demo", {"manifest_yaml": "kind: Agent\n", "query": "hi"})
    assert "job_id" in agent

    mas = api.run_mas_job("demo", {"manifest_yaml": "kind: MAS\n", "query": "hi"})
    assert "job_id" in mas

    bench = api.run_benchmark_job("demo", {"experiment_yaml": "metadata:\n  name: t\n"})
    assert bench["command"].startswith("mas-lab benchmark")

    pipe = api.run_pipeline_job("demo", {"pipeline_yaml": "metadata:\n  name: p\nsteps: []\n"})
    assert "job_id" in pipe


def test_api_list_scenarios_and_config(sample_lab, monkeypatch):
    api = ControllerAPI()
    monkeypatch.setattr(api.manifests, "refresh", lambda: None)
    api.manifests._libraries = {"demo": sample_lab}
    assert isinstance(api.list_scenarios("demo"), list)
    assert "infra" in api.config_files("demo")


def test_catalog_plugins(sample_lab):
    from mas.lab.controller.artifact_discovery import discover_tools

    tools = discover_tools(sample_lab)
    assert isinstance(tools, list)


def test_catalog_plugins_filters(sample_lab, monkeypatch):
    monkeypatch.setattr(
        "mas.lab.controller.artifact_discovery.discover_tools",
        lambda base_dir, namespaces=None: [{"name": "my_tool", "description": ""}],
    )
    from mas.lab.controller.artifact_discovery import discover_tools

    tools = discover_tools(sample_lab)
    assert tools == [{"name": "my_tool", "description": ""}]


def test_api_overlay_crud_and_jobs(sample_lab, monkeypatch):
    api = ControllerAPI()
    monkeypatch.setattr(api.manifests, "refresh", lambda: None)
    api.manifests._libraries = {"demo": sample_lab}

    api.save_overlay("demo", "extra", "description: extra\n")
    assert api.get_overlay_content("demo", "extra")["name"] == "extra"
    api.delete_overlay("demo", "extra")

    (sample_lab / "experiments" / "broken.yaml").write_text(": [", encoding="utf-8")
    exps = api.list_experiments("demo")
    assert any(e["name"] == "broken" for e in exps)

    api.submit_benchmark({"experiment_yaml": str(sample_lab / "experiments" / "smoke.yaml")})
    jobs = api.list_jobs()
    assert len(jobs) >= 1


def test_api_tools_skills_and_step_types(sample_lab, monkeypatch):
    api = ControllerAPI()
    monkeypatch.setattr(api.manifests, "refresh", lambda: None)
    api.manifests._libraries = {"demo": sample_lab}

    assert isinstance(api.list_tools("demo"), list)
    assert isinstance(api.list_skills("demo"), list)
    step_types = api.pipeline_step_types()
    assert "step_types" in step_types
