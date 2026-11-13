#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner
from mas.lab.cli import app


def test_init_yes_creates_config_and_default_infra(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    runner = CliRunner()

    result = runner.invoke(app, ["init", "--yes"])

    assert result.exit_code == 0, result.output
    cfg = tmp_path / "mas" / "config.yaml"
    infra = tmp_path / "mas" / "infra" / "llmprovider.yaml"

    assert cfg.exists()
    assert infra.exists()
    assert "infra_refs" in cfg.read_text(encoding="utf-8")
    assert "OPENAI_API_KEY" in infra.read_text(encoding="utf-8")
    assert "env:OPENAI_BASE_URL|https://api.openai.com/v1" in infra.read_text(encoding="utf-8")
    assert "Next:" in result.output


def test_init_local_creates_self_contained_project_config(tmp_path: Path):
    runner = CliRunner()

    with runner.isolated_filesystem(temp_dir=tmp_path):
        result = runner.invoke(app, ["init", "--local", "--yes"])

        assert result.exit_code == 0, result.output
        cfg = Path("config.yaml")
        infra = Path("infra/llmprovider.yaml")
        cfg_text = cfg.read_text(encoding="utf-8")

        assert cfg.exists()
        assert infra.exists()
        assert "infra/llmprovider.yaml" in cfg_text
        assert "~/.config/mas/infra" not in cfg_text
        assert "OPENAI_API_KEY" in infra.read_text(encoding="utf-8")
        assert "env:OPENAI_BASE_URL|https://api.openai.com/v1" in infra.read_text(encoding="utf-8")
        assert "Initialized MAS project configuration." in result.output


def test_init_interactive_skip_infra(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    runner = CliRunner()

    result = runner.invoke(app, ["init"], input="n\n")

    assert result.exit_code == 0, result.output
    cfg = tmp_path / "mas" / "config.yaml"
    infra_dir = tmp_path / "mas" / "infra"

    assert cfg.exists()
    assert "infra_refs" not in cfg.read_text(encoding="utf-8")
    assert not infra_dir.exists()
    assert "infra   skipped" in result.output
    assert "Set your API key" not in result.output


def test_init_interactive_custom_provider(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["init"],
        input="y\nmyprovider\nhttps://example.llm/v1\nMY_API_KEY\ntrip-model\nopenai/gpt-4o-mini\n",
    )

    assert result.exit_code == 0, result.output
    cfg = tmp_path / "mas" / "config.yaml"
    infra = tmp_path / "mas" / "infra" / "myprovider.yaml"

    assert cfg.exists()
    assert infra.exists()
    cfg_text = cfg.read_text(encoding="utf-8")
    infra_text = infra.read_text(encoding="utf-8")
    assert "myprovider" in cfg_text
    assert "env:MYPROVIDER_API_BASE|https://example.llm/v1" in infra_text
    assert "MY_API_KEY" in infra_text
    assert "trip-model: openai/gpt-4o-mini" in infra_text
    assert "mas-ctl run-mas library-samples/apps/trip-planner/mas.yaml" in result.output
    assert "export MY_API_KEY=<your-key>" in result.output
    assert "trip-model -> openai/gpt-4o-mini" in result.output
    assert "--events-file" in result.output
    assert "--trace full" in result.output
    assert "mas-lab telemetry show library-samples/apps/trip-planner/traces/events.jsonl" in result.output
    assert "mas-lab plot trajectory library-samples/apps/trip-planner/traces/events.jsonl" in result.output
    assert len(result.output.splitlines()) < 40


def test_init_refuses_overwrite_with_yes(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    runner = CliRunner()

    cfg = tmp_path / "mas" / "config.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("mas_lab:\n  flavour: old\n", encoding="utf-8")

    result = runner.invoke(app, ["init", "--yes"])

    assert result.exit_code != 0
    assert "already exists" in result.output
    assert "rerun without --yes" in result.output


def test_init_interactive_decline_overwrite_keeps_existing(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    runner = CliRunner()

    cfg = tmp_path / "mas" / "config.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    original = "mas_lab:\n  flavour: old\n"
    cfg.write_text(original, encoding="utf-8")

    result = runner.invoke(app, ["init"], input="n\nn\n")

    assert result.exit_code == 0, result.output
    assert cfg.read_text(encoding="utf-8") == original


def test_init_config_lists_every_option_commented_out(monkeypatch, tmp_path: Path):
    import yaml

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    result = CliRunner().invoke(app, ["init", "--yes"])
    assert result.exit_code == 0, result.output
    text = (tmp_path / "mas" / "config.yaml").read_text(encoding="utf-8")

    # Only the minimal setup is active; the cache mode is offered, not applied.
    active = yaml.safe_load(text)
    assert active["mas_ctl"] == {"trace": "summary"}
    assert "cache" not in active["mas_ctl"]
    assert "# cache: read-only" in text
    for section in ("runtime_refs", "middleware", "manifest_libraries", "aliases", "defaults", "paths"):
        assert section not in active
        assert f"# {section}:" in text


def test_api_base_for_file_names_the_env_var():
    from mas.lab.cli.commands.init import _api_base_for_file

    assert _api_base_for_file("https://api.openai.com/v1") == "env:OPENAI_BASE_URL|https://api.openai.com/v1"
    assert _api_base_for_file("env:MY_BASE|http://localhost/v1") == "env:MY_BASE|http://localhost/v1"
    assert _api_base_for_file("") == "env:OPENAI_BASE_URL|https://api.openai.com/v1"
    assert (
        _api_base_for_file("https://example.llm/v1", infra_name="myprovider")
        == "env:MYPROVIDER_API_BASE|https://example.llm/v1"
    )


def test_render_without_infra_drops_infra_block_but_keeps_options():
    from mas.lab.cli.commands.init import _render_config_yaml

    text = _render_config_yaml(infra_name=None)
    assert "infra_refs" not in text
    assert "default_infra" not in text
    assert "# cache: read-only" in text
    assert "# paths:" in text


def test_init_quiet_prints_nothing_but_writes_files(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    result = CliRunner().invoke(app, ["init", "--yes", "-q"])

    assert result.exit_code == 0, result.output
    assert result.output == ""
    assert (tmp_path / "mas" / "config.yaml").exists()
    assert (tmp_path / "mas" / "infra" / "llmprovider.yaml").exists()


def test_api_base_env_name_is_a_shell_name_and_never_the_legacy_one():
    from mas.lab.cli.commands.init import _api_base_for_file

    assert _api_base_for_file("https://x.io/v1", infra_name="openai").startswith("env:OPENAI_PROXY_API_BASE|")
    assert _api_base_for_file("https://x.io/v1", infra_name="1abc").startswith("env:LLM_1ABC_API_BASE|")


def test_two_infra_names_with_the_same_slug_do_not_share_a_variable(tmp_path: Path):
    from mas.lab.cli.commands.init import _api_base_for_file, _taken_api_base_names

    (tmp_path / "my-provider.yaml").write_text(
        "spec:\n  proxy:\n    api_base: env:MY_PROVIDER_API_BASE|https://a/v1\n", encoding="utf-8"
    )
    taken = _taken_api_base_names(tmp_path, "my_provider")
    assert taken == {"MY_PROVIDER_API_BASE"}
    assert _api_base_for_file("https://b/v1", infra_name="my_provider", taken=taken).startswith(
        "env:MY_PROVIDER_API_BASE_2|"
    )
    # rewriting the infra file itself does not collide with its own variable
    assert _taken_api_base_names(tmp_path, "my-provider") == frozenset()
