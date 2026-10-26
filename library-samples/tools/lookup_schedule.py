#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Trip-planner tool: lookup_schedule

Returns departure schedules and travel times for routes in the Arborian Network.
Data is loaded from the active dataset via ``_scene.py``.
"""

from __future__ import annotations

from typing import Any, Dict, List

from mas.runtime.contracts import ToolContract

from ._scene import (
    find_city,
    find_route,
    load_network,
    unknown_cities,
    unknown_city_result,
)


def _parse_highlight(highlight_item: Any) -> Dict[str, str] | None:
    """Parse a city highlight from string or legacy dict format."""
    if isinstance(highlight_item, str):
        text = highlight_item.strip()
        if not text:
            return None
        name = text.split("(", 1)[0].strip() if "(" in text else text
        admission = ""
        if "(" in text and ")" in text:
            admission = text[text.index("(") + 1 : text.rindex(")")].strip()
        return {"name": name, "admission": admission}
    if isinstance(highlight_item, dict):
        for attraction_name, details_list in highlight_item.items():
            admission = ""
            if isinstance(details_list, list):
                for detail in details_list:
                    if isinstance(detail, dict) and "admission" in detail:
                        admission = detail["admission"]
                        break
            return {"name": str(attraction_name), "admission": admission}
    return None


class LookupScheduleTool(ToolContract):
    """Retrieve departure schedule between two Arborian Network cities."""

    def __init__(self, dataset_path: str = "fixtures/arborian-network.yaml") -> None:
        self._dataset_path = dataset_path

    def list_tools(self, **_: Any) -> List[Dict[str, Any]]:
        return [
            {
                "name": "lookup_schedule",
                "description": (
                    "Look up departure schedules for a route in the Arborian Network. "
                    "Returns travel times, frequencies, departures, and available service classes."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "origin": {
                            "type": "string",
                            "description": "Departure city name.",
                        },
                        "destination": {
                            "type": "string",
                            "description": "Arrival city name.",
                        },
                        "travel_mode": {
                            "type": "string",
                            "enum": ["train", "airplane", "any"],
                            "description": "Transport mode filter (default: any).",
                        },
                        "departure_date": {
                            "type": "string",
                            "description": "Departure date: 'weekday', 'weekend', or ISO date.",
                        },
                    },
                    "required": ["origin", "destination"],
                },
            }
        ]

    def call_tool(self, tool_name: str, arguments: Dict[str, Any], **_: Any) -> Any:
        if tool_name != "lookup_schedule":
            return None

        origin = arguments.get("origin", "")
        destination = arguments.get("destination", "")
        travel_mode = arguments.get("travel_mode", "any")
        departure_date = arguments.get("departure_date", "weekday")

        network = load_network(self._dataset_path)
        unknown = unknown_cities(network, origin, destination)
        if unknown:
            return unknown_city_result(unknown, origin=origin, destination=destination)

        routes = find_route(network, origin, destination, travel_mode)

        if not routes:
            return {
                "found": False,
                "origin": origin,
                "destination": destination,
                "message": f"No direct route found between {origin} and {destination}."
                           f" Check city names or consider a connection route.",
            }

        results: list[Dict[str, Any]] = []
        for r in routes:
            date_key = "weekday"
            if isinstance(departure_date, str):
                if "weekend" in departure_date.lower():
                    date_key = "weekend"
                elif departure_date.lower() == "weekday":
                    date_key = "weekday"
                else:
                    # ISO date — infer weekday/weekend from weekday()
                    try:
                        import datetime
                        d = datetime.date.fromisoformat(departure_date)
                        date_key = "weekend" if d.weekday() >= 5 else "weekday"
                    except ValueError:
                        date_key = "weekday"

            # Determine directionality for sample departures
            direction_key = None
            r_from = r.get("from", "")
            if r_from.lower() == origin.lower():
                direction_key = f"sample_departures_from_{r_from}"
            else:
                dest_city = r.get("from", "")
                direction_key = f"sample_departures_from_{dest_city}"

            departures: list[Any] = []
            if direction_key and direction_key in r:
                dep_data = r[direction_key]
                if isinstance(dep_data, dict):
                    selected = dep_data.get(date_key, dep_data.get("daily", []))
                    departures = selected if isinstance(selected, list) else []
                elif isinstance(dep_data, list):
                    departures = dep_data

            service_classes = list(r.get("fares_usd", {}).keys()) or list(r.get("train_types", []))
            results.append({
                "route_id": r.get("id", ""),
                "from": r.get("from", ""),
                "to": r.get("to", ""),
                "mode": r.get("mode", ""),
                "travel_time": r.get("travel_time", ""),
                "frequency": r.get("frequency", ""),
                "service_classes": service_classes,
                "departures": departures,
            })

        # Include city highlights with admission info
        city = find_city(network, destination)
        attractions: list[Dict[str, Any]] = []
        if city:
            for highlight_item in city.get("highlights", []):
                parsed = _parse_highlight(highlight_item)
                if parsed:
                    attractions.append(parsed)

        return {
            "found": True,
            "origin": origin,
            "destination": destination,
            "routes": results,
            "destination_highlights": attractions,
        }
