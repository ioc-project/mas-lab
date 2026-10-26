#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Trip-planner tool: query_graph_database

Enumerates direct and multi-hop paths in the Arborian Network graph.
Returns route topology only — no fares, no departure times.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Dict, List, Optional

from ._scene import find_city, load_network, unknown_cities, unknown_city_result

_SCENIC_REGIONS = {"forest_belt", "alpine", "coastal"}


def _build_adjacency(routes: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Return adjacency dict: city → list of {to, route_id, mode, travel_time}."""
    adj: Dict[str, List[Dict[str, Any]]] = {}
    for r in routes:
        frm = r.get("from", "")
        to = r.get("to", "")
        entry = {
            "to": to,
            "route_id": r.get("id", ""),
            "mode": r.get("mode", ""),
            "travel_time": r.get("travel_time", ""),
            "from": frm,
        }
        adj.setdefault(frm, []).append(entry)
        # bidirectional
        rev = dict(entry)
        rev["from"], rev["to"] = to, frm
        adj.setdefault(to, []).append(rev)
    return adj


def _parse_minutes(travel_time: str) -> int:
    """Parse '3h', '1h 30m', '55m' → total minutes."""
    import re
    h = re.search(r"(\d+)\s*h", travel_time)
    m = re.search(r"(\d+)\s*m", travel_time)
    return int(h.group(1)) * 60 + (int(m.group(1)) if m else 0) if h else (int(m.group(1)) if m else 999)


def _region_of(network: Dict[str, Any], city: str) -> Optional[str]:
    for c in network.get("cities", []):
        if c.get("name", "").lower() == city.lower():
            return c.get("region", "")
    return None


class QueryGraphDatabaseTool:
    """Enumerate routes in the Arborian Network graph."""

    def __init__(self, dataset_path: str = "datasets/arborian-network.yaml") -> None:
        self._dataset_path = dataset_path

    def on_collect_tools(self, **_: Any) -> List[Dict[str, Any]]:
        return [
            {
                "name": "query_graph_database",
                "description": (
                    "Enumerate feasible paths between two cities in the Arborian Network. "
                    "Returns route topology (cities, modes, hop count, travel times). "
                    "Does NOT return fares or departure times."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "origin": {"type": "string", "description": "Departure city name."},
                        "destination": {"type": "string", "description": "Arrival city name."},
                        "optimise_for": {
                            "type": "string",
                            "enum": ["time", "cost", "scenic"],
                            "description": "Ranking criterion (default: time).",
                        },
                        "max_hops": {
                            "type": "integer",
                            "description": "Maximum intermediate stops (default: 3).",
                        },
                    },
                    "required": ["origin", "destination"],
                },
            }
        ]

    def on_execute_tool(self, tool_name: str, arguments: Dict[str, Any], **_: Any) -> Any:
        if tool_name != "query_graph_database":
            return None

        origin = arguments.get("origin", "")
        destination = arguments.get("destination", "")
        optimise_for = arguments.get("optimise_for", "time")
        max_hops = min(int(arguments.get("max_hops", 3)), 3)

        network = load_network(self._dataset_path)
        unknown = unknown_cities(network, origin, destination)
        if unknown:
            return unknown_city_result(unknown, origin=origin, destination=destination)

        origin_name = (find_city(network, origin) or {}).get("name") or origin
        dest_name = (find_city(network, destination) or {}).get("name") or destination
        adj = _build_adjacency(network.get("routes", []))

        # BFS up to max_hops to find all paths. City existence is checked
        # above; origin == destination for a known city is a zero-hop route.
        paths: List[List[Dict[str, Any]]] = []
        queue: deque[tuple[str, List[Dict[str, Any]]]] = deque([(origin_name, [])])
        while queue:
            current, path = queue.popleft()
            if current.lower() == dest_name.lower():
                paths.append(path)
                continue
            if len(path) >= max_hops:
                continue
            for edge in adj.get(current, []):
                next_city = edge["to"]
                # avoid cycles within this path
                path_cities = {p["from"] for p in path} | ({path[-1]["to"]} if path else {origin_name})
                if next_city in path_cities:
                    continue
                queue.append((next_city, path + [edge]))

        if not paths:
            return {
                "found": False,
                "origin": origin,
                "destination": destination,
                "message": f"No path found between {origin} and {destination} within {max_hops} hops.",
            }

        # Rank paths
        def sort_key(p: List[Dict[str, Any]]) -> float:
            if optimise_for == "time":
                return sum(_parse_minutes(e.get("travel_time", "999m")) for e in p)
            elif optimise_for == "cost":
                return len(p)  # proxy: fewer hops = lower cost
            else:  # scenic
                scenic = sum(
                    1 for e in p
                    if _region_of(network, e["to"]) in _SCENIC_REGIONS
                )
                return -scenic  # higher scenic score ranks first

        paths.sort(key=sort_key)
        routes_out: list[Dict[str, Any]] = []
        for i, p in enumerate(paths[:3], 1):
            total_min = sum(_parse_minutes(e.get("travel_time", "0m")) for e in p)
            h, m = divmod(total_min, 60)
            routes_out.append({
                "rank": i,
                "hops": [{"from": e["from"], "to": e["to"], "route_id": e["route_id"],
                           "mode": e["mode"], "travel_time": e["travel_time"]} for e in p],
                "total_hops": len(p),
                "total_travel_time": f"{h}h {m}m" if h else f"{m}m",
                "notes": f"Optimised for {optimise_for}.",
            })

        return {
            "found": True,
            "origin": origin,
            "destination": destination,
            "optimise_for": optimise_for,
            "routes": routes_out,
        }
