#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Copy the five catalog fixtures into a temp tree and build an isolated store."""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi.testclient import TestClient

_FIXTURES = Path(__file__).resolve().parent / "fixtures"


def copy_scope_fixtures(tmp_path: Path) -> dict[str, Path]:
    dest = tmp_path / "scopes"
    mapping = {
        "versioned_lib": _FIXTURES / "versioned_lib",
        "x": _FIXTURES / "lab_with_local_lib" / "x.lab",
        "lonely": _FIXTURES / "lab_without_library" / "lonely.lab",
        "readonly": _FIXTURES / "readonly_lib",
        "root_app": _FIXTURES / "root_app_lib",
    }
    roots: dict[str, Path] = {}
    for slug, src in mapping.items():
        target = dest / slug
        shutil.copytree(src, target)
        roots[slug] = target
    # Lab-local library lives inside the lab copy.
    roots["x_lib"] = roots["x"] / "x_lib"
    return roots


def build_store(roots: dict[str, Path], *, read_only_slugs: tuple[str, ...] = ("readonly",)):
    from mas.lab.controller.lab_registry import LabRegistry
    from mas.lab.controller.manifest_store import ManifestStore

    registry = LabRegistry(workspace=None)
    libraries = {
        "versioned_lib": roots["versioned_lib"],
        "x": roots["x"],
        "x_lib": roots["x_lib"],
        "lonely": roots["lonely"],
        "readonly": roots["readonly"],
        "root_app": roots["root_app"],
    }
    sources = {
        "versioned_lib": "manifest_libraries:libs",
        "x": "labs_search_paths:labs",
        "x_lib": "lab-local:x",
        "lonely": "labs_search_paths:labs",
        "readonly": "entry-point:mas.runtime.manifest_libraries:readonly",
        "root_app": "manifest_libraries:.",
    }
    registry._libraries = libraries
    registry._library_sources = sources
    store = ManifestStore(registry=registry)
    store._libraries = dict(libraries)
    store._read_only_slugs = read_only_slugs
    return store


def make_client(store, monkeypatch) -> TestClient:
    from mas.lab.controller import deps, jobs
    from mas.lab.controller.fastapi_app import create_app
    from mas.lab.controller.scope_catalog import ScopeCatalog

    monkeypatch.setattr(deps, "_manifest_store", store)
    monkeypatch.setattr("mas.lab.controller.deps.get_manifest_store", lambda: store)
    jobs._jobs.clear()
    from mas.lab.controller.api import set_controller_api
    from mas.lab.controller.run_bridge import reset_follow_state

    set_controller_api(None)
    reset_follow_state()

    original = ScopeCatalog.from_registry

    def from_registry_with_readonly(registry, *, read_only_slugs=None):
        slugs = read_only_slugs or getattr(store, "_read_only_slugs", ("readonly",))
        return original(registry, read_only_slugs=slugs)

    monkeypatch.setattr(ScopeCatalog, "from_registry", from_registry_with_readonly)
    app = create_app()
    app.state.manifest_store = store
    return TestClient(app)
