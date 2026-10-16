#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Trip-planner tool: calc

Evaluates a safe arithmetic expression for cost aggregation.
Supports +, -, *, / and parentheses. Numbers only — no function calls.
"""

from __future__ import annotations

import ast
import operator
from typing import Any, Dict, List

from mas.runtime.contracts import ToolContract

_ALLOWED_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def _safe_eval(expr: str) -> float:
    """Evaluate *expr* using only numeric literals and arithmetic operators."""
    tree = ast.parse(expr.strip(), mode="eval")

    def _visit(node: ast.expr) -> float:
        if isinstance(node, ast.Expression):
            return _visit(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_OPERATORS:
            return _ALLOWED_OPERATORS[type(node.op)](_visit(node.left), _visit(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_OPERATORS:
            return _ALLOWED_OPERATORS[type(node.op)](_visit(node.operand))
        raise ValueError(f"Unsupported expression node: {ast.dump(node)}")

    return _visit(tree.body)


class CalcTool(ToolContract):
    """Evaluate a safe arithmetic expression for cost aggregation."""

    def list_tools(self, **_: Any) -> List[Dict[str, Any]]:
        return [
            {
                "name": "calc",
                "description": (
                    "Evaluate a basic arithmetic expression (+, -, *, /) "
                    "and return the numeric result. Use to sum or multiply costs."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "expression": {
                            "type": "string",
                            "description": "Arithmetic expression to evaluate.",
                        }
                    },
                    "required": ["expression"],
                },
            }
        ]

    def call_tool(self, tool_name: str, arguments: Dict[str, Any], **_: Any) -> Any:
        if tool_name != "calc":
            return None

        expr = arguments.get("expression", "")
        try:
            result = _safe_eval(expr)
            return {"expression": expr, "result": round(result, 2)}
        except Exception as exc:
            return {"expression": expr, "error": str(exc)}
