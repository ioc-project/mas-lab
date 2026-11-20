#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Only the kernel, its machines and a design pattern move the product state; nothing else writes it."""

from __future__ import annotations

import ast
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_ROOTS = [_REPO / "runtime" / "src", _REPO / "ctl" / "src", *sorted(_REPO.glob("library-*/src"))]
# Directories (by path segment sequence) that may assign ``q.<field>``.
_ALLOWED = (("mas", "runtime", "kernel"), ("mas", "runtime", "machines"), ("design_patterns",), ("design_pattern",))


def _allowed(path: Path) -> bool:
    parts = path.parts
    return any(any(parts[i : i + len(seg)] == seg for i in range(len(parts) - len(seg) + 1)) for seg in _ALLOWED)


def _is_product_state(node: ast.expr) -> bool:
    """``q`` or ``<anything>.q`` (``kernel.q``, ``self.kernel.q``)."""
    return (isinstance(node, ast.Name) and node.id == "q") or (isinstance(node, ast.Attribute) and node.attr == "q")


def _writes(tree: ast.AST) -> list[int]:
    lines: list[int] = []
    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        flat: list[ast.expr] = []
        for t in targets:
            flat.extend(t.elts if isinstance(t, (ast.Tuple, ast.List)) else [t])
        lines.extend(t.lineno for t in flat if isinstance(t, ast.Attribute) and _is_product_state(t.value))
    return lines


def test_nothing_outside_kernel_machines_and_patterns_writes_the_product_state() -> None:
    # Kills: a coordinator, driver or plugin assigning ``q.<field>`` on a side path (hidden state copied in).
    offenders: list[str] = []
    for root in _ROOTS:
        for path in root.rglob("*.py"):
            if _allowed(path):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            offenders.extend(f"{path.relative_to(_REPO)}:{line}" for line in _writes(tree))
    assert offenders == []


def test_the_driver_does_not_register_a_call_wait() -> None:
    # Kills: the driver calling register_inflight after an await. The assignment scan above misses a helper.
    driver = _REPO / "runtime" / "src" / "mas" / "runtime" / "driver" / "driver.py"
    tree = ast.parse(driver.read_text(encoding="utf-8"), filename=str(driver))
    calls = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == "register_inflight")
            or (isinstance(node.func, ast.Attribute) and node.func.attr == "register_inflight")
        )
    ]
    assert calls == []
