#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Infra manifest — *where* a KG serializes.

An **infra manifest** declares the serialization targets a run can push to. This
library owns the ``Neo4j`` target kind (the KG counterpart of the
``OtelCollector`` target that ``library-telemetry`` owns): it tells
``neo4j_push`` / ``neo4j_dump`` steps which graph database to use.

Manifest shape (``kind: Infra``)::

    apiVersion: mas/v1
    kind: Infra
    metadata:
      name: local-neo4j
    spec:
      targets:
        - kind: Neo4j
          uri: bolt://localhost:7687
          database: neo4j
          username: neo4j
          password_env: NEO4J_PASSWORD          # env var holding the password

Resolution precedence for connection fields: explicit call arg → manifest →
environment (``NEO4J_URI`` / ``NEO4J_USER`` / ``NEO4J_DB_AGENT`` /
``NEO4J_PASSWORD``). ``$NEO4J_URI`` is a shortcut for shipping
``infra/local-neo4j.yaml`` — you do not need a manifest file when the env is
set. The password is never stored in the manifest — only the name of the env
var that holds it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from mas.runtime import hostenv

NEO4J_KIND = "Neo4j"


@dataclass(frozen=True)
class Neo4jTarget:
    """A resolved Neo4j serialization target."""

    uri: str = "bolt://localhost:7687"
    database: str = "neo4j"
    username: str = "neo4j"
    password_env: str = "NEO4J_PASSWORD"

    def resolved_uri(self, override: Optional[str] = None) -> str:
        return override or self.uri or hostenv.get("NEO4J_URI", "bolt://localhost:7687")

    def resolved_username(self, override: Optional[str] = None) -> str:
        return override or self.username or hostenv.get("NEO4J_USER", "neo4j")

    def resolved_database(self, override: Optional[str] = None) -> str:
        return override or self.database or hostenv.get("NEO4J_DB_AGENT", "neo4j")

    def password(self) -> str:
        """Read the password from the configured env var (never from the manifest)."""
        return hostenv.get(self.password_env, "")


def _load_manifest(manifest: str | Path | Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(manifest, dict):
        return manifest
    path = Path(manifest)
    text = path.read_text(encoding="utf-8")
    if path.suffix in {".yaml", ".yml"}:
        import yaml

        return yaml.safe_load(text) or {}
    return json.loads(text)


def _targets(manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    spec = manifest.get("spec") or manifest
    targets = spec.get("targets")
    if isinstance(targets, list):
        return targets
    if spec.get("kind"):
        return [spec]
    return []


def resolve_neo4j(
    infra: str | Path | Dict[str, Any] | None = None,
    *,
    uri: Optional[str] = None,
) -> Neo4jTarget:
    """Resolve Neo4j the same way a live push would.

    ``$NEO4J_URI`` is a shortcut for ``infra/local-neo4j.yaml``. Precedence:
    *uri* → *infra* → env.
    """
    if infra is not None:
        target = neo4j_target(infra)
        return Neo4jTarget(
            uri=target.resolved_uri(uri),
            database=target.resolved_database(),
            username=target.resolved_username(),
            password_env=target.password_env,
        )
    resolved = (uri or hostenv.get("NEO4J_URI", "")).strip()
    if not resolved:
        raise ValueError(
            "no Neo4j URI — pass infra=, uri=, or set $NEO4J_URI "
            "(env is a shortcut for local-neo4j.yaml)"
        )
    return Neo4jTarget(
        uri=resolved,
        database=hostenv.get("NEO4J_DB_AGENT", "neo4j"),
        username=hostenv.get("NEO4J_USER", "neo4j"),
        password_env="NEO4J_PASSWORD",
    )


def neo4j_target(
    manifest: str | Path | Dict[str, Any],
    *,
    name: Optional[str] = None,
) -> Neo4jTarget:
    """Resolve the ``Neo4j`` target from an infra manifest.

    Raises:
        ValueError: when no ``Neo4j`` target is present.
    """
    doc = _load_manifest(manifest)
    candidates = [
        t
        for t in _targets(doc)
        if str(t.get("kind")) == NEO4J_KIND and (name is None or t.get("name") == name)
    ]
    if not candidates:
        raise ValueError(
            f"no {NEO4J_KIND!r} target in infra manifest" + (f" (name={name!r})" if name else "")
        )
    t = candidates[0]
    return Neo4jTarget(
        uri=str(t.get("uri") or "bolt://localhost:7687"),
        database=str(t.get("database") or "neo4j"),
        username=str(t.get("username") or "neo4j"),
        password_env=str(t.get("password_env") or "NEO4J_PASSWORD"),
    )


def resolve_neo4j_conn(
    *,
    infra: str | Path | Dict[str, Any] | None = None,
    uri: Optional[str] = None,
    username: Optional[str] = None,
    password_env: Optional[str] = None,
    database: Optional[str] = None,
) -> Dict[str, Optional[str]]:
    """Merge explicit connection overrides with an ``--infra`` manifest, field by field.

    Shared by ``lab_cli.py``'s ``neo4j-push``/``neo4j-dump`` commands and
    ``bench.py``'s ``Neo4jPushStep``/``Neo4jDumpStep`` — both accept the same
    four explicit overrides plus an optional infra-manifest fallback, and
    previously duplicated this exact merge. Each field: the explicit value
    if given, else the infra manifest's value (when *infra* is given), else
    ``None`` (the caller's own default, e.g. ``run_neo4j_push``'s, applies).
    """
    conn: Dict[str, Optional[str]] = {
        "uri": uri,
        "username": username,
        "password_env": password_env,
        "database": database,
    }
    if infra:
        target = neo4j_target(infra)
        conn["uri"] = conn["uri"] or target.uri
        conn["username"] = conn["username"] or target.username
        conn["password_env"] = conn["password_env"] or target.password_env
        conn["database"] = conn["database"] or target.database
    return conn


__all__ = [
    "Neo4jTarget",
    "neo4j_target",
    "resolve_neo4j",
    "resolve_neo4j_conn",
    "NEO4J_KIND",
]
