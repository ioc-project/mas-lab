#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Manifest validation endpoints."""

from __future__ import annotations

from fastapi import APIRouter
from mas.lab.controller import manifest_validation
from mas.lab.controller.models import ValidateRequest
from mas.lab.controller.path_safety import is_relative_name
from mas.lab.controller.routes._api import deps
from mas.runtime.spec.yaml_errors import safe_yaml_load

router = APIRouter()


@router.post("/api/libraries/{library_name}/validate", tags=["Libraries"])
def validate_manifest(library_name: str, req: ValidateRequest):
    """Validate an agent or MAS manifest in-process. Resolves refs relative to the library dir."""
    lib_dir = deps.get_library_path(library_name)
    # Unsaved drafts use __MAS_NAME__ placeholder — skip file-based ref
    # resolution since the agent files don't exist on disk yet.
    resolve = "__MAS_NAME__" not in req.manifest_yaml

    base_dir = lib_dir
    try:
        data = safe_yaml_load(req.manifest_yaml)
        kind = (data.get("kind") or "").lower() if isinstance(data, dict) else ""
        mas_name = data.get("metadata", {}).get("name", "") if isinstance(data, dict) else ""
        if mas_name and is_relative_name(mas_name) and "/" not in mas_name:
            app_dir = lib_dir / "apps" / mas_name
            base_dir = app_dir / "agents" if kind == "agent" else app_dir
    except Exception:  # a bad document is reported by the validation below
        pass

    return manifest_validation.validate_manifest_yaml_content(
        req.manifest_yaml, base_dir=base_dir, resolve_refs=resolve
    )
