#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Shared helpers for builtin scenario generators."""

from __future__ import annotations


def items_by_id(dataset_items: list[dict]) -> dict[str, dict]:
    return {str(item.get("id")): item for item in dataset_items}


def require_ids(requested: list, available: set[str], *, kind: str) -> list[str]:
    ids = [str(x) for x in requested]
    missing = [i for i in ids if i not in available]
    if missing:
        raise ValueError(f"design references unknown {kind}: {missing}")
    return ids
