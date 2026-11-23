#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0

"""Datastore connection specs for bench codecs (ClickHouse, Neo4j, filesystem)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ArtifactBinding:
    kind: str = ""
    ops: list[str] = field(default_factory=list)


@dataclass
class DatastoreSpec:
    name: str = ""
    type: str = ""
    uri: str = ""
    host: str = ""
    port: int = 0
    path: str = ""
    user: str = ""
    password: object = None
    database: str = ""
    description: str = ""
    artifacts: list[ArtifactBinding] = field(default_factory=list)

    @classmethod
    def from_store_dict(cls, name: str, spec: dict) -> DatastoreSpec:
        artifacts = [
            ArtifactBinding(kind=a.get("kind", ""), ops=list(a.get("ops") or []))
            for a in (spec.get("artifacts") or [])
            if isinstance(a, dict)
        ]
        return cls(
            name=name,
            type=str(spec.get("type") or ""),
            uri=str(spec.get("uri") or ""),
            host=str(spec.get("host") or ""),
            port=int(spec.get("port") or 0),
            path=str(spec.get("path") or ""),
            user=str(spec.get("user") or ""),
            password=spec.get("password"),
            database=str(spec.get("database") or ""),
            description=str(spec.get("description") or ""),
            artifacts=artifacts,
        )
