---
name: trip-orchestration
description: >
  Use when moderating a trip request: classify it, dispatch specialists, and
  assemble the itinerary. Call `activate_skill("trip-orchestration")` and follow
  the loaded instructions before answering.
metadata:
  version: "1.0.0"
  domain: travel
  tags: [orchestration, protocol, moderator]
---
<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Trip Orchestration Protocol

## Step 1 — Classify the Request
Examine the user's TripRequest and determine which specialists are needed:
- **Schedule query only** → delegate to `schedule_agent` exclusively.
- **Route planning only** → delegate to `itinerary_agent` exclusively.
- **Full itinerary** (default) → delegate to all three specialists in sequence:
  `schedule_agent` → `itinerary_agent` → `concierge_agent`.

## Step 2 — Build Typed Queries
Before delegating, construct the correct typed query for each specialist:
- `ScheduleQuery` → `schedule_agent` (origin, destination, travel_mode, departure_date)
- `RouteRequest` → `itinerary_agent` (origin, destination, optimise_for, max_hops)
- `FareQuery` → `concierge_agent` (route legs + travel_class from RouteOptions)

Never pass a raw user message to a specialist. Always translate to the typed schema.

## Step 3 — Collect Specialist Results
Wait for each specialist in turn:
1. `ScheduleResult` from `schedule_agent`
2. `RouteOptions` from `itinerary_agent`
3. `FareResult` from `concierge_agent`

If a specialist returns `rate_limit_reached: true`, use the partial result and
note the limitation in the final response.

If a specialist returns `found: false`, `error: unknown_city`, or no path,
say that in the final answer. Do not invent attractions, fares, a budget, or a
feasible itinerary when a lookup failed.

## Step 4 — Budget Gate
If `FareResult.total_cost_usd > 1200`:
- Set `budget_compliant: false`.
- Include the exact line: `⚠️ HUMAN APPROVAL REQUIRED: budget exceeds $1200`.
- Do NOT suppress or round down the cost figure.
- Do NOT proceed with itinerary assembly — surface the gate to the user.

## Step 5 — Assemble Final Itinerary
Combine `ScheduleResult`, `RouteOptions`, and `FareResult` into a readable natural-language reply:
- Origin → Destination
- Day-by-day breakdown (transport mode, activities, accommodation cost)
- Total duration and total cost in USD
- Budget compliance status
- Human-review note if budget gate triggered

Respond in plain readable text. Do NOT return raw JSON to the user.
Always ground your assembly in specialist outputs — never invent fares, schedules, or route details.
A failed route, unknown city, or missing fare must appear in the final answer.
