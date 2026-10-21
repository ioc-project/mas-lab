<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# qa-agent example traces

Captured from Tutorial 1's `qa-agent` (`What is 15 * 23?` → `15 * 23 equals 345.`).
Same layout as `examples/trip-planner/`: reusable native + OTel JSON for
conversion, replay, and Tutorial 8.

| File | Source |
| --- | --- |
| `events.jsonl` | Native observability plugin |
| `otel_sdk_spans_live.jsonl` | Live OTel plugin (JSON file sink) |
| `otel_sdk_spans_replay.jsonl` | `convert_events_to_spans_file` on `events.jsonl` |

Used by `tests/telemetry/plugins/test_live_plugin_vs_replay.py` and
`tests/tutorials/test_tutorial_08.py`.
