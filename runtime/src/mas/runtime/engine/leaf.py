#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Find the live engine. The public object is LiveLlmEngine; this still
unwraps an old stack of inner layers in tests."""

from __future__ import annotations

from typing import Any

from mas.runtime.contracts.tool_semantics import existing_attr

# Distinct auto-vivifying mock wrappers evade id-based cycle detection.
_MAX_UNWRAP_DEPTH = 50


def leaf_engine(engine: Any) -> Any:
    """Return the innermost engine that handles LLM/tool IO.

    Reads ``.inner`` without ``__getattr__`` so a spec-less ``MagicMock``
    cannot auto-create an unbounded wrapper chain.
    """
    seen: set[int] = set()
    current = engine
    depth = 0
    while current is not None and id(current) not in seen:
        if depth >= _MAX_UNWRAP_DEPTH:
            raise RuntimeError(
                f"leaf_engine(): .inner chain exceeded {_MAX_UNWRAP_DEPTH} levels "
                "without terminating. A real engine wrapper stack is never this "
                "deep — this usually means `engine` is a test double (e.g. a bare "
                "MagicMock()) whose `.inner` auto-vivifies a new object on every "
                "access instead of returning None. Set `inner=None` explicitly "
                "on the mock/stub."
            )
        seen.add(id(current))
        inner = existing_attr(current, "inner")
        if inner is None or inner is current:
            return current
        current = inner
        depth += 1
    return current
