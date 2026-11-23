# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

import pytest

pytest_plugins = ["mas.runtime.hostenv_pytest", "lab_secret_state"]


@pytest.fixture(autouse=True)
def _isolate_user_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Do not read the developer's XDG config or MAS_INFRA_REFS."""
    xdg = tmp_path / "xdg-config"
    (xdg / "mas").mkdir(parents=True, exist_ok=True)
    (xdg / "mas" / "config.yaml").write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.delenv("MAS_INFRA_REFS", raising=False)
