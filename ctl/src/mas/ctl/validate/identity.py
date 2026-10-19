#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Declared MAS/agent identity checks for ``mas-ctl validate``.

Identity is ``metadata.name`` (and optional ``otel.app_name`` / ``otel.mas_id``).
A filesystem path is a contract error. ``otel.service_name`` is not identity.
"""

from __future__ import annotations

from typing import Any, Iterator


def _path_like(value: str) -> bool:
    return "/" in value or "\\" in value


def _identity_issues(value: str, *, field: str, required: bool) -> list:
    from mas.ctl.validate.validator import ValidationIssue

    text = str(value or "").strip()
    if not text:
        if not required:
            return []
        return [
            ValidationIssue(
                "error",
                f"{field} is required and must be a declared identity, not empty",
                path=field,
            )
        ]
    if _path_like(text):
        return [
            ValidationIssue(
                "error",
                f"{field} must be a declared identity, not a filesystem path: {text!r}",
                path=field,
            )
        ]
    return []


def _iter_observability_entries(block: Any, path: str) -> Iterator[tuple[str, Any]]:
    if isinstance(block, list):
        for idx, item in enumerate(block):
            yield f"{path}.{idx}", item
        return
    if not isinstance(block, dict):
        return
    op = block.get("$op")
    if isinstance(op, dict):
        for key in ("replace", "add"):
            items = op.get(key)
            if isinstance(items, list):
                for idx, item in enumerate(items):
                    yield f"{path}.$op.{key}.{idx}", item


def _iter_otel_configs(data: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    spec = data.get("spec") if isinstance(data.get("spec"), dict) else {}
    blocks: list[tuple[str, Any]] = [("spec.observability", spec.get("observability"))]
    patch = spec.get("patch") if isinstance(spec.get("patch"), dict) else {}
    blocks.append(("spec.patch.observability", patch.get("observability")))
    agents = patch.get("agents")
    if isinstance(agents, dict):
        for agent_id, agent_patch in agents.items():
            if isinstance(agent_patch, dict):
                blocks.append(
                    (
                        f"spec.patch.agents.{agent_id}.observability",
                        agent_patch.get("observability"),
                    )
                )

    for block_path, block in blocks:
        for item_path, item in _iter_observability_entries(block, block_path):
            if not isinstance(item, dict):
                continue
            cfg = item.get("otel")
            if isinstance(cfg, dict):
                yield f"{item_path}.otel", cfg


def _iter_events_to_otel_configs(data: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    for root_path, root in (
        ("spec.steps", (data.get("spec") or {}).get("steps") if isinstance(data.get("spec"), dict) else None),
        (
            "pipeline.steps",
            (data.get("pipeline") or {}).get("steps") if isinstance(data.get("pipeline"), dict) else None,
        ),
    ):
        if not isinstance(root, list):
            continue
        for idx, step in enumerate(root):
            if not isinstance(step, dict):
                continue
            if str(step.get("type") or "") != "events_to_otel":
                continue
            cfg = step.get("config")
            if isinstance(cfg, dict):
                yield f"{root_path}.{idx}.config", cfg


def collect_identity_issues(data: dict[str, Any], kind: str | None) -> list:
    """Return identity errors for Agent, MAS, Overlay, Flavour, and pipeline steps."""
    from mas.ctl.validate.validator import ValidationIssue

    issues: list = []
    resolved = str(kind or "").strip().lower()
    if resolved in {"agent", "mas"}:
        issues.extend(
            _identity_issues(
                str(((data.get("metadata") or {}).get("name") or "")),
                field="metadata.name",
                required=True,
            )
        )

    for path, cfg in _iter_otel_configs(data):
        issues.extend(
            _identity_issues(
                str(cfg.get("app_name") or ""),
                field=f"{path}.app_name",
                required=False,
            )
        )
        issues.extend(
            _identity_issues(
                str(cfg.get("mas_id") or ""),
                field=f"{path}.mas_id",
                required=False,
            )
        )

    for path, cfg in _iter_events_to_otel_configs(data):
        issues.extend(
            _identity_issues(
                str(cfg.get("app_name") or ""),
                field=f"{path}.app_name",
                required=False,
            )
        )
        service_name = str(cfg.get("service_name") or "").strip()
        app_name = str(cfg.get("app_name") or "").strip()
        if service_name and not app_name:
            issues.append(
                ValidationIssue(
                    "error",
                    "events_to_otel service_name is not MAS identity; set app_name "
                    "or stamp app_name on the events",
                    path=f"{path}.service_name",
                )
            )

    return issues


__all__ = ["collect_identity_issues"]
