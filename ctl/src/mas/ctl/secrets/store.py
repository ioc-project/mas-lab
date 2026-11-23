#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Which secret store a workspace uses, where it lives, and the resolver built on it. Ctl only."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mas.runtime.secrets import SecretRefError, SecretResolver


def secrets_block(workspace: Any) -> dict[str, Any]:
    """The workspace ``secrets:`` block (``dir``, ``store``, ``allow_loose_permissions``)."""
    data = getattr(workspace, "_data", None)
    block = data.get("secrets") if isinstance(data, dict) else None
    return dict(block) if isinstance(block, dict) else {}


def secrets_dir_from_workspace(workspace: Any, *, override: Path | str | None = None) -> Path:
    """*override*, else the block's ``dir`` (relative to the workspace root), else ``<root>/.mas/secrets``."""
    if override:
        return Path(override).expanduser().resolve()
    root = Path(getattr(workspace, "root", None) or Path.cwd())
    raw = secrets_block(workspace).get("dir") or Path(".mas") / "secrets"
    return (root / Path(str(raw)).expanduser()).resolve()


def secret_store_kind(workspace: Any, *, override: str | None = None) -> str:
    return str(override or secrets_block(workspace).get("store") or "").strip() or "file"


def build_resolver(
    *,
    directory: Path | str | None = None,
    store: str = "file",
    workspace: Any = None,
    allow_loose_permissions: bool | None = None,
) -> SecretResolver:
    """Construct the store plugin and wrap it. Ctl only.

    ``allow_loose_permissions`` defaults to the workspace ``secrets.allow_loose_permissions``.
    """
    kind = (store or "file").strip() or "file"
    if kind == "env":
        from mas.library.standard.plugins.secrets.env_store import EnvSecretStore
        from mas.runtime import hostenv

        return SecretResolver(EnvSecretStore(hostenv.private), ttl_s=0.0)
    if kind != "file":
        raise SecretRefError(f"unknown secret store {kind!r}; use file or env")
    from mas.library.standard.plugins.secrets.file_store import FileSecretStore

    if allow_loose_permissions is None:
        allow_loose_permissions = secrets_block(workspace).get("allow_loose_permissions") is True
    path = secrets_dir_from_workspace(workspace, override=directory)
    return SecretResolver(FileSecretStore(path, allow_loose_permissions=allow_loose_permissions))


def install_process_resolver(
    *,
    directory: Path | str | None = None,
    store: str | None = None,
    workspace: Any = None,
) -> SecretResolver:
    """Build the store and install it as the process resolver (a later call replaces it)."""
    from mas.runtime.secrets import set_process_resolver

    ws = workspace
    if ws is None:
        from mas.ctl.workspace.config import WorkspaceConfig

        ws = WorkspaceConfig.load()
    kind = secret_store_kind(ws, override=store)
    resolver = build_resolver(directory=directory, store=kind, workspace=ws)
    set_process_resolver(resolver)
    return resolver
