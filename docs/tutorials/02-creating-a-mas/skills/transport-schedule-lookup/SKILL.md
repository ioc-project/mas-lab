---
name: transport-schedule-lookup
description: >
  Use when looking up departure schedules or attraction opening hours in the
  Arborian Network dataset (schedule_agent). Call
  `activate_skill("transport-schedule-lookup")` and follow the loaded
  instructions before answering.
metadata:
  version: "1.0.0"
  domain: travel
  tags: [schedule, lookup, transport]
---
<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Transport Schedule Lookup Protocol

## Input
A `ScheduleQuery` with:
- `origin` — departure city (must match a city in the Arborian Network)
- `destination` — arrival city
- `travel_mode` — `train`, `air`, or `any`
- `departure_date` — ISO date or weekday descriptor (`weekday` / `weekend`)

## Step 1 — Identify the Route
Always call `lookup_schedule` with the origin, destination, and travel_mode.
If `found` is false or `error` is `unknown_city`, report that and stop — do
not invent schedules or attractions. If no direct route exists between two
known cities, check for connections via an intermediate city.

## Step 2 — Retrieve Departures
Extract:
- Matching departure times for the travel_mode and departure_date.
- Travel time and frequency.
- Available service classes (Express, Standard, NightTrain, Economy, Business).

## Step 3 — Attraction Hours (Optional)
If the query includes `include_attractions: true`, look up opening hours and
entry fees for the top highlights of the destination city.

## Step 4 — Rate Limit
You may perform **at most 8 `lookup_schedule` calls per turn**.
If the limit is reached, return the best data found so far with `rate_limit_reached: true`.
Never invent departure times or fares.

## Output: `ScheduleResult`
```json
{
  "type": "ScheduleResult",
  "origin": "...",
  "destination": "...",
  "route_id": "...",
  "mode": "train|air",
  "travel_time": "...",
  "frequency": "...",
  "departures": ["..."],
  "attractions": [{"city": "...", "name": "...", "opening_hours": "...", "entry_fee_usd": 0.0}],
  "rate_limit_reached": false,
  "lookups_used": 0
}
```
