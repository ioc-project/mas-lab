#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""A broken or absent config file loads as the defaults; it never raises or echoes its content."""

from __future__ import annotations

from pathlib import Path

import pytest
from mas.ctl.workspace.config import UserConfig, WorkspaceConfig
from mas.ctl.workspace.user_connections import load_user_connections
from mas.runtime.workspace_config import _CONFIG_FILE


def test_a_workspace_file_that_is_not_yaml_loads_as_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAS_WORKSPACE_ROOT", raising=False)
    (tmp_path / _CONFIG_FILE).write_text("a: [unclosed\n", encoding="utf-8")
    assert WorkspaceConfig.load(tmp_path)._data == {}


def test_a_user_config_that_is_not_yaml_loads_as_the_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    config = tmp_path / "mas" / "config.yaml"
    config.parent.mkdir()
    config.write_text("a: [unclosed\n", encoding="utf-8")
    assert UserConfig.load().default_infra == "standard:production"


def test_no_connections_file_is_an_empty_mapping(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert load_user_connections() == {}
