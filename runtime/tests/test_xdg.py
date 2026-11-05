#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0

from mas.runtime.xdg import (
    mas_cache_root,
    mas_data_root,
    mas_state_root,
    mas_user_config_file,
    xdg_cache_home,
    xdg_config_home,
    xdg_data_home,
    xdg_state_home,
)


def test_xdg_respects_env_overrides(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_home / "cfg"))
    monkeypatch.setenv("XDG_DATA_HOME", str(fake_home / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(fake_home / "cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(fake_home / "state"))

    assert xdg_config_home() == fake_home / "cfg"
    assert mas_user_config_file() == fake_home / "cfg" / "mas" / "config.yaml"
    assert mas_data_root() == fake_home / "data" / "mas"
    assert mas_cache_root() == fake_home / "cache" / "mas"
    assert mas_state_root() == fake_home / "state" / "mas"


def test_xdg_defaults_when_env_unset(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    for name in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME"):
        monkeypatch.delenv(name, raising=False)

    assert xdg_config_home() == fake_home / ".config"
    assert xdg_data_home() == fake_home / ".local" / "share"
    assert xdg_cache_home() == fake_home / ".cache"
    assert xdg_state_home() == fake_home / ".local" / "state"


def test_xdg_env_path_is_resolved(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.chdir(fake_home)
    monkeypatch.setenv("XDG_DATA_HOME", "relative-data")

    assert xdg_data_home() == (fake_home / "relative-data").resolve()
