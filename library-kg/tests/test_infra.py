#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Infra-manifest (Neo4j target) tests for library-kg."""

from __future__ import annotations

from pathlib import Path

import pytest

from mas.library.kg.infra import Neo4jTarget, neo4j_target

_MANIFEST = {
    "apiVersion": "mas/v1",
    "kind": "Infra",
    "metadata": {"name": "local-neo4j"},
    "spec": {
        "targets": [
            {
                "kind": "Neo4j",
                "uri": "bolt://db:7687",
                "database": "kg",
                "username": "u",
                "password": {"secret": "neo4j"},
            },
            {"kind": "OtelCollector", "endpoint": "http://localhost:4318"},
        ]
    },
}


def test_resolve_from_dict():
    t = neo4j_target(_MANIFEST)
    assert isinstance(t, Neo4jTarget)
    assert t.resolved_uri() == "bolt://db:7687"
    assert t.resolved_database() == "kg"
    assert t.resolved_username() == "u"


def test_password_is_a_secret_ref_never_a_literal():
    from mas.runtime.secrets.errors import SecretMissing
    from secret_fakes import bound_secrets

    t = neo4j_target(_MANIFEST)
    assert t.password == {"secret": "neo4j"}
    with pytest.raises(SecretMissing, match="neo4j"):  # a configured ref is required
        t.password_value()
    with bound_secrets({"neo4j": "from-store"}):
        assert t.password_value() == "from-store"
    with bound_secrets({"neo4j": "from-store"}):
        assert (
            Neo4jTarget().password_value() == ""
        )  # nothing declared: no password, no default secret
    assert (
        Neo4jTarget(password={"secret": {"name": "neo4j", "optional": True}}).password_value() == ""
    )
    with pytest.raises(Exception, match="password"):
        Neo4jTarget(password="literal").password_value()


def test_overrides_win():
    t = Neo4jTarget(uri="bolt://manifest:7687")
    assert t.resolved_uri("bolt://override:7687") == "bolt://override:7687"


def test_shipped_yaml_manifest():
    manifest = Path(__file__).resolve().parents[1] / "infra" / "local-neo4j.yaml"
    assert neo4j_target(manifest).resolved_uri() == "bolt://localhost:7687"


def test_env_is_shortcut_for_neo4j_manifest(monkeypatch):
    from mas.library.kg.infra import resolve_neo4j

    monkeypatch.setenv("NEO4J_URI", "bolt://from-env:7687")
    t = resolve_neo4j()
    assert t.uri == "bolt://from-env:7687"


def test_missing_target_raises():
    with pytest.raises(ValueError):
        neo4j_target({"kind": "Infra", "spec": {"targets": [{"kind": "OtelCollector"}]}})


_CANARY = "Pw0rd-neo4j-canary"


@pytest.mark.parametrize(
    "target",
    [
        {"kind": "Neo4j", "uri": f"bolt://neo:{_CANARY}@127.0.0.1:7687"},
        {"kind": "Neo4j", "uri": "bolt://127.0.0.1:7687", "password": _CANARY},
    ],
)
def test_an_infra_file_with_a_credential_is_refused(tmp_path: Path, target: dict) -> None:
    import yaml
    from mas.runtime.secrets import SecretRefError

    path = tmp_path / "infra.yaml"
    path.write_text(
        yaml.safe_dump({"kind": "Infra", "spec": {"targets": [target]}}), encoding="utf-8"
    )
    with pytest.raises(SecretRefError) as caught:
        neo4j_target(path)
    assert str(path) in str(caught.value) and "spec.targets[0]" in str(caught.value)
    assert _CANARY not in str(caught.value)


def test_a_uri_override_with_a_credential_is_refused() -> None:
    from mas.runtime.secrets import SecretRefError

    from mas.library.kg.infra import resolve_neo4j, resolve_neo4j_conn

    uri = f"bolt://neo:{_CANARY}@graph:7687"
    for resolve in (resolve_neo4j, lambda **kw: resolve_neo4j_conn(infra=_MANIFEST, **kw)):
        with pytest.raises(SecretRefError, match="uri") as caught:
            resolve(uri=uri)
        assert _CANARY not in str(caught.value)
