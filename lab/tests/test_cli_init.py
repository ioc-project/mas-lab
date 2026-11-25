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
    assert "{secret: openai}" in infra.read_text(encoding="utf-8")
    assert "https://api.openai.com/v1" in infra.read_text(encoding="utf-8")
    assert "env:" not in infra.read_text(encoding="utf-8")
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
        assert "{secret: openai}" in infra.read_text(encoding="utf-8")
        assert "https://api.openai.com/v1" in infra.read_text(encoding="utf-8")
        assert "env:" not in infra.read_text(encoding="utf-8")
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
        input="y\nmyprovider\nhttps://example.llm/v1\ntrip-model\nopenai/gpt-4o-mini\n",
    )

    assert result.exit_code == 0, result.output
    cfg = tmp_path / "mas" / "config.yaml"
    infra = tmp_path / "mas" / "infra" / "myprovider.yaml"

    assert cfg.exists()
    assert infra.exists()
    cfg_text = cfg.read_text(encoding="utf-8")
    infra_text = infra.read_text(encoding="utf-8")
    assert "myprovider" in cfg_text
    assert "https://example.llm/v1" in infra_text
    assert "{secret: openai}" in infra_text
    assert "env:" not in infra_text
    assert "trip-model: openai/gpt-4o-mini" in infra_text
    assert "mas-ctl run-mas library-samples/apps/trip-planner/mas.yaml" in result.output
    assert "mas-ctl secrets set openai" in result.output
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


def test_init_writes_the_api_base_as_given(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    result = CliRunner().invoke(app, ["init", "-q"], input="\nmyprovider\n  https://x.io/v1  \n\n\n")

    assert result.exit_code == 0, result.output
    text = (tmp_path / "mas" / "infra" / "myprovider.yaml").read_text()
    assert "https://x.io/v1" in text
    assert "  https://x.io/v1  " not in text


def test_init_yes_writes_the_default_api_base(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    result = CliRunner().invoke(app, ["init", "--yes", "-q"])

    assert result.exit_code == 0, result.output
    assert "https://api.openai.com/v1" in (tmp_path / "mas" / "infra" / "llmprovider.yaml").read_text()


OLD_INFRA = """\
apiVersion: infra/v1
kind: LLMProxy
metadata: {name: old}
spec:
  proxy:
    api_base: env:LLM_PROXY_API_BASE|https://proxy.example/v1
    api_key_env: OPENAI_API_KEY
"""


def test_init_finds_files_with_removed_forms_and_rewrites_them_on_a_yes(tmp_path: Path, capsys):
    from mas.lab.cli.commands.init import _files_with_removed_forms, _offer_migration

    infra = tmp_path / "infra" / "old.yaml"
    infra.parent.mkdir()
    infra.write_text(OLD_INFRA, encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text(f"infra_refs:\n  - {infra}\n", encoding="utf-8")

    stale = _files_with_removed_forms(config, {"infra_refs": [str(infra)]}, infra.parent, "old")
    assert stale == [infra.resolve()]

    _offer_migration(stale, yes=True)
    text = infra.read_text(encoding="utf-8")
    assert "api_key: {secret: openai}" in text and "env:" not in text and "_env" not in text
    assert (infra.parent / "old.yaml.bak").read_text(encoding="utf-8") == OLD_INFRA
    assert 'printf %s "$OPENAI_API_KEY" | mas-ctl secrets set openai' in capsys.readouterr().err
    assert _files_with_removed_forms(config, {"infra_refs": [str(infra)]}, infra.parent, "old") == []


def _stale_home(tmp_path: Path) -> tuple[Path, Path]:
    home = tmp_path / "mas"
    infra = home / "infra" / "llm-proxy.yaml"
    infra.parent.mkdir(parents=True)
    infra.write_text(OLD_INFRA, encoding="utf-8")
    (home / "config.yaml").write_text(f"infra_refs:\n  - {infra}\n", encoding="utf-8")
    return home, infra


def test_init_proposes_the_migration_and_explains_it(monkeypatch, tmp_path: Path):
    """Declined: nothing changes, and the text says what changed, why, and what to do."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    _home, infra = _stale_home(tmp_path)
    result = CliRunner().invoke(app, ["init"], input="n\n")
    assert result.exit_code == 1  # nothing else would work: the old forms are refused at load
    assert infra.read_text(encoding="utf-8") == OLD_INFRA
    for part in (
        "old env: / *_env form",
        "child processes, logs and traces",
        "`api_key_env: OPENAI_API_KEY` becomes `api_key: {secret: openai}`",
        "How keys are stored now",
        "directory 0700, files 0400",
        "Rewrite these files now",
        "mas-ctl secrets migrate --write FILE",
    ):
        assert part in result.output, part


def test_init_rewrites_on_a_yes_and_says_what_to_run_next(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    _home, infra = _stale_home(tmp_path)
    result = CliRunner().invoke(app, ["init"], input="y\nn\nn\nn\nn\n")
    assert "api_key: {secret: openai}" in infra.read_text(encoding="utf-8")
    assert 'Then store the key once:  printf %s "$OPENAI_API_KEY" | mas-ctl secrets set openai' in result.output
