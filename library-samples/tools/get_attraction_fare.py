#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Trip-planner tool: get_attraction_fare

Returns the admission information for a named attraction in a city in the
Arborian Network. Data is loaded from the active dataset via ``_scene.py``.
"""

from __future__ import annotations

from typing import Any, Dict, List

from mas.runtime.contracts import ToolContract
from ._scene import find_city, load_network


class GetAttractionFareTool(ToolContract):
    """Look up admission details for a city attraction."""

    def __init__(self, dataset_path: str = "fixtures/arborian-network.yaml") -> None:
        self._dataset_path = dataset_path

    def list_tools(self, **_: Any) -> List[Dict[str, Any]]:
        return [
            {
                "name": "get_attraction_fare",
                "description": (
                    "Return the admission details for a named attraction in a city. "
                    "Use this for attraction entry prices and hours."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "city": {
                            "type": "string",
                            "description": "City name in the Arborian Network.",
                        },
                        "attraction": {
                            "type": "string",
                            "description": "Exact attraction/highlight name in that city.",
                        },
                    },
                    "required": ["city", "attraction"],
                },
            }
        ]

    def call_tool(self, tool_name: str, arguments: Dict[str, Any], **_: Any) -> Any:
        if tool_name != "get_attraction_fare":
            return None

        city_name = arguments.get("city", "").strip()
        attraction_name = arguments.get("attraction", "").strip()

        if not city_name:
            return {"found": False, "message": "City name is required."}
        if not attraction_name:
            return {"found": False, "city": city_name, "message": "Attraction name is required."}

        network = load_network(self._dataset_path)
        city = find_city(network, city_name)

        if not city:
            return {
                "found": False,
                "city": city_name,
                "message": f"City '{city_name}' not found in Arborian Network.",
            }

        for highlight_item in city.get("highlights", []):
            for candidate_name, details_list in highlight_item.items():
                if candidate_name.casefold() != attraction_name.casefold():
                    continue

                admission = ""
                if isinstance(details_list, list):
                    for detail in details_list:
                        if isinstance(detail, dict) and "admission" in detail:
                            admission = detail["admission"]
                            break

                return {
                    "found": True,
                    "city": city_name,
                    "region": city.get("region", ""),
                    "attraction": candidate_name,
                    "admission": admission,
                }

        available_attractions = []
        for highlight_item in city.get("highlights", []):
            available_attractions.extend(highlight_item.keys())

        return {
            "found": False,
            "city": city_name,
            "attraction": attraction_name,
            "message": f"Attraction '{attraction_name}' not found in {city_name}.",
            "available_attractions": available_attractions,
        }