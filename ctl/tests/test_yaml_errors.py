#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""A YAML load error names a fixed problem and its place, never the line or a token of the document;
the manifest loader refuses the ``!!set``/``!!omap``/``!!pairs`` tags; the manifest walker refuses an alias bomb."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest
from mas.runtime.secrets.errors import SecretRefError
from mas.runtime.secrets.manifest import check_manifest
from mas.runtime.spec.batch_cache import BatchCache, bind_batch_cache, reset_batch_cache
from mas.runtime.spec.source import clear_yaml_file_cache, load_yaml_file
from mas.runtime.spec.yaml_errors import YAML_ERROR_PREFIX, YamlLoadError, safe_yaml_load, yaml_error_text

CANARY = "sk-CANARY-yaml"
DOCS = {
    "tab": (f"api_key: {CANARY}\n\tbad: x\n", "invalid YAML syntax (line 2, column 1)"),
    "undefined alias": (f"api_key: *U-{CANARY}\n", "YAML cannot be composed (line 1, column 10)"),
    "int tag": (f"api_key: !!int {CANARY}\n", "ValueError"),
    "unknown tag": (f"api_key: !{CANARY} x\n", "YAML tag not allowed (line 1, column 10)"),
    "omap": (f"spec: !!omap\n  - api_key: {CANARY}\n", "YAML tag not allowed (line 1, column 7)"),
}


@pytest.mark.parametrize("name", sorted(DOCS))
def test_safe_yaml_load_names_the_problem_never_the_text(name: str) -> None:
    text, expected = DOCS[name]
    with pytest.raises(YamlLoadError) as raised:
        safe_yaml_load(text, source="x.yaml")
    assert str(raised.value).startswith(f"x.yaml: {expected}")
    assert isinstance(raised.value, ValueError) and "CANARY" not in str(raised.value)
    assert raised.value.__cause__ is None and raised.value.__context__ is None
    assert yaml_error_text(ValueError(CANARY)) == "ValueError"


def test_anchors_load_and_the_walker_refuses_an_alias_bomb_or_cycle() -> None:
    assert safe_yaml_load("a: &a {x: 1}\nb:\n  <<: *a\n  y: 2\n") == {"a": {"x": 1}, "b": {"x": 1, "y": 2}}
    bomb = "x0: &x0 [a, a, a]\n" + "".join(f"x{i}: &x{i} [*x{i - 1}, *x{i - 1}]\n" for i in range(1, 30))
    for text in (bomb, "a: &a [*a]\n"):
        with pytest.raises(SecretRefError, match="too large or too deeply nested"):
            check_manifest(safe_yaml_load(text))


@pytest.mark.parametrize(("name", "cached"), [("tab", False), ("omap", True)])
def test_file_loaders_name_the_file_and_not_the_secret(tmp_path: Path, name: str, cached: bool) -> None:
    path = tmp_path / "infra.yaml"
    path.write_text(DOCS[name][0], encoding="utf-8")
    clear_yaml_file_cache()
    token = bind_batch_cache(BatchCache()) if cached else None
    try:
        with pytest.raises(YamlLoadError) as raised:
            load_yaml_file(path)
    finally:
        if token is not None:
            reset_batch_cache(token)
    assert str(path.resolve()) in str(raised.value) and "CANARY" not in str(raised.value)


_HEAD = "apiVersion: infra/v1\nkind: LLMProxy\nmetadata: {name: f}\nspec:\n  protocol: openai\n  proxy:\n"
COMMANDS = {
    "validate": ["validate", "infra.yaml"],
    "infra show": ["infra", "show", "--infra-ref", "infra.yaml"],
    "infra resolved": ["infra", "resolved", "infra.yaml"],
    "hardening check": ["hardening", "check", "infra.yaml"],
    "chat": ["-vvv", "chat", "agent.yaml", "--infra-ref", "infra.yaml", "-q", "hi"],
}


def _run(tmp_path: Path, code: str, args: list[str]) -> subprocess.CompletedProcess[str]:
    home = tmp_path / "home"
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MAS_", "OPENAI_"))}
    env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / "config"), XDG_DATA_HOME=str(home / "data"))
    return subprocess.run(
        [sys.executable, "-c", code, *args], capture_output=True, text=True, cwd=tmp_path / "work", env=env, timeout=120
    )


@pytest.mark.parametrize(
    ("command", "name"), zip(sorted(COMMANDS), ["tab", "undefined alias", "int tag"] * 2, strict=False)
)
def test_mas_ctl_reports_a_broken_manifest_without_the_secret(tmp_path: Path, name: str, command: str) -> None:
    work = tmp_path / "work"
    work.mkdir()
    (work / "infra.yaml").write_text(_HEAD + "    " + DOCS[name][0].replace("\n", "\n    ").rstrip(), encoding="utf-8")
    (work / "agent.yaml").write_text("apiVersion: mas/v1\nkind: Agent\nmetadata: {name: t}\nspec: {}\n", "utf-8")
    result = _run(tmp_path, "from mas.ctl.cli.main import app; app()", COMMANDS[command])
    assert result.returncode != 0
    assert "CANARY" not in result.stdout + result.stderr
    if command != "chat":  # chat at -vvv logs tracebacks on purpose
        assert "Traceback" not in result.stderr


def test_lab_sinks_report_a_broken_file_by_position(tmp_path: Path, caplog) -> None:
    pytest.importorskip("mas.lab.cli")
    from click.testing import CliRunner
    from mas.lab.benchmark.pipeline.executor import _find_lab_meta
    from mas.lab.cli.commands.validate import validate_cmd
    from mas.library.lab.steps.eval.collect_metrics import _load_dataset_lookup
    from mas.runtime.constants import LAB_CONFIG_FILENAME

    bad = f"!!int {CANARY}"
    (tmp_path / "demo.lab" / "pipelines").mkdir(parents=True)
    (tmp_path / "demo.lab" / LAB_CONFIG_FILENAME).write_text(f"lab:\n  name: {bad}\n", encoding="utf-8")
    (tmp_path / "ds.yaml").write_text(f"items:\n- id: a\n  api_key: {bad}\n", encoding="utf-8")
    (tmp_path / "experiment.yaml").write_text(f"experiment:\n  api_key: {bad}\n", encoding="utf-8")
    with caplog.at_level(logging.DEBUG):
        assert _find_lab_meta(tmp_path / "demo.lab" / "pipelines" / "p.yaml")[0] == "demo"
        assert _load_dataset_lookup(str(tmp_path / "ds.yaml"), config_path=tmp_path / "pipeline.yaml") == {}
    result = CliRunner().invoke(validate_cmd, [str(tmp_path / "experiment.yaml")])
    assert result.exit_code != 0 and YAML_ERROR_PREFIX in result.output
    assert "cannot load dataset" in caplog.text
    assert CANARY not in caplog.text + result.output
    (tmp_path / "work").mkdir()
    (tmp_path / "home" / "config" / "mas").mkdir(parents=True)
    (tmp_path / "home" / "config" / "mas" / "connections.yaml").write_text(f"connections:\n  neo4j: {bad}\n", "utf-8")
    started = _run(tmp_path, "from mas.lab.cli import app; app()", ["config"])
    assert started.returncode == 1 and "connections.yaml" in started.stderr
    assert "CANARY" not in started.stdout + started.stderr and "Traceback" not in started.stderr


def test_controller_layout_helpers_do_not_quote_a_manifest() -> None:
    layout = pytest.importorskip("mas.lab.controller.manifest_layout")
    from fastapi import HTTPException

    text = DOCS["undefined alias"][0]
    assert layout._rewrite_name(text, "n") == text
    with pytest.raises(HTTPException) as caught:
        layout._add_agent_to_mas(text, "a")
    assert caught.value.status_code == 422 and CANARY not in str(caught.value.detail)
