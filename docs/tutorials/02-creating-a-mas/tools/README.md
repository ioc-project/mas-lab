<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# trip-planner tools

These tools are application tools, not framework tools.

They live with the trip-planner example because they encode Arborian Network
domain behavior.

Dataset policy:

- static example data can be referenced here with explicit `dataset_path`
- benchmark fixture selection belongs to the lab layer
- neither `mas-runtime` nor `mas-ctl` should coordinate fixtures through
  hidden sidecars or implicit files for this example

Files:

- `query_graph_database.py` — route topology and connection search
- `lookup_schedule.py` — departures, travel times, service classes, highlights
- `get_fares.py` — fare lookup by route and class
- `calc.py` — arithmetic helper
- `*.tool.yaml` — declarative tool contracts and constructor params
- `_scene.py` — dataset helper only; not a framework primitive