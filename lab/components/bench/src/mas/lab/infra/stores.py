#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Resolve Datastore specs from infra YAML via mas-ctl workspace discovery."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from mas.ctl.infra.resolve import resolve_infra_refs
from mas.ctl.workspace.config import WorkspaceConfig
from mas.lab.infra.datastore import DatastoreSpec
from mas.runtime.secrets.errors import SecretRefError
from mas.runtime.secrets.manifest import load_checked_document

logger = logging.getLogger(__name__)


def _stores_from_infra_doc(data: dict[str, Any]) -> dict[str, DatastoreSpec]:
    spec = data.get("spec") or {}
    stores_raw = spec.get("stores") or {}
    if not isinstance(stores_raw, dict):
        return {}
    return {
        name: DatastoreSpec.from_store_dict(name, store if isinstance(store, dict) else {})
        for name, store in stores_raw.items()
    }


def resolve_datastore(store_id: str, *, anchor: Path | None = None) -> DatastoreSpec | None:
    """Load a named store from workspace infra refs (kind: Datastore or bundle includes)."""
    ws = WorkspaceConfig.load(anchor or Path.cwd())
    if not ws.found or ws.root is None:
        return None
    mas_lab = (ws._data.get("mas_lab") or {}) if hasattr(ws, "_data") else {}
    mas_ctl = (ws._data.get("mas_ctl") or {}) if hasattr(ws, "_data") else {}
    infra_ref = mas_lab.get("infra") or mas_ctl.get("infra")
    if not infra_ref:
        return None
    anchor = ws.root
    try:
        resolved = resolve_infra_refs([str(infra_ref)], anchor=anchor, workspace=ws)
        for part in getattr(resolved, "parts", []) or []:
            stores = _stores_from_infra_doc(getattr(part, "raw", {}) or {})
            if store_id in stores:
                return stores[store_id]
        raw = getattr(resolved, "raw", None) or {}
        stores = _stores_from_infra_doc(raw if isinstance(raw, dict) else {})
        if store_id in stores:
            return stores[store_id]
    except SecretRefError:
        raise
    except Exception:
        logger.debug("suppressed", exc_info=True)
    # Direct file ref relative to workspace
    ref_path = anchor / str(infra_ref)
    if ref_path.is_file():
        stores = _stores_from_infra_doc(load_checked_document(ref_path))
        if store_id in stores:
            return stores[store_id]
    return None
