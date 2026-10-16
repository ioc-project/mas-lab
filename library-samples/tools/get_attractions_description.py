#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Trip-planner tool: get_attractions

Returns a detailed list of attractions/highlights for a city in the Arborian Network.
Data is loaded from the active dataset via ``_scene.py``.
"""

from __future__ import annotations

from typing import Any, Dict, List

from mas.runtime.contracts import ToolContract
from ._scene import load_network, find_city


class GetAttractionsDescriptionTool(ToolContract):
    """Retrieve attractions/highlights descriptions for a city in the Arborian Network."""

    def __init__(self, dataset_path: str = "fixtures/arborian-network.yaml") -> None:
        self._dataset_path = dataset_path

    def list_tools(self, **_: Any) -> List[Dict[str, Any]]:
        return [
            {
                "name": "get_attractions_description",
                "description": (
                    "Get a detailed list of attractions and highlights in a city with accessibility information."
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
        if tool_name != "get_attractions_description":
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

        # Extract attractions with accessibility info
        attractions = []
        for highlight_item in city.get("highlights", []):
            # Each highlight is a dict: {"Attraction Name": [{"admission": "...", "additional_info": "..."}]}
            for attraction_name, details_list in highlight_item.items():
                # Extract additional_info (accessibility) from the details list
                accessibility = ""
                admission = ''
                if isinstance(details_list, list):
                    for detail in details_list:
                        if isinstance(detail, dict) and "additional_info" in detail:
                            accessibility = detail["additional_info"]
                            admission = detail.get("admission", '')
                            break
                attractions.append({
                    "name": attraction_name,
                    "accessibility": accessibility,
                    "admission": admission,
                })

        return {
            "found": True,
            "city": city_name,
            "region": city.get("region", ""),
            "attractions_count": len(attractions),
            "attractions": attractions,
            "typical_day_cost_usd": city.get("typical_day_cost_usd", 0),
        }
