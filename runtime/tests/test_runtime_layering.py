#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Kernel source must not import ``mas.library.standard`` (inverted dependency)."""

from __future__ import annotations

import ast
from pathlib import Path

_RUNTIME_SRC = Path(__file__).resolve().parents[1] / "src"
_FORBIDDEN = "mas.library.standard"


def _imported_modules(tree: ast.AST) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
        elif isinstance(node, ast.Call):
            func = node.func
            is_importlib = (
                isinstance(func, ast.Attribute)
                and func.attr == "import_module"
                and (
                    (isinstance(func.value, ast.Name) and func.value.id == "importlib")
                    or (
                        isinstance(func.value, ast.Attribute)
                        and func.value.attr == "importlib"
                    )
                )
            )
            is_dunder_import = isinstance(func, ast.Name) and func.id == "__import__"
            if is_importlib or is_dunder_import:
                if node.args and isinstance(node.args[0], ast.Constant):
                    val = node.args[0].value
                    if isinstance(val, str):
                        names.append(val)
    return names


def test_runtime_src_does_not_import_library_standard() -> None:
    offenders: list[str] = []
    for path in _RUNTIME_SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        rel = str(path.relative_to(_RUNTIME_SRC))
        if _FORBIDDEN in text:
            offenders.append(f"{rel}: substring {_FORBIDDEN!r}")
            continue
        tree = ast.parse(text, filename=str(path))
        for name in _imported_modules(tree):
            if name == _FORBIDDEN or name.startswith(f"{_FORBIDDEN}."):
                offenders.append(f"{rel}: {name}")
    assert offenders == []
