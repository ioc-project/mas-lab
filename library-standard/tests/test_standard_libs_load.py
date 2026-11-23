#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Every shipped ``standard:`` manifest loads, and the keyless local servers need no secret."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

LIBS = Path(__file__).resolve().parents[1] / "src" / "mas" / "library" / "standard" / "libs" / "standard"
FILES = sorted(LIBS.glob("*.yaml"))


def test_the_standard_library_ships_manifests() -> None:
    assert len(FILES) > 10


@pytest.mark.parametrize("path", FILES, ids=[path.stem for path in FILES])
def test_every_standard_manifest_parses_and_passes_prepare_manifest(path: Path) -> None:
    from mas.runtime.secrets import prepare_manifest

    docs = [doc for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")) if doc is not None]
    assert docs, path.name
    for doc in docs:
        assert isinstance(doc, dict) and doc.get("kind"), path.name
        prepare_manifest(doc)


@pytest.mark.parametrize("path", FILES, ids=[path.stem for path in FILES])
def test_every_standard_manifest_resolves_through_the_infra_loader(path: Path, tmp_path: Path) -> None:
    from mas.ctl.infra.resolve import resolve_infra_refs

    resolve_infra_refs([f"standard:{path.stem}"], anchor=tmp_path, catalog=True)


@pytest.mark.parametrize("name", ["ollama", "vllm", "litellm"])
def test_a_keyless_local_server_starts_with_an_empty_secret_store(name: str, tmp_path: Path) -> None:
    from mas.ctl.infra.resolve import resolve_infra_refs
    from mas.ctl.session.engine_factory import _require_leaf_credentials
    from secret_fakes import bound_secrets

    infra = resolve_infra_refs([f"standard:{name}"], anchor=tmp_path, catalog=True)
    with bound_secrets({}):
        _require_leaf_credentials(dict(infra.llm_proxy or {}), dict(infra.llm_proxies or {}), strict_replay=False)
