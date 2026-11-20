#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Fixed CPU work: SHA-256 round trip ``n`` times."""

from __future__ import annotations

import hashlib
from typing import Any

from mas.runtime.contracts import ToolContract


class WorkTool(ToolContract):
    """Hash a buffer ``n`` times so a run has a known amount of CPU work."""

    def list_tools(self, **_: Any) -> list[dict[str, Any]]:
        return [
            {
                "name": "work",
                "description": "Hash a buffer n times and return the hex digest.",
                "parameters": {
                    "type": "object",
                    "properties": {"n": {"type": "integer", "description": "Number of SHA-256 rounds."}},
                    "required": ["n"],
                },
            }
        ]

    def call_tool(self, tool_name: str, arguments: dict[str, Any], **_: Any) -> Any:
        if tool_name != "work":
            return None
        rounds = max(1, int(arguments.get("n") or 1))
        digest = hashlib.sha256(b"lab-run-throughput").digest()
        for _ in range(rounds):
            digest = hashlib.sha256(digest).digest()
        return {"n": rounds, "digest": digest.hex()}
