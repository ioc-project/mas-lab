#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0

from mas.ctl.workspace.config import WorkspaceConfig


def _flavour(ws: WorkspaceConfig) -> str | None:
    mas_ctl = ws.mas_ctl
    if mas_ctl.get("flavour"):
        return str(mas_ctl["flavour"])
    runtime = ws._data.get("mas_runtime") or {}
    return runtime.get("flavour") if isinstance(runtime, dict) else None


def test_workspace_walk_up_prefers_nearest_file(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    ws = repo / "config.yaml"
    ws.write_text("mas_ctl:\n  flavour: local\n", encoding="utf-8")

    deep = repo / "a" / "b"
    deep.mkdir(parents=True)

    monkeypatch.chdir(deep)
    monkeypatch.delenv("MAS_WORKSPACE_ROOT", raising=False)
    loaded = WorkspaceConfig.load()
    assert loaded.found is True
    assert _flavour(loaded) == "local"
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.chdir(other)
    assert loaded.config_path == ws.resolve()


def test_workspace_global_fallback(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    deep = repo / "x" / "y"
    deep.mkdir(parents=True)

    fake_home = tmp_path / "home"
    global_dir = fake_home / ".config" / "mas"
    global_dir.mkdir(parents=True)
    (global_dir / "config.yaml").write_text(
        "mas_ctl:\n  flavour: global-local\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_home / ".config"))
    monkeypatch.chdir(deep)
    monkeypatch.delenv("MAS_WORKSPACE_ROOT", raising=False)

    loaded = WorkspaceConfig.load()
    assert loaded.found is True
    assert _flavour(loaded) == "global-local"


def test_workspace_explicit_root_overrides_global(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "config.yaml").write_text(
        "mas_ctl:\n  flavour: mounted\n",
        encoding="utf-8",
    )

    fake_home = tmp_path / "home"
    global_dir = fake_home / ".mas"
    global_dir.mkdir(parents=True)
    (global_dir / "config.yaml").write_text(
        "mas_ctl:\n  flavour: global-local\n",
        encoding="utf-8",
    )

    other = tmp_path / "elsewhere"
    other.mkdir()

    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("MAS_WORKSPACE_ROOT", str(workspace))
    monkeypatch.chdir(other)

    loaded = WorkspaceConfig.load()
    assert loaded.found is True
    assert _flavour(loaded) == "mounted"


def test_workspace_config_plugins_default_off(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "config.yaml").write_text(
        "mas_ctl:\n  flavour: local\n# plugins:\n#   - mas.runtime.debug_script\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(repo)
    monkeypatch.delenv("MAS_WORKSPACE_ROOT", raising=False)
    loaded = WorkspaceConfig.load()
    assert loaded.plugins == []


def test_workspace_config_plugins_uncommented(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "config.yaml").write_text(
        "plugins:\n  - mas.runtime.debug_script\n  - mas.checkpoint_store.hybrid\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(repo)
    monkeypatch.delenv("MAS_WORKSPACE_ROOT", raising=False)
    loaded = WorkspaceConfig.load()
    assert loaded.plugins == ["mas.runtime.debug_script", "mas.checkpoint_store.hybrid"]


def test_workspace_explicit_root_skips_global_when_missing(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    fake_home = tmp_path / "home"
    global_dir = fake_home / ".config" / "mas"
    global_dir.mkdir(parents=True)
    (global_dir / "config.yaml").write_text(
        "mas_ctl:\n  flavour: global-local\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_home / ".config"))
    monkeypatch.setenv("MAS_WORKSPACE_ROOT", str(workspace))
    monkeypatch.chdir(workspace)

    loaded = WorkspaceConfig.load()
    assert loaded.found is False
