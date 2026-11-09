#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0

from mas.runtime.workspace_config import RuntimeWorkspaceConfig


def test_config_path_is_fixed_at_load_time(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    config = repo / "config.yaml"
    config.write_text("paths:\n  labs_dir: data/labs\n", encoding="utf-8")
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_home / ".config"))
    monkeypatch.delenv("MAS_WORKSPACE_ROOT", raising=False)

    monkeypatch.chdir(repo)
    loaded = RuntimeWorkspaceConfig.load(start=repo)
    assert loaded.config_path == config.resolve()

    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.chdir(other)
    assert loaded.config_path == config.resolve()


def test_user_config_used_when_no_project_config(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    nowhere = tmp_path / "nowhere"
    nowhere.mkdir()
    xdg = fake_home / ".config" / "mas"
    xdg.mkdir(parents=True)
    user_cfg = xdg / "config.yaml"
    user_cfg.write_text("paths:\n  labs_dir: /tmp/xdg-labs\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_home / ".config"))
    monkeypatch.delenv("MAS_WORKSPACE_ROOT", raising=False)
    monkeypatch.chdir(nowhere)

    from mas.runtime.workspace_config import find_workspace_file

    assert find_workspace_file(nowhere) == user_cfg.resolve()
