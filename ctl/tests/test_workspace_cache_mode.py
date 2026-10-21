# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
"""``mas_ctl.cache: read-only`` in the workspace config makes recorded answers the default."""

from __future__ import annotations

from pathlib import Path

import yaml
from mas.ctl.workspace.config import WorkspaceConfig


def test_workspace_cache_read_only_is_off_by_default() -> None:
    assert WorkspaceConfig({"mas_ctl": {"trace": "summary"}}).cache_read_only is False


def test_workspace_cache_read_only_when_configured() -> None:
    assert WorkspaceConfig({"mas_ctl": {"cache": "read-only"}}).cache_read_only is True


def test_config_schema_accepts_the_cache_key() -> None:
    schema = yaml.safe_load(
        (Path(__file__).resolve().parents[2] / "docs" / "schemas" / "config.schema.yaml").read_text(encoding="utf-8")
    )
    cache = schema["properties"]["mas_ctl"]["properties"]["cache"]
    assert cache["enum"] == ["read-only"]
