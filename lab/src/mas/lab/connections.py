#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Connection credential resolution — mirroring the k8s secretKeyRef pattern.

Credentials are declared in ``config/infra/neo4j.yaml`` (committed, no secrets)
and resolved at runtime from the process environment or a ``.env`` file, exactly
like Kubernetes resolves ``secretKeyRef`` from a Secret.

Resolution order for each field (highest → lowest priority):
  1. **CLI flag**                    ``--uri``, ``--user``, ``--database``
  2. **InfraManifest**               ``config/infra/neo4j.yaml`` / ``kind: Datastore``
                                     discovered via the nearest ``config.yaml``
  3. **``$XDG_CONFIG_HOME/mas/connections.yaml``**  personal override (same ``env:VAR`` syntax)
  4. **``NEO4J_*`` environment variables**  backward compatibility
  5. **Built-in default**            ``bolt://localhost:7687``, user ``neo4j``, db ``neo4j``

The ``password`` field is intentionally absent from the CLI signature — it is
always resolved through the ``password_env`` name declared in the infra manifest,
the ``env:VAR`` indirection in connections.yaml, or directly from ``NEO4J_PASSWORD``.

Canonical infra manifest (committed, gitignored.free)::

    # config/infra/neo4j.yaml
    api_version: infra/v1
    kind: Datastore
    spec:
      stores:
        neo4j:
          uri:          bolt://localhost:7687
          user:         neo4j
          password_env: NEO4J_PASSWORD   # secretKeyRef — env var name, never the value
          database:     neo4j

Personal secrets (gitignored)::

    # config/secrets/.env  OR  export in shell
    NEO4J_PASSWORD=<your-password>

Quick-start (Neo4j graph store — internal extension, not shipped in OSS)::

    export NEO4J_PASSWORD=<secret>
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import yaml as _yaml  # type: ignore
except ImportError:
    _yaml = None  # type: ignore


from mas.runtime.constants import CONNECTIONS_CONFIG_FILENAME, WORKSPACE_CONFIG_FILENAME
from mas.runtime.xdg import mas_config_dir

_CONNECTIONS_FILE = mas_config_dir() / CONNECTIONS_CONFIG_FILENAME


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_dotenv() -> Dict[str, str]:
    """Minimal .env parser (walks up from CWD).  Does NOT modify os.environ.

    Search order:
      1. Walk up from CWD, looking for ``.env`` in each directory.
      2. ``<workspace_root>/config/secrets/.env`` — for secrets stored in the
         dedicated gitignored secrets directory.
    """
    result: Dict[str, str] = {}

    def _parse_file(path: Path) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
                v = v[1:-1]
            out[k] = v
        return out

    # 1. Walk up from CWD
    here = Path.cwd()
    for parent in (here, *here.parents):
        candidate = parent / ".env"
        if candidate.exists():
            result.update(_parse_file(candidate))
            break

    # 2. <workspace_root>/config/secrets/.env (higher priority — declared secrets)
    for parent in (here, *here.parents):
        if (parent / WORKSPACE_CONFIG_FILENAME).exists():
            secrets_env = parent / "config" / "secrets" / ".env"
            if secrets_env.exists():
                result.update(_parse_file(secrets_env))
            break

    return result


def _resolve_value(v: Any, dotenv: Dict[str, str]) -> str:
    """Resolve a single config value.

    - ``"env:VAR_NAME"``         → look up *VAR_NAME* in ``os.environ`` then *dotenv*
    - ``"env:VAR_NAME|default"`` → same, but fall back to *default* when unset
    - anything else              → ``str(v)`` (literal — allowed for non-secrets)
    """
    if isinstance(v, str) and v.startswith("env:"):
        rest = v[4:].strip()
        if "|" in rest:
            var, default = rest.split("|", 1)
            var = var.strip()
            default = default.strip()
        else:
            var, default = rest, ""
        return os.environ.get(var) or dotenv.get(var) or default
    return str(v) if v is not None else ""


def _load_named_connection(name: str) -> Dict[str, Any]:
    """Return the raw config entry for *name* from the user connections file.

    Returns an empty dict if the file does not exist or the entry is absent.
    """
    if _yaml is None or not _CONNECTIONS_FILE.exists():
        return {}
    raw = _yaml.safe_load(_CONNECTIONS_FILE.read_text(encoding="utf-8")) or {}
    return (raw.get("connections") or {}).get(name) or {}


def _load_infra_store(store_id: str) -> Optional[Any]:
    """Return the ``DatastoreSpec`` for *store_id* from the nearest infra manifest.

    Discovers the infra bundle referenced by the nearest ``config.yaml``
    (same chain used by mas-runtime and mas-ctl), loads it, and returns the
    matching ``DatastoreSpec`` from ``infra.stores``.  Returns ``None`` on any
    failure (missing workspace, missing infra ref, missing store entry).
    """
    try:
        from mas.lab.workspace import WorkspaceConfig
        from mas.ctl.infra.models import InfraManifest
        from mas.ctl.infra.resolve import resolve_infra_refs as resolve_infra_ref_to_manifest
    except ImportError:
        return None

    try:
        ws = WorkspaceConfig.load()
        if not ws.found:
            return None

        # Try mas.lab > mas_ctl sections for the infra ref (same fallback as mas-lab eval)
        infra_ref: Optional[str] = (
            ws.get("mas_lab", "infra")
            or ws.get("mas_ctl", "infra")
        )
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
    host:     Optional[str] = None,
    port:     Optional[int] = None,
    user:     Optional[str] = None,
    database: Optional[str] = None,
) -> Dict[str, Any]:
    """Return fully-resolved ClickHouse HTTP connection parameters.

    Resolution order per field:
        CLI flag > $XDG_CONFIG_HOME/mas/connections.yaml >
        CLICKHOUSE_* env vars > built-in default

    The ``password`` is always resolved from the ``password_env`` indirection in
    connections.yaml, or directly from ``CLICKHOUSE_PASSWORD``.

    Returns:
        Dict with keys: ``host``, ``port`` (int), ``user``, ``password``, ``database``.
    """
    dotenv = _load_dotenv()
    cfg = _load_named_connection("clickhouse")

    def _pick_ch(key: str, cli_val, env_var: str, default: Any) -> Any:
        if cli_val is not None:
            return cli_val
        if key in cfg:
            resolved = _resolve_value(cfg[key], dotenv)
            if resolved:
                return resolved
        return os.environ.get(env_var) or dotenv.get(env_var) or default

    password = ""
    if "password" in cfg:
        password = _resolve_value(cfg["password"], dotenv)
    if not password:
        password = os.environ.get("CLICKHOUSE_PASSWORD") or dotenv.get("CLICKHOUSE_PASSWORD") or ""

    raw_port = _pick_ch("port", port, "CLICKHOUSE_PORT", 8123)
    try:
        resolved_port = int(raw_port)
    except (TypeError, ValueError):
        resolved_port = 8123

    return {
        "host":     _pick_ch("host",     host,     "CLICKHOUSE_HOST",     "localhost"),
        "port":     resolved_port,
        "user":     _pick_ch("user",     user,     "CLICKHOUSE_USER",     "admin"),
        "password": password,
        "database": _pick_ch("database", database, "CLICKHOUSE_DATABASE", "default"),
    }


def resolve_neo4j_conn(
    *,
    uri:      Optional[str] = None,
    user:     Optional[str] = None,
    database: Optional[str] = None,
    store_id: str = "neo4j",
) -> Dict[str, Any]:
    """Return fully-resolved Neo4j Bolt connection parameters.

    Resolution order per field:
        CLI flag > infra manifest store (store_id) >
        $XDG_CONFIG_HOME/mas/connections.yaml >
        NEO4J_* env vars > built-in default

    Returns:
        Dict with keys: ``uri``, ``user``, ``password``, ``database``.
    """
    dotenv = _load_dotenv()
    cfg = _load_named_connection("neo4j")

    # Try infra manifest store first
    store = _load_infra_store(store_id) if store_id else None
    if store is not None:
        store_uri  = getattr(store, "uri",  None) or ""
        store_user = getattr(store, "user", None) or ""
        store_db   = getattr(store, "database", None) or ""
        pw_env     = getattr(store, "password_env", None) or "NEO4J_PASSWORD"
        store_pass = os.environ.get(pw_env) or dotenv.get(pw_env) or ""
        return {
            "uri":      uri      or store_uri  or os.environ.get("NEO4J_URI")      or dotenv.get("NEO4J_URI")      or "bolt://localhost:7687",
            "user":     user     or store_user or os.environ.get("NEO4J_USER")     or dotenv.get("NEO4J_USER")     or "neo4j",
            "password": store_pass,
            "database": database or store_db   or os.environ.get("NEO4J_DATABASE") or dotenv.get("NEO4J_DATABASE") or "neo4j",
        }

    def _pick(key: str, cli_val, env_var: str, default: Any) -> Any:
        if cli_val is not None:
            return cli_val
        if key in cfg:
            resolved = _resolve_value(cfg[key], dotenv)
            if resolved:
                return resolved
        return os.environ.get(env_var) or dotenv.get(env_var) or default

    password = ""
    if "password" in cfg:
        password = _resolve_value(cfg["password"], dotenv)
    if not password:
        pw_env = cfg.get("password_env", "NEO4J_PASSWORD")
        password = os.environ.get(pw_env) or dotenv.get(pw_env) or ""

    return {
        "uri":      _pick("uri",      uri,      "NEO4J_URI",      "bolt://localhost:7687"),
        "user":     _pick("user",     user,     "NEO4J_USER",     "neo4j"),
        "password": password,
        "database": _pick("database", database, "NEO4J_DATABASE", "neo4j"),
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

    url = (mas_lab_url
           or os.environ.get("MAS_LAB_URL")
           or "http://localhost:8090").rstrip("/")

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
        raise RuntimeError(
            f"Could not reach mas-lab server at {url}: {exc}\n"
            "Is 'mas-lab serve' running?"
        ) from exc

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
                    f"Service start failed for {service_name!r} "
                    f"(profile={profile}): {job.get('stderr', '')}"
                )
        except urllib.error.URLError:
            pass
        _time.sleep(1.5)

    raise RuntimeError(
        f"Timed out waiting for service {service_name!r} to start "
        f"(profile={profile}, timeout={timeout}s)"
    )
