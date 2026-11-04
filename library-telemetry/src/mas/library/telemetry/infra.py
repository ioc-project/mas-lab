#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Infra manifest — *where* a span set serializes.

An **infra manifest** declares the serialization targets a run can push to. This
library owns the ``OtelCollector`` target kind (the telemetry counterpart of the
``Neo4j`` target that ``library-kg`` owns): it tells :func:`push`/``export`` steps
which OTLP endpoint to send spans to.

Manifest shape (``kind: Infra``)::

    apiVersion: mas/v1
    kind: Infra
    metadata:
      name: local-otel
    spec:
      targets:
        - kind: OtelCollector
          endpoint: http://localhost:4318      # OTLP HTTP/JSON base URL
          protocol: otlp-http                  # only otlp-http today
          app_name: my-app                     # optional application_id override

Resolution precedence for the endpoint: explicit call arg → manifest
``endpoint`` → ``$OTEL_EXPORTER_OTLP_ENDPOINT``. Setting the standard env var
is a shortcut for shipping ``infra/local-otel.yaml`` — you do not need a
manifest file when the env is set. ClickHouse read-back is the same idea with
``$CLICKHOUSE_HOST`` vs ``infra/local-clickhouse.yaml``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from mas.library.telemetry._dist import extra_hint

OTEL_COLLECTOR_KIND = "OtelCollector"
CLICKHOUSE_KIND = "ClickHouse"
_ENDPOINT_ENV = "OTEL_EXPORTER_OTLP_ENDPOINT"


@dataclass(frozen=True)
class OtelCollectorTarget:
    """A resolved OTLP collector serialization target."""

    endpoint: str
    protocol: str = "otlp-http"
    service_name: str = ""
    app_name: str = ""

    def resolved_endpoint(self, override: Optional[str] = None) -> str:
        """Endpoint with precedence: *override* → manifest → ``$OTEL_..._ENDPOINT``."""
        return override or self.endpoint or os.environ.get(_ENDPOINT_ENV, "")


@dataclass(frozen=True)
class ClickHouseTarget:
    """A resolved ClickHouse read-back target (collector persistence)."""

    host: str = "localhost"
    port: int = 8123
    database: str = "default"
    user: str = "default"
    password_env: str = "CLICKHOUSE_PASSWORD"
    table: str = "otel_traces"

    def resolved_host(self) -> str:
        return self.host or os.environ.get("CLICKHOUSE_HOST", "localhost")

    def resolved_port(self) -> int:
        return self.port or int(os.environ.get("CLICKHOUSE_PORT", "8123"))

    def resolved_database(self) -> str:
        return self.database or os.environ.get("CLICKHOUSE_DATABASE", "default")

    def resolved_user(self) -> str:
        return self.user or os.environ.get("CLICKHOUSE_USER", "default")


def resolve_otel_collector(
    infra: str | Path | Dict[str, Any] | None = None,
    *,
    endpoint: str | None = None,
) -> OtelCollectorTarget:
    """Resolve an OTel collector the same way a live export would.

    ``$OTEL_EXPORTER_OTLP_ENDPOINT`` is a shortcut for a one-target
    ``OtelCollector`` infra manifest. Precedence: *endpoint* → *infra* → env.
    """
    if infra is not None:
        target = otel_collector_target(infra)
        resolved = target.resolved_endpoint(endpoint)
        if not resolved:
            raise ValueError(
                f"no OTLP endpoint — infra {infra!r} has an empty endpoint and "
                f"${_ENDPOINT_ENV} is unset"
            )
        return OtelCollectorTarget(
            endpoint=resolved,
            protocol=target.protocol,
            service_name=target.service_name,
            app_name=target.app_name,
        )
    resolved = (endpoint or os.environ.get(_ENDPOINT_ENV, "")).strip()
    if not resolved:
        raise ValueError(
            "no OTLP endpoint — pass infra=, endpoint=, or set "
            f"${_ENDPOINT_ENV} (env is a shortcut for local-otel.yaml)"
        )
    return OtelCollectorTarget(endpoint=resolved)


def resolve_clickhouse(
    infra: str | Path | Dict[str, Any] | None = None,
) -> ClickHouseTarget:
    """Resolve ClickHouse from an infra manifest, or from ``CLICKHOUSE_*`` env.

    ``$CLICKHOUSE_HOST`` is a shortcut for ``infra/local-clickhouse.yaml``.
    """
    if infra is not None:
        return clickhouse_target(infra)
    host = os.environ.get("CLICKHOUSE_HOST", "").strip()
    if not host:
        raise ValueError(
            "no ClickHouse host — pass infra= or set $CLICKHOUSE_HOST "
            "(env is a shortcut for local-clickhouse.yaml)"
        )
    return ClickHouseTarget(
        host=host,
        port=int(os.environ.get("CLICKHOUSE_PORT", "8123")),
        database=os.environ.get("CLICKHOUSE_DATABASE", "default"),
        user=os.environ.get("CLICKHOUSE_USER", "default"),
        password_env="CLICKHOUSE_PASSWORD",
    )


def _load_manifest(manifest: str | Path | Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(manifest, dict):
        return manifest
    path = Path(manifest)
    text = path.read_text(encoding="utf-8")
    if path.suffix in {".yaml", ".yml"}:
        try:
            import yaml
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "pyyaml is required to read YAML infra manifests: "
                f"{extra_hint('verify')}"
            ) from exc
        return yaml.safe_load(text) or {}
    return json.loads(text)


def _targets(manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    spec = manifest.get("spec") or manifest
    targets = spec.get("targets")
    if isinstance(targets, list):
        return targets
    # Allow a single-target shorthand: spec: {kind: OtelCollector, endpoint: ...}
    if spec.get("kind"):
        return [spec]
    return []


def otel_collector_target(
    manifest: str | Path | Dict[str, Any],
    *,
    name: Optional[str] = None,
) -> OtelCollectorTarget:
    """Resolve the ``OtelCollector`` target from an infra manifest.

    Args:
        manifest: Path to a ``kind: Infra`` YAML/JSON file, or the parsed dict.
        name: If several OtelCollector targets exist, pick the one whose
            ``name`` matches; otherwise the first is used.

    Raises:
        ValueError: when no ``OtelCollector`` target is present.
    """
    doc = _load_manifest(manifest)
    candidates = [
        t
        for t in _targets(doc)
        if str(t.get("kind")) == OTEL_COLLECTOR_KIND
        and (name is None or t.get("name") == name)
    ]
    if not candidates:
        raise ValueError(
            f"no {OTEL_COLLECTOR_KIND!r} target in infra manifest"
            + (f" (name={name!r})" if name else "")
        )
    t = candidates[0]
    return OtelCollectorTarget(
        endpoint=str(t.get("endpoint") or ""),
        protocol=str(t.get("protocol") or "otlp-http"),
        service_name=str(t.get("service_name") or ""),
        app_name=str(t.get("app_name") or ""),
    )


def clickhouse_target(
    manifest: str | Path | Dict[str, Any],
    *,
    name: Optional[str] = None,
) -> ClickHouseTarget:
    """Resolve the ``ClickHouse`` target from an infra manifest."""
    doc = _load_manifest(manifest)
    candidates = [
        t
        for t in _targets(doc)
        if str(t.get("kind")) == CLICKHOUSE_KIND
        and (name is None or t.get("name") == name)
    ]
    if not candidates:
        raise ValueError(
            f"no {CLICKHOUSE_KIND!r} target in infra manifest"
            + (f" (name={name!r})" if name else "")
        )
    t = candidates[0]
    return ClickHouseTarget(
        host=str(t.get("host") or "localhost"),
        port=int(t.get("port") or 8123),
        database=str(t.get("database") or "default"),
        user=str(t.get("user") or "default"),
        password_env=str(t.get("password_env") or "CLICKHOUSE_PASSWORD"),
        table=str(t.get("table") or "otel_traces"),
    )


__all__ = [
    "OtelCollectorTarget",
    "ClickHouseTarget",
    "otel_collector_target",
    "clickhouse_target",
    "resolve_otel_collector",
    "resolve_clickhouse",
    "OTEL_COLLECTOR_KIND",
    "CLICKHOUSE_KIND",
]
