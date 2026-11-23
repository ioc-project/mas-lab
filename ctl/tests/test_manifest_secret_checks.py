#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The manifest checks (one walker) and every loader seam that runs them: a literal secret or a credential
in a URL is refused, naming the file and the field, never the value."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml
from mas.runtime.secrets import SecretRef, SecretRefError, check_manifest, load_checked_document, prepare_manifest
from mas.runtime.secrets.manifest import ManifestProblem, iter_manifest_problems

CANARY = "sk-S3CR3T-manifest-canary"
_BAD = {"llm": {"api_key": CANARY}, "tools": [{"url": f"https://u:{CANARY}@tools.example/mcp"}]}


def _agent(spec: dict) -> dict:
    return {"apiVersion": "agent/v1", "kind": "Agent", "metadata": {"name": "a"}, "spec": spec}


def _write(path: Path, doc: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return path


def _refused(call: Callable[[], object], path: Path) -> None:
    with pytest.raises(SecretRefError) as caught:
        call()
    assert str(path) in str(caught.value)
    assert CANARY not in str(caught.value)


def test_every_rule_is_listed_with_its_path() -> None:
    doc = {
        "spec": {
            "api_key": CANARY,
            "password": "file:/run/secrets/db",
            "headers": {"Authorization": f"Bearer {CANARY}", "Accept": "json"},
            "url": f"https://u:{CANARY}@h.example/v1",
            "dsn": f"u:{CANARY}@db:5432",
            "api_key_env": "OPENAI_API_KEY",
            "base": "env:OPENAI_BASE_URL",
            "bad": {"secret": {"name": "x", "bogus": 1}},
            "admission": {"callers": {"billing": CANARY, "portal": {"secret": "p"}, "ops": "file:/tok"}},
            "listed": {"admission": {"callers": [CANARY, {"secret": "x"}]}},
        }
    }
    problems = dict(iter_manifest_problems(doc))
    assert set(problems) == {
        "spec.api_key",
        "spec.password",
        "spec.headers.Authorization",
        "spec.url",
        "spec.dsn",
        "spec.api_key_env",
        "spec.base",
        "spec.bad",
        "spec.admission.callers.billing",
        "spec.admission.callers.ops",
        "spec.listed.admission.callers[0]",
    }
    assert "file: reference" in problems["spec.password"]
    assert all(CANARY not in f"{path} {text}" for path, text in problems.items())


@pytest.mark.parametrize(
    "doc",
    [
        {"spec": {"db": {"password": 123456}}},
        {"spec": {"pipeline": [{"layer": "x", "params": {"token": CANARY}}]}},
        {"spec": {"proxy": {"Client-Secret": CANARY}}},
        {"spec": {"proxy": {"api_key": [CANARY]}}},
        {"spec": {"proxy": {"api_key": {"secret": {"name": "openai"}, "value": CANARY}}}},
        {"x": {"extra_headers": {"X-Api-Key": CANARY}}},
        {"m": {f"https://u:{CANARY}@h/": 1}},
    ],
)
def test_a_secret_shape_is_refused_without_its_value(doc: dict) -> None:
    with pytest.raises(SecretRefError) as caught:
        prepare_manifest(doc)
    assert CANARY not in str(caught.value) and "123456" not in str(caught.value)


def test_refs_and_unset_secret_fields_are_accepted() -> None:
    prepared = prepare_manifest(
        {
            "spec": {"proxy": {"apiKey": {"secret": "openai"}, "API_KEY": None, "token": {}, "password": ""}},
            "x": {"http_headers": {"Authorization": {"secret": {"name": "tok", "prefix": "Bearer "}}, "Accept": "*/*"}},
        }
    )
    assert prepared["spec"]["proxy"]["apiKey"] == SecretRef("openai")


def test_source_leads_each_path_and_str_is_the_raised_text() -> None:
    doc = {"spec": {"api_key": CANARY}}
    (problem,) = iter_manifest_problems(doc, source="infra.yaml")
    assert problem.path == "infra.yaml: spec.api_key"
    with pytest.raises(SecretRefError) as raised:
        prepare_manifest(doc, source="infra.yaml")
    assert str(raised.value) == str(problem)


def test_a_clean_document_is_hydrated_and_check_manifest_leaves_it_as_data() -> None:
    doc = _agent({"llm": {"api_key": {"secret": "openai"}, "url": "https://api.example/v1"}})
    assert list(iter_manifest_problems(doc)) == []
    assert prepare_manifest(doc)["spec"]["llm"]["api_key"] == SecretRef(name="openai")
    check_manifest(doc)
    assert doc["spec"]["llm"]["api_key"] == {"secret": "openai"}


def test_depth_and_node_caps() -> None:
    deep: list = []
    for _ in range(150):
        deep = [deep]
    wide: list = [1] * 10
    for _ in range(9):
        wide = [wide] * 10  # shared lists: 10^10 entries once expanded
    for doc in (deep, wide):
        assert list(iter_manifest_problems(doc)) == [ManifestProblem("", "manifest too large or too deeply nested")]
        with pytest.raises(SecretRefError, match="too large or too deeply nested"):
            check_manifest(doc)


def test_load_checked_document(tmp_path: Path) -> None:
    doc = {"spec": {"password": {"secret": "db"}}}
    as_json = tmp_path / "infra.json"
    as_json.write_text(json.dumps(doc), encoding="utf-8")
    (tmp_path / "empty.yaml").write_text("", encoding="utf-8")
    assert load_checked_document(_write(tmp_path / "infra.yaml", doc)) == doc
    assert load_checked_document(str(as_json)) == doc
    assert load_checked_document(doc, name="infra") == doc
    assert load_checked_document(tmp_path / "empty.yaml") == {}
    with pytest.raises(SecretRefError, match="^infra: "):
        load_checked_document({"token": CANARY}, name="infra")
    broken = tmp_path / "broken.json"
    broken.write_text('{"token": "' + CANARY, encoding="utf-8")
    with pytest.raises(ValueError) as bad:
        load_checked_document(broken)
    assert CANARY not in str(bad.value)
    (tmp_path / "list.yaml").write_text("- a\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a mapping"):
        load_checked_document(tmp_path / "list.yaml")


def _mas(tmp_path: Path, spec: dict) -> Path:
    doc = {"apiVersion": "mas/v1", "kind": "MAS", "metadata": {"name": "m"}, "spec": spec}
    return _write(tmp_path / "mas.yaml", doc)


def _seam_compile_agent(tmp_path: Path, spec: dict) -> tuple[Callable[[], object], Path]:
    from mas.ctl.compile import compile_manifest

    path = _write(tmp_path / "agent.yaml", _agent(spec))
    return lambda: compile_manifest(path, validate=False, fill_defaults=False), path


def _seam_compile_mas_agent(tmp_path: Path, spec: dict) -> tuple[Callable[[], object], Path]:
    from mas.ctl.compile import compile_manifest

    agent = _write(tmp_path / "agents" / "a.yaml", _agent(spec))
    mas = _mas(tmp_path, {"agents": [{"name": "a", "ref": "agents/a.yaml"}]})
    return lambda: compile_manifest(mas, validate=False, fill_defaults=False), agent


def _seam_compile_overlay(tmp_path: Path, spec: dict) -> tuple[Callable[[], object], Path]:
    from mas.ctl.compile import compile_manifest

    agent = _write(tmp_path / "agent.yaml", _agent({"llm": {"model": "m"}}))
    doc = {"apiVersion": "overlay/v1", "kind": "Overlay", "spec": {"target": {"kind": "Agent"}, "patch": spec}}
    overlay = _write(tmp_path / "ov.yaml", doc)
    return lambda: compile_manifest(agent, [overlay], validate=False, fill_defaults=False), overlay


def _seam_compose(tmp_path: Path, spec: dict) -> tuple[Callable[[], object], Path]:
    from mas.ctl.compose.runner import ComposeRequest, compose_run

    mas = _mas(tmp_path, {"shared": spec})
    return lambda: compose_run(ComposeRequest(manifest=mas, validate=False)), mas


def _seam_deployment(tmp_path: Path, spec: dict) -> tuple[Callable[[], object], Path]:
    from mas.ctl.deployment.load import load_deployment

    doc = {"apiVersion": "deployment/v1", "kind": "Deployment", "spec": spec}
    path = _write(tmp_path / "deployments" / "d.yaml", doc)
    return lambda: load_deployment(manifest_dir=tmp_path, deployment_path=path), path


def _seam_peer_agent(tmp_path: Path, spec: dict) -> tuple[Callable[[], object], Path]:
    from mas.ctl.manifest.mas_agent_merge import _load_agent_yaml

    path = _write(tmp_path / "agent.yaml", _agent(spec))
    return lambda: _load_agent_yaml(path), path


def _seam_pattern_lookup(tmp_path: Path, spec: dict) -> tuple[Callable[[], object], Path]:
    from mas.ctl.compose.pattern_registry import pattern_for_agent

    path = _write(tmp_path / "agents" / "a.yaml", _agent(spec))
    mas = {"spec": {"agents": [{"name": "a", "ref": "agents/a.yaml"}]}}
    return lambda: pattern_for_agent(mas, "a", mas_base_dir=tmp_path), path


def _seam_benchmark_probe(tmp_path: Path, spec: dict) -> tuple[Callable[[], object], Path]:
    from mas.ctl.benchmark.runner import _manifest_kind_on_disk

    path = _write(tmp_path / "agent.yaml", _agent(spec))
    return lambda: _manifest_kind_on_disk(path), path


def _seam_runtime_overlay(tmp_path: Path, spec: dict) -> tuple[Callable[[], object], Path]:
    from mas.ctl.runtime_cli import load_merged_agent_manifest

    agent = _write(tmp_path / "agent.yaml", _agent({"llm": {"api_key": {"secret": "openai"}}}))
    overlay = _write(tmp_path / "ov.yaml", {"kind": "Overlay", "spec": spec})
    return lambda: load_merged_agent_manifest(agent, overlays=(str(overlay),), validate=False), overlay


def _seam_bind_agent(tmp_path: Path, spec: dict) -> tuple[Callable[[], object], Path]:
    from mas.ctl.compose.models import AgentBindSlice, EffectiveBindManifest
    from mas.ctl.executor.mas_session import load_agent_manifest_from_bind

    path = _write(tmp_path / "agents" / "a.yaml", _agent(spec))
    bind = EffectiveBindManifest(
        mas_id="m",
        spec_revision="1",
        runtime_id="mas_runtime_py",
        deployment_name="local",
        agents=[AgentBindSlice(agent_id="a", manifest_path="agents/a.yaml")],
        mas_base_dir=tmp_path,
    )
    return lambda: load_agent_manifest_from_bind(bind, "a"), path


def _seam_workspace_config(tmp_path: Path, spec: dict) -> tuple[Callable[[], object], Path]:
    from mas.ctl.workspace.config import WorkspaceConfig

    (tmp_path / ".git").mkdir()
    path = _write(tmp_path / "config.yaml", {"kind": "UserConfig", "mas_ctl": spec})
    return lambda: WorkspaceConfig.load(tmp_path), path


def _seam_experiment(tmp_path: Path, spec: dict) -> tuple[Callable[[], object], Path]:
    loader = pytest.importorskip("mas.lab.manifests.loader")
    path = _write(tmp_path / "experiment.yaml", {"experiment": {"name": "e", "vars": spec}})
    return lambda: (loader.load_experiment_data(path), loader.load_pipeline_data(path)), path


def _seam_flavour(tmp_path: Path, spec: dict) -> tuple[Callable[[], object], Path]:
    load = pytest.importorskip("mas.lab.flavour.load")
    doc = {"apiVersion": "runtime/v1", "kind": "Flavour", "metadata": {"name": "f"}, "spec": {"config": spec}}
    path = _write(tmp_path / "flavour.yaml", doc)
    return lambda: load.load_flavour(path), path


_SEAMS = {name: seam for name, seam in globals().items() if name.startswith("_seam_")}


@pytest.mark.parametrize("seam", sorted(_SEAMS))
def test_loader_seam_refuses(tmp_path: Path, seam: str) -> None:
    call, path = _SEAMS[seam](tmp_path, _BAD)
    _refused(call, path)


_TUTORIAL_03 = Path(__file__).resolve().parents[2] / "docs" / "tutorials" / "03-experiments-and-analysis"


@pytest.mark.parametrize(
    ("kwargs", "named"),
    [
        ({"overrides": [f"experiment.vars.endpoint=https://u:{CANARY}@c.example/v1"]}, "experiment.vars.endpoint"),
        ({"step_overrides": [f"export_otel.api_key={CANARY}"]}, "export_otel.api_key"),
    ],
)
def test_mas_lab_run_refuses_a_secret_given_on_the_command_line(tmp_path: Path, caplog, kwargs, named) -> None:
    import logging
    import shutil

    load = pytest.importorskip("mas.lab.benchmark.schedule.run_batch.load")
    shutil.copytree(_TUTORIAL_03, tmp_path / "t03")
    with caplog.at_level(logging.ERROR):
        assert load.load_experiment(tmp_path / "t03" / "experiment.yaml", **kwargs) is None
    assert named in caplog.text
    assert CANARY not in caplog.text
