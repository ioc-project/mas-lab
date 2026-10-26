---
name: route-planning
description: >
  Use when planning graph-based routes in the Arborian Network for the
  Itinerary Agent (time, cost, or scenic value). Call
  `activate_skill("route-planning")` and follow the loaded instructions
  before answering.
metadata:
  version: "1.0.0"
  domain: travel
  tags: [route, graph, planning]
---
<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Route Planning Protocol

## Input
A `RouteRequest` with:
- `origin` — departure city
- `destination` — arrival city
- `optimise_for` — `"time"` | `"cost"` | `"scenic"`
- `max_hops` — maximum intermediate stops (default: 3)

## Step 1 — Direct Route Check
Always call `query_graph_database` before describing a route. If `found` is
false or `error` is `unknown_city`, report that and stop — do not invent
attractions, hops, or travel times.
If a direct route exists, record it as rank-1 option.

## Step 2 — Multi-Hop Enumeration
If no direct route, or if more options are desired, query for 1-hop and 2-hop
connections. Rank results by the `optimise_for` criterion:
- `time` → minimise total travel time across legs
- `cost` → minimise total fare (use Standard class as default)
- `scenic` → prefer routes through forest_belt, alpine, or coastal regions

## Step 3 — Rate Limit
You may call `query_graph_database` **at most 5 times per turn**.
If the limit is reached, return available options with `rate_limit_reached: true`.

## Step 4 — Scope Boundary
Do NOT look up fares (→ `concierge_agent` responsibility).
Do NOT look up departure times (→ `schedule_agent` responsibility).
Return route topology only: cities, modes, hop counts, and travel times.

## Output: `RouteOptions`
```json
{
  "type": "RouteOptions",
  "origin": "...",
  "destination": "...",
  "optimise_for": "time|cost|scenic",
  "routes": [
    {
      "rank": 1,
      "hops": [{"from": "...", "to": "...", "route_id": "...", "mode": "train|air", "travel_time": "..."}],
      "total_hops": 0,
      "total_travel_time": "...",
      "notes": "..."
    }
  ],
  "rate_limit_reached": false,
  "queries_used": 0
}
```
