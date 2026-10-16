#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Trip-planner tool: get_attractions

Returns a list of attraction names for a city in the Arborian Network.
Data is loaded from the active dataset via ``_scene.py``.
"""

from __future__ import annotations

from typing import Any, Dict, List

from mas.runtime.contracts import ToolContract
from ._scene import load_network, find_city


class GetAttractionsTool(ToolContract):
    """Retrieve attraction names for a city in the Arborian Network."""

    def __init__(self, dataset_path: str = "fixtures/arborian-network.yaml") -> None:
        self._dataset_path = dataset_path

    def list_tools(self, **_: Any) -> List[Dict[str, Any]]:
        return [
            {
                "name": "get_attractions",
                "description": (
                    "Get a list of attraction names and highlights in a city."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "city": {
                            "type": "string",
                            "description": "City name in the Arborian Network.",
                        },
                    },
                    "required": ["city"],
                },
            }
        ]

    def call_tool(self, tool_name: str, arguments: Dict[str, Any], **_: Any) -> Any:
        if tool_name != "get_attractions":
            return None

        city_name = arguments.get("city", "").strip()

        if not city_name:
            return {
                "found": False,
                "message": "City name is required.",
            }

        network = load_network(self._dataset_path)
        city = find_city(network, city_name)

        if not city:
            return {
                "found": False,
                "city": city_name,
                "message": f"City '{city_name}' not found in Arborian Network.",
            }

        # Extract only attraction names
        attraction_names = []
        for highlight_item in city.get("highlights", []):
            # Each highlight is a dict: {"Attraction Name": [{"admission": "...", "additional_info": "..."}]}
            for attraction_name in highlight_item.keys():
                attraction_names.append(attraction_name)

        return {
            "found": True,
            "city": city_name,
            "region": city.get("region", ""),
            "attractions_count": len(attraction_names),
            "attractions": attraction_names,
            "typical_day_cost_usd": city.get("typical_day_cost_usd", 0),
        }
