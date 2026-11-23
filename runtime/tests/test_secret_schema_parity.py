#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Every schema property typed as a SecretRef is one ``prepare_manifest`` refuses as a literal, and back."""

from __future__ import annotations

from pathlib import Path

import yaml
from mas.runtime.secrets.ref import HTTP_SECRET_HEADERS, is_secret_field

SCHEMAS = Path(__file__).resolve().parents[2] / "docs" / "schemas"


def _is_secret_ref(node: object) -> bool:
    if not isinstance(node, dict):
        return False
    if str(node.get("$ref", "")).endswith("secret-ref.schema.yaml"):
        return True
    return any(_is_secret_ref(item) for key in ("oneOf", "anyOf", "allOf") for item in node.get(key) or [])


def _secret_ref_properties() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}

    def walk(node: object, where: str) -> None:
        if isinstance(node, dict):
            props = node.get("properties")
            if isinstance(props, dict):
                for name, sub in props.items():
                    if _is_secret_ref(sub):
                        found.setdefault(str(name), set()).add(where)
            for value in node.values():
                walk(value, where)
        elif isinstance(node, list):
            for item in node:
                walk(item, where)

    for path in sorted(SCHEMAS.rglob("*.schema.yaml")):
        walk(yaml.safe_load(path.read_text(encoding="utf-8")), path.name)
    return found


def test_schema_secret_fields_and_headers_match_what_prepare_manifest_refuses() -> None:
    found = _secret_ref_properties()
    lowered = {name.lower() for name in found}
    uncovered = sorted(n for n in found if not is_secret_field(n) and n.lower() not in HTTP_SECRET_HEADERS)
    assert uncovered == [], f"schema SecretRef fields prepare_manifest would accept as literals: {uncovered}"
    assert set(HTTP_SECRET_HEADERS) <= lowered, sorted(set(HTTP_SECRET_HEADERS) - lowered)
    assert {"api_key", "password", "secret"} <= set(found)
