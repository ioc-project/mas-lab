#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The local stand-in of the fares tool: a fixed table. Infra decides whether the real one is behind a server."""

from __future__ import annotations

from typing import Any

FARES = {"CV": 24, "VL": 18}


class GetFaresTool:
    def on_collect_tools(self, **_: Any) -> list[dict[str, Any]]:
        return [
            {
                "name": "get_fares",
                "description": "Return the one-way fare of a route.",
                "parameters": {
                    "type": "object",
                    "properties": {"route_id": {"type": "string"}},
                    "required": ["route_id"],
                },
            }
        ]

    def call_tool(self, name: str, arguments: dict[str, Any], **_: Any) -> dict[str, Any]:
        route = str(arguments.get("route_id") or "")
        return {"route_id": route, "fare": FARES.get(route)}
