<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# qa-agent example traces and graphs

Same `qa-agent` run as `library-telemetry/examples/qa-agent/`
(native events + replayed OTel). This folder is the library-kg copy, matching
`examples/trip-planner/`.

| File | Source |
| --- | --- |
| `events.jsonl` | Native plugin |
| `otel_spans.jsonl` | Replay of `events.jsonl` (`observe_sdk`) |
| `kg-native.jsonld` | `run_normalize` on `events.jsonl` |
| `kg-otel.jsonld` | `run_normalize_otel` on `otel_spans.jsonl` |

Used by `library-kg/tests/kg/test_native_otel_norm_equivalence.py` and
`tests/tutorials/test_tutorial_09.py`.
