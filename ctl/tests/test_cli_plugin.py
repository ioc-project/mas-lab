#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for ``mas-ctl plugin`` (list/enable/doctor)."""

from __future__ import annotations

import json

import pytest
import yaml
from click.testing import CliRunner
from mas.ctl.cli.commands import plugin as plugin_cli
from mas.runtime.registry import PluginEntry, PluginRegistry, VariantInfo


def _fake_registry() -> PluginRegistry:
    reg = PluginRegistry()
    reg.register(
        PluginEntry(
            urn="mas.dp.fake_ok",
            description="Always available",
            variants={"builtin": VariantInfo(module="pathlib", class_name="Path")},
        )
    )
    reg.register(
        PluginEntry(
            urn="mas.codec.fake_missing",
            description="Needs an uninstalled package",
            variants={
                "builtin": VariantInfo(
                    module="pathlib",
                    class_name="Path",
                    requires=["no_such_package_xyz"],
                    extra="some-package[extra]",
                )
            },
        )
    )
    reg.register_runtime_spec_keys({"design_pattern", "codec"})
    return reg


@pytest.fixture
def fake_registry(monkeypatch):
    reg = _fake_registry()
    monkeypatch.setattr(plugin_cli, "_registry", lambda: reg)
    return reg


def test_list_shows_available_and_disabled(fake_registry) -> None:
    runner = CliRunner()
    result = runner.invoke(plugin_cli.plugin_group, ["list"])
    assert result.exit_code == 0, result.output
    assert "mas.dp.fake_ok" in result.output
    assert "available" in result.output
    assert "mas.codec.fake_missing" in result.output
    assert "disabled" in result.output
    assert "no_such_package_xyz" in result.output
    assert "mas-ctl plugin enable mas.codec.fake_missing" in result.output


def test_list_filters_by_type(fake_registry) -> None:
    runner = CliRunner()
    result = runner.invoke(plugin_cli.plugin_group, ["list", "--type", "codec"])
    assert result.exit_code == 0, result.output
    assert "mas.codec.fake_missing" in result.output
    assert "mas.dp.fake_ok" not in result.output


def test_list_json(fake_registry) -> None:
    runner = CliRunner()
    result = runner.invoke(plugin_cli.plugin_group, ["list", "--json"])
    assert result.exit_code == 0, result.output
    by_urn = {item["urn"]: item for item in json.loads(result.output)}
    assert by_urn["mas.dp.fake_ok"]["available"] is True
    assert by_urn["mas.codec.fake_missing"]["available"] is False
    assert by_urn["mas.codec.fake_missing"]["missing"] == ["no_such_package_xyz"]


def test_enable_reports_already_available(fake_registry) -> None:
    runner = CliRunner()
    result = runner.invoke(plugin_cli.plugin_group, ["enable", "mas.dp.fake_ok"])
    assert result.exit_code == 0, result.output
    assert "already available" in result.output


def test_enable_dry_run_prints_install_command_without_running(fake_registry, monkeypatch) -> None:
    called = []
    monkeypatch.setattr(plugin_cli.subprocess, "run", lambda *a, **k: called.append((a, k)))
    runner = CliRunner()
    result = runner.invoke(plugin_cli.plugin_group, ["enable", "mas.codec.fake_missing", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "uv pip install some-package[extra]" in result.output
    assert called == []


def test_enable_unknown_urn_fails(fake_registry) -> None:
    runner = CliRunner()
    result = runner.invoke(plugin_cli.plugin_group, ["enable", "mas.dp.does_not_exist"])
    assert result.exit_code != 0


def test_doctor_default_checks_every_registered_plugin(fake_registry) -> None:
    runner = CliRunner()
    result = runner.invoke(plugin_cli.plugin_group, ["doctor"])
    assert result.exit_code != 0
    assert "ok       mas.dp.fake_ok" in result.output
    assert "MISSING  mas.codec.fake_missing" in result.output


def test_doctor_against_manifest_checks_only_referenced_plugins(fake_registry, tmp_path) -> None:
    manifest = tmp_path / "agent.yaml"
    manifest.write_text(
        yaml.dump(
            {
                "apiVersion": "mas/v1",
                "kind": "Agent",
                "metadata": {"name": "a"},
                "spec": {"design_pattern": {"type": "mas.dp.fake_ok"}},
            }
        )
    )
    runner = CliRunner()
    result = runner.invoke(plugin_cli.plugin_group, ["doctor", str(manifest)])
    assert result.exit_code == 0, result.output
    assert "mas.dp.fake_ok" in result.output
    assert "fake_missing" not in result.output


def test_plugin_installer_env_is_the_allowlist_plus_installer_keys(monkeypatch):
    from mas.ctl.cli.commands.plugin import _index_urls
    from mas.runtime.secrets import child_env

    monkeypatch.setenv("OPENAI_API_KEY", "sk-CANARY-uv")
    monkeypatch.setenv("UV_INDEX_URL", "https://index.example/simple")
    env = child_env(_index_urls(), lab=True)
    assert "OPENAI_API_KEY" not in env
    assert env["UV_INDEX_URL"] == "https://index.example/simple"
    assert "PATH" in env
