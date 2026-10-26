#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Trip-planner tool: get_fares

Returns one-way adult fares for a route leg and travel class.
Data is loaded from the Arborian Network dataset via ``_scene.py``.

Privilege isolation: this tool is exclusively for concierge_agent.
"""

from __future__ import annotations

from typing import Any, Dict, List

from mas.runtime.contracts import ToolContract

from ._scene import load_network


class GetTripFaresTool(ToolContract):
    """Look up one-way fare for a route_id + travel_class combination."""

    def __init__(self, dataset_path: str = "fixtures/arborian-network.yaml") -> None:
        self._dataset_path = dataset_path

    def list_tools(self, **_: Any) -> List[Dict[str, Any]]:
        return [
            {
                "name": "get_fares",
                "description": (
                    "Return the one-way adult fare (USD) for a route leg and travel class. "
                    "Use route_id from RouteOptions.hops[].route_id."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "route_id": {
                            "type": "string",
                            "description": "Route identifier (e.g. 'CV', 'CL-air').",
                        },
                        "travel_class": {
                            "type": "string",
                            "enum": ["Standard", "Express", "NightTrain_couchette",
                                     "Economy", "Business"],
                            "description": "Service class for the fare.",
                        },
                    },
                    "required": ["route_id", "travel_class"],
                },
            }
        ]

    def call_tool(self, tool_name: str, arguments: Dict[str, Any], **_: Any) -> Any:
        if tool_name != "get_fares":
            return None

        route_id = arguments.get("route_id", "")
        travel_class = arguments.get("travel_class", "Standard")

        network = load_network(self._dataset_path)
        routes = network.get("routes", [])

        for r in routes:
            if r.get("id", "") == route_id:
                fares = r.get("fares_usd", {})
                if travel_class in fares:
                    return {
                        "route_id": route_id,
                        "from": r.get("from", ""),
                        "to": r.get("to", ""),
                        "travel_class": travel_class,
                        "fare_usd": fares[travel_class],
                        "mode": r.get("mode", ""),
                    }
                # class not available on this route
                available = list(fares.keys())
                return {
                    "route_id": route_id,
                    "error": f"Travel class '{travel_class}' not available on route {route_id}.",
                    "available_classes": available,
                }

        return {
            "route_id": route_id,
            "error": f"Route '{route_id}' not found in the Arborian Network.",
        }
