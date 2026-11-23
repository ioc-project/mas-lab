#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Load deployment/v1 manifests and resolve runtime_id for a run."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mas.ctl.deployment.runtime_id import DEFAULT_RUNTIME_ID, normalize_runtime_id, runtime_id_from_deployment
from mas.ctl.registry.catalog import validate_runtime_id
from mas.ctl.workspace.config import WorkspaceConfig
from mas.runtime.secrets import load_checked_document


def default_deployment(*, runtime_id: str = DEFAULT_RUNTIME_ID, name: str = "local-inproc") -> dict[str, Any]:
    rid = validate_runtime_id(normalize_runtime_id(runtime_id))
    return {
        "apiVersion": "deployment/v1",
        "kind": "Deployment",
        "metadata": {"name": name},
        "spec": {
            "runtime_id": rid,
            "strategy": "local-inproc",
            "framework": {"default_adapter": "native"},
            "agents": [],
            "bus": {"kind": "inproc"},
            "shared": {"observability_ref": "native"},
        },
    }


def _load_yaml(path: Path) -> dict[str, Any]:
    return load_checked_document(path)


def load_deployment(
    *,
    manifest_dir: Path,
    deployment_path: Path | None = None,
    deployment_name: str | None = None,
) -> dict[str, Any]:
    """Load deployment manifest: explicit path → deployments/<name>.yaml → default file."""
    if deployment_path and deployment_path.is_file():
        return _load_yaml(deployment_path)

    deployments_dir = manifest_dir / "deployments"
    if deployment_name:
        candidate = deployments_dir / (
            deployment_name if deployment_name.endswith(".yaml") else f"{deployment_name}.yaml"
        )
        if candidate.is_file():
            return _load_yaml(candidate)

    default = deployments_dir / "local-inproc.yaml"
    if default.is_file():
        return _load_yaml(default)

    # Walk up for workspace-level deployments/ (repo root)
    for parent in [manifest_dir, *manifest_dir.parents]:
        ws_default = parent / "deployments" / "local-inproc.yaml"
        if ws_default.is_file():
            return _load_yaml(ws_default)

    return default_deployment()


def resolve_runtime_id(
    *,
    deployment: dict[str, Any] | None = None,
    runtime_profile: dict[str, Any] | None = None,
    workspace: WorkspaceConfig | None = None,
    cli_override: str | None = None,
) -> str:
    """Resolve runtime_id: CLI override → deployment → runtime profile → workspace → default runtime."""
    if cli_override:
        return validate_runtime_id(normalize_runtime_id(cli_override))

    if deployment:
        rid = runtime_id_from_deployment(deployment)
        if rid:
            return validate_runtime_id(rid)

    if runtime_profile:
        spec = runtime_profile.get("spec") or {}
        rid = spec.get("runtime_id")
        if isinstance(rid, str) and rid.strip():
            return validate_runtime_id(normalize_runtime_id(rid))

    if workspace and workspace.found:
        rid = workspace.runtime_id
        if rid:
            return validate_runtime_id(normalize_runtime_id(rid))

    return validate_runtime_id(DEFAULT_RUNTIME_ID)


def load_deployment_for_run(
    *,
    manifest_dir: Path,
    deployment_path: Path | None = None,
    deployment_name: str | None = None,
    workspace: WorkspaceConfig | None = None,
) -> dict[str, Any]:
    ws = workspace or WorkspaceConfig.load(manifest_dir)
    dep_name = deployment_name or ws.deployment_name
    return load_deployment(
        manifest_dir=manifest_dir,
        deployment_path=deployment_path,
        deployment_name=dep_name,
    )


def resolve_runtime_id_for_run(
    *,
    manifest_dir: Path,
    deployment_path: Path | None = None,
    deployment_name: str | None = None,
    runtime_profile_path: Path | None = None,
    cli_override: str | None = None,
    workspace: WorkspaceConfig | None = None,
) -> str:
    ws = workspace or WorkspaceConfig.load(manifest_dir)
    deployment = load_deployment_for_run(
        manifest_dir=manifest_dir,
        deployment_path=deployment_path,
        deployment_name=deployment_name,
        workspace=ws,
    )
    profile: dict[str, Any] | None = None
    if runtime_profile_path and runtime_profile_path.is_file():
        profile = _load_yaml(runtime_profile_path)
    elif ws.runtime_profile_path:
        profile = _load_yaml(ws.runtime_profile_path)
    return resolve_runtime_id(
        deployment=deployment,
        runtime_profile=profile,
        workspace=ws,
        cli_override=cli_override,
    )
