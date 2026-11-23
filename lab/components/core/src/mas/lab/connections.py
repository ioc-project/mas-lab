#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Connection resolution for lab codecs: hosts from hostenv, passwords as SecretRef.

Credentials are declared in an infra Datastore manifest (committed, no secrets).
The password is a SecretRef; the value lives in the secret store
(``mas-ctl secrets set neo4j`` / ``mas-ctl secrets set clickhouse``).

Resolution order for each non-secret field (highest → lowest priority):
  1. **CLI flag**                    ``--uri``, ``--user``, ``--database``
  2. **InfraManifest**               ``config/infra/neo4j.yaml`` / ``kind: Datastore``
                                     discovered via the nearest ``config.yaml``
  3. **User connections** (ctl loads ``$XDG_CONFIG_HOME/mas/connections.yaml`` and binds it)
  4. **Host values**                 ``NEO4J_URI`` / ``NEO4J_USER`` / ``CLICKHOUSE_HOST`` (non-secret)
  5. **Built-in default**            ``bolt://localhost:7687``, user ``neo4j``, db ``neo4j``

Canonical infra manifest (committed)::

    # config/infra/neo4j.yaml
    api_version: infra/v1
    kind: Datastore
    spec:
      stores:
        neo4j:
          uri:      bolt://localhost:7687
          user:     neo4j
          password: {secret: neo4j}
          database: neo4j
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any, Dict, Mapping, Optional

from mas.runtime import hostenv

_USER_CONNECTIONS: ContextVar[Mapping[str, Any] | None] = ContextVar("mas_lab_user_connections", default=None)


def bind_user_connections(data: Mapping[str, Any] | None) -> Token[Mapping[str, Any] | None]:
    """Hand the ctl-loaded connections file to lab resolvers. Lab never reads the host file."""
    return _USER_CONNECTIONS.set(dict(data or {}))


def reset_user_connections(token: Token[Mapping[str, Any] | None]) -> None:
    _USER_CONNECTIONS.reset(token)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_value(v: Any) -> str:
    """Resolve a single config value. Secrets are SecretRef objects; hosts are plain strings.

    A missing optional secret resolves to ``""``; a missing required one raises.
    """
    from mas.runtime.secrets import SecretRefError, current_resolver
    from mas.runtime.secrets.ref import is_ref, parse_secret_ref

    if isinstance(v, str) and v.startswith("env:"):
        raise SecretRefError(
            "env: prefix is removed; write a SecretRef ({secret: NAME}) for a secret, "
            "or a plain overlay value for a host"
        )
    if is_ref(v):
        return current_resolver().reveal_or_none(parse_secret_ref(v)) or ""
    return str(v) if v is not None else ""


def _load_named_connection(name: str) -> Dict[str, Any]:
    """Return the raw config entry for *name* from the mapping ctl bound.

    Empty when nothing is bound or the entry is absent. Lab never opens the host file.
    """
    raw = _USER_CONNECTIONS.get() or {}
    block = raw.get("connections") if isinstance(raw.get("connections"), dict) else raw
    entry = (block or {}).get(name) if isinstance(block, dict) else None
    return dict(entry) if isinstance(entry, dict) else {}


def _load_infra_store(store_id: str) -> Optional[Any]:
    """Return the ``DatastoreSpec`` for *store_id* from the nearest infra manifest.

    Discovers the infra bundle referenced by the nearest ``config.yaml``
    (same chain used by mas-runtime and mas-ctl), loads it, and returns the
    matching ``DatastoreSpec`` from ``infra.stores``.  Returns ``None`` on any
    failure (missing workspace, missing infra ref, missing store entry).
    """
    try:
        from mas.ctl.infra.models import InfraManifest  # noqa: F401
        from mas.ctl.infra.resolve import resolve_infra_refs as resolve_infra_ref_to_manifest
        from mas.lab.workspace import WorkspaceConfig
    except ImportError:
        return None

    try:
        ws = WorkspaceConfig.load()
        if not ws.found:
            return None

        # Try mas.lab > mas_ctl sections for the infra ref (same fallback as mas-lab eval)
        infra_ref: Optional[str] = ws.get("mas_lab", "infra") or ws.get("mas_ctl", "infra")
        if not infra_ref:
            return None

        fake_flavour = ws._path / "_conn.yaml"
        infra = resolve_infra_ref_to_manifest(infra_ref, fake_flavour)
        return infra.stores.get(store_id)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def resolve_clickhouse_conn(
    *,
    host: Optional[str] = None,
    port: Optional[int] = None,
    user: Optional[str] = None,
    database: Optional[str] = None,
) -> Dict[str, Any]:
    """Return the ClickHouse HTTP connection data, without a password.

    Resolution order per field:
        CLI flag > user connections (bound by ctl) > CLICKHOUSE_* host values > built-in default

    Nothing here touches the secret store: a caller that authenticates asks
    :func:`resolve_clickhouse_password` for the password when it connects.

    ``uri`` (``https://...`` for TLS) comes from the user connections only.

    Returns:
        Dict with keys: ``host``, ``port`` (int), ``user``, ``database``, ``uri``.
    """
    cfg = _load_named_connection("clickhouse")

    def _pick_ch(key: str, cli_val, env_var: str, default: Any) -> Any:
        if cli_val is not None:
            return cli_val
        if key in cfg:
            resolved = _resolve_value(cfg[key])
            if resolved:
                return resolved
        return hostenv.get(env_var) or default

    raw_port = _pick_ch("port", port, "CLICKHOUSE_PORT", 8123)
    try:
        resolved_port = int(raw_port)
    except (TypeError, ValueError):
        resolved_port = 8123

    return {
        "host": _pick_ch("host", host, "CLICKHOUSE_HOST", "localhost"),
        "port": resolved_port,
        "user": _pick_ch("user", user, "CLICKHOUSE_USER", "admin"),
        "database": _pick_ch("database", database, "CLICKHOUSE_DATABASE", "default"),
        "uri": cfg.get("uri") or "",
    }


def reveal_password(raw: Any) -> str:
    """The declared password (a SecretRef, required unless optional), ``""`` when none is declared."""
    from mas.runtime.secrets import current_resolver
    from mas.runtime.secrets.ref import parse_secret_ref_optional

    ref = parse_secret_ref_optional(raw, field="password")
    return (current_resolver().reveal_or_none(ref) or "") if ref else ""


def resolve_clickhouse_password() -> str:
    """Reveal the ClickHouse password the user connections declare, at the call that connects."""
    return reveal_password(_load_named_connection("clickhouse").get("password"))


def resolve_neo4j_conn(
    *,
    uri: Optional[str] = None,
    user: Optional[str] = None,
    database: Optional[str] = None,
    store_id: str = "neo4j",
) -> Dict[str, Any]:
    """Return the Neo4j Bolt connection data, without a password.

    Resolution order per field:
        CLI flag > infra manifest store (store_id) > user connections (bound by ctl) > built-in default

    Nothing here touches the secret store: a caller that authenticates reads the password from its target.

    Returns:
        Dict with keys: ``uri``, ``user``, ``database``.
    """
    store = _load_infra_store(store_id) if store_id else None
    if store is not None:
        return {
            "uri": uri or getattr(store, "uri", None) or "bolt://localhost:7687",
            "user": user or getattr(store, "user", None) or "neo4j",
            "database": database or getattr(store, "database", None) or "neo4j",
        }

    cfg = _load_named_connection("neo4j")

    def _pick(key: str, cli_val, default: Any) -> Any:
        if cli_val is not None:
            return cli_val
        if key in cfg:
            resolved = _resolve_value(cfg[key])
            if resolved:
                return resolved
        return default

    return {
        "uri": _pick("uri", uri, "bolt://localhost:7687"),
        "user": _pick("user", user, "neo4j"),
        "database": _pick("database", database, "neo4j"),
    }


def ensure_service_running(
    service_name: str,
    *,
    profile: str = "prod",
    mas_lab_url: Optional[str] = None,
    timeout: int = 60,
) -> None:
    """Ensure a workspace service is running via the mas-lab REST API.

    Calls ``POST /api/services/start`` on the running ``mas-lab serve`` instance,
    then polls the job until it completes.  Used to start kubectl port-forwards
    (profile ``prod``) or Docker Compose services (profile ``local``) before
    establishing a downstream connection.

    Args:
        service_name: Name matching ``config/services/<name>.yaml``.
        profile:      ``'prod'`` (kubectl port-forward) or ``'local'`` (Docker).
        mas_lab_url:  Base URL of the mas-lab server.  Defaults to the
                      ``MAS_LAB_URL`` environment variable, then
                      ``http://localhost:8090``.
        timeout:      Seconds to wait for the start job to complete.

    Raises:
        RuntimeError: If the API call fails or the job does not complete.
    """
    import json as _json
    import time as _time
    import urllib.error
    import urllib.request

    url = (mas_lab_url or hostenv.get("MAS_LAB_URL") or "http://localhost:8090").rstrip("/")

    body = _json.dumps({"services": [service_name], "profile": profile}).encode()
    req = urllib.request.Request(
        f"{url}/api/services/start",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            result = _json.loads(r.read())
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach mas-lab server at {url}: {exc}\nIs 'mas-lab serve' running?") from exc

    job_id = result.get("job_id")
    if not job_id:
        return  # service may have already been running

    deadline = _time.time() + timeout
    while _time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/api/jobs/{job_id}", timeout=5) as r:
                job = _json.loads(r.read())
            status = job.get("status", "running")
            if status == "done":
                return
            if status == "error":
                raise RuntimeError(
                    f"Service start failed for {service_name!r} (profile={profile}): {job.get('stderr', '')}"
                )
        except urllib.error.URLError:
            pass
        _time.sleep(1.5)

    raise RuntimeError(
        f"Timed out waiting for service {service_name!r} to start (profile={profile}, timeout={timeout}s)"
    )
