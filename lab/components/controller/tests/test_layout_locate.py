#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Layouts resolve the folder they found; edits write there."""

from __future__ import annotations

from pathlib import Path

import pytest
from mas.lab.controller.manifest_layout import ApplicationLayout
from mas.lab.controller.scope_catalog import ScopeCatalog
from scope_fixtures import build_store, copy_scope_fixtures


@pytest.fixture
def catalog(tmp_path: Path):
    roots = copy_scope_fixtures(tmp_path)
    store = build_store(roots)
    return ScopeCatalog.from_registry(store._registry, read_only_slugs=("readonly",)), roots


def test_versioned_and_plain_and_renamed(catalog):
    scopes, roots = catalog
    scope = scopes.get("library:versioned_lib")
    layout = ApplicationLayout()
    v2 = layout.locate(scope, "triage@v2")
    assert v2 is not None
    assert v2.root == (roots["versioned_lib"] / "apps" / "triage" / "v2").resolve()
    plain = layout.locate(scope, "plain")
    assert plain is not None
    assert plain.root == (roots["versioned_lib"] / "apps" / "plain").resolve()
    renamed = layout.locate(scope, "renamed-app")
    assert renamed is not None
    assert renamed.root == (roots["versioned_lib"] / "apps" / "other-folder").resolve()


def test_root_app_and_lab_local(catalog):
    scopes, roots = catalog
    layout = ApplicationLayout()
    root_app = layout.locate(scopes.get("library:root_app"), "rootapp")
    assert root_app is not None
    assert root_app.root == roots["root_app"].resolve()
    hello = layout.locate(scopes.get("library:x_lib"), "hello")
    assert hello is not None
    assert hello.root == (roots["x_lib"] / "apps" / "hello").resolve()
