#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Shared fixtures for controller tests."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _reset_controller_api():
    yield
    from mas.lab.controller.api import set_controller_api
    from mas.lab.controller.run_bridge import reset_follow_state

    set_controller_api(None)
    reset_follow_state()


@pytest.fixture(autouse=True)
def _controller_test_defaults(monkeypatch: pytest.MonkeyPatch):
    """Speed up controller tests by disabling startup discovery noise and churn."""
    monkeypatch.setenv("MAS_CONTROLLER_DISABLE_STARTUP_DISCOVERY", "1")
    monkeypatch.setenv("MAS_CONTROLLER_REFRESH_INTERVAL_S", "5")
    monkeypatch.setenv("MAS_LAB_REGISTRY_EAGER_REFRESH", "0")
    monkeypatch.delenv("MAS_WORKSPACE_ROOT", raising=False)


@pytest.fixture(autouse=True)
def _clear_library_discovery_caches():
    """Reset the lru_cache-memoized library/catalog discovery helpers.

    See ``runtime/tests/conftest.py`` for the full rationale: these caches
    are keyed by resolved root path, so a test that writes a library dir,
    resolves it, then rewrites the same path must still see the change.
    """
    from mas.library_catalog import clear_catalog_discovery_cache
    from mas.library_roots import clear_library_discovery_cache

    clear_catalog_discovery_cache()
    clear_library_discovery_cache()
    yield
    clear_catalog_discovery_cache()
    clear_library_discovery_cache()


@pytest.fixture
def temp_mas_home(monkeypatch: pytest.MonkeyPatch):
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        monkeypatch.setenv("MAS_HOME", str(home))
        monkeypatch.setenv("MAS_CONTROLLER_DIR", str(home / "controller"))
        monkeypatch.setenv("MAS_CONTROLLER_SOCKET", str(home / "controller.sock"))
    monkeypatch.setenv("MAS_CONTROLLER_PID", str(home / "controller.pid"))
    monkeypatch.setenv("MAS_CONTROLLER_LOG", str(home / "controller" / "daemon.log"))
    yield home


@pytest.fixture
def sample_lab(tmp_path: Path) -> Path:
    lab = tmp_path / "demo.lab"
    (lab / "experiments").mkdir(parents=True)
    (lab / "pipelines").mkdir()
    (lab / "overlays").mkdir()
    (lab / "datasets").mkdir()
    (lab / "apps").mkdir()
    (lab / "flavours").mkdir()
    (lab / "infra").mkdir()
    (lab / "skills" / "demo-skill").mkdir(parents=True)
    (lab / "skills" / "demo-skill" / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: A demo skill\n---\n# Demo\n",
        encoding="utf-8",
    )
    (lab / "experiments" / "smoke.yaml").write_text(
        "experiment:\n  name: smoke\n  description: smoke test\n  version: '1'\n",
        encoding="utf-8",
    )
    (lab / "pipelines" / "analysis.yaml").write_text(
        "pipeline:\n  name: analysis\n  description: test pipeline\n  steps:\n"
        "  - name: step1\n    type: noop\n    depends_on: []\n",
        encoding="utf-8",
    )
    (lab / "overlays" / "baseline.yaml").write_text(
        "apiVersion: mas/v1\nkind: Overlay\ndescription: baseline overlay\n",
        encoding="utf-8",
    )
    (lab / "datasets" / "queries.yaml").write_text(
        "apiVersion: lab/v1\nkind: Dataset\nmetadata:\n  name: queries\n"
        "  description: dataset\nspec:\n  items: []\n",
        encoding="utf-8",
    )
    (lab / "datasets" / "items.yaml").write_text(
        "apiVersion: lab/v1\nkind: Dataset\nmetadata:\n  name: items\n"
        "  description: yaml dataset\nspec:\n  items: []\n",
        encoding="utf-8",
    )
    (lab / "apps" / "team").mkdir(parents=True)
    (lab / "apps" / "team" / "mas.yaml").write_text(
        "kind: MAS\nmetadata:\n  name: team\n",
        encoding="utf-8",
    )
    (lab / "infra" / "tools.yaml").write_text(
        "kind: ToolProvider\nspec:\n  tools:\n    search: {}\n",
        encoding="utf-8",
    )
    return lab
