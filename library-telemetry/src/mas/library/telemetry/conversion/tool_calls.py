#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Render ``llm_call_end.tool_calls`` as completion text.

A tool-use turn has no output text; without this the chat span would show an
empty completion. The native KG handler renders the same text, so both paths
read identically (see ``library-kg`` ``handlers/chat.py``).
"""

from __future__ import annotations

import json
from typing import Any

MAX_ARGS_CHARS = 300


def tool_calls_text(tool_calls: Any) -> str:
    """One ``→ name(args)`` line per call; args cut at :data:`MAX_ARGS_CHARS`.

    Accepts OpenAI calls (``function.name`` / ``function.arguments``), the
    runtime's ``{toolName, arguments}`` and flat ``{name, arguments}``.
    Returns ``""`` for anything that is not a list of calls.
    """
    if not isinstance(tool_calls, list):
        return ""
    lines: list[str] = []
    for item in tool_calls:
        if not isinstance(item, dict):
            continue
        fn = item.get("function") if isinstance(item.get("function"), dict) else {}
        name = str(fn.get("name") or item.get("toolName") or item.get("name") or "tool")
        args = fn.get("arguments") or item.get("arguments") or "{}"
        if not isinstance(args, str):
            try:
                args = json.dumps(args, ensure_ascii=False)
            except TypeError:
                args = str(args)
        lines.append(f"→ {name}({args[:MAX_ARGS_CHARS]})")
    return "\n".join(lines)
