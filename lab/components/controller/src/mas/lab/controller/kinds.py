#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Kind metadata the UI uses instead of hardcoded folder / model lists."""

from __future__ import annotations

from typing import Any

NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
VERSION_PATTERN = r"^v[0-9]+([.][0-9]+)*$"

# Kinds a lab scope reports (no applications — a lab is not a library).
LAB_KINDS = (
    "experiment",
    "pipeline",
    "overlay",
    "dataset",
    "scenario",
)

# Kinds a library always offers. Experiments/pipelines appear when present.
LIBRARY_BASE_KINDS = (
    "application",
    "agent",
    "overlay",
    "dataset",
    "tool",
    "skill",
)

KIND_CATALOG: dict[str, dict[str, Any]] = {
    "application": {
        "id": "application",
        "plural": "applications",
        "schema_id": "mas",
        "creatable": True,
        "versioned": True,
        "runnable": True,
        "children": ["agent"],
        "name_pattern": NAME_PATTERN,
        "template_ids": ["blank"],
        "display_fields": ["title", "summary", "ref", "latest"],
    },
    "agent": {
        "id": "agent",
        "plural": "agents",
        "schema_id": "agent",
        "creatable": True,
        "versioned": False,
        "children": [],
        "name_pattern": NAME_PATTERN,
        "template_ids": ["blank"],
        "display_fields": ["title", "summary", "ref"],
    },
    "overlay": {
        "id": "overlay",
        "plural": "overlays",
        "schema_id": "overlay",
        "creatable": True,
        "versioned": False,
        "children": [],
        "name_pattern": NAME_PATTERN,
        "template_ids": ["blank"],
        "display_fields": ["title", "summary", "ref"],
    },
    "pipeline": {
        "id": "pipeline",
        "plural": "pipelines",
        "schema_id": "pipeline",
        "creatable": True,
        "versioned": False,
        "runnable": True,
        "children": [],
        "name_pattern": NAME_PATTERN,
        "template_ids": ["blank"],
        "display_fields": ["title", "summary", "ref"],
    },
    "experiment": {
        "id": "experiment",
        "plural": "experiments",
        "schema_id": "experiment",
        "creatable": True,
        "versioned": False,
        "runnable": True,
        "children": [],
        "name_pattern": NAME_PATTERN,
        "template_ids": ["blank"],
        "display_fields": ["title", "summary", "ref"],
    },
    "dataset": {
        "id": "dataset",
        "plural": "datasets",
        "schema_id": "dataset",
        "creatable": True,
        "versioned": False,
        "children": [],
        "name_pattern": NAME_PATTERN,
        "template_ids": ["blank"],
        "display_fields": ["title", "summary", "ref"],
    },
    "tool": {
        "id": "tool",
        "plural": "tools",
        "schema_id": "tool",
        "creatable": False,
        "versioned": False,
        "children": [],
        "name_pattern": NAME_PATTERN,
        "template_ids": [],
        "display_fields": ["title", "summary", "ref"],
    },
    "skill": {
        "id": "skill",
        "plural": "skills",
        "schema_id": "skill",
        "creatable": False,
        "versioned": False,
        "children": [],
        "name_pattern": NAME_PATTERN,
        "template_ids": [],
        "display_fields": ["title", "summary", "ref"],
    },
    "scenario": {
        "id": "scenario",
        "plural": "scenarios",
        "schema_id": "scenario",
        "creatable": False,
        "versioned": False,
        "children": [],
        "name_pattern": NAME_PATTERN,
        "template_ids": [],
        "display_fields": ["title", "summary", "ref"],
    },
}


def kind_info(kind: str) -> dict[str, Any]:
    info = KIND_CATALOG.get(kind)
    if info is None:
        raise KeyError(kind)
    return dict(info)


def list_kinds() -> list[dict[str, Any]]:
    return [dict(KIND_CATALOG[k]) for k in KIND_CATALOG]


def path_for_kind(kind: str) -> str:
    return str(KIND_CATALOG.get(kind, {}).get("plural") or f"{kind}s")
