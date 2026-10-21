<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Ontology extensions

Ontology fields are classification keys on `events.jsonl` records. A trace is the ordered event log of one run. This page is for pipeline authors who read those keys: bench pipelines (evaluation metrics, trace statistics, lifecycle figures) filter on them instead of scraping free text.

---

## Event payload extensions

Common classification keys in `events.jsonl` (see [trajectory-schema.md](trajectory-schema.md)):

| Key | Used by |
|-----|---------|
| `mealy_symbol` | Which symbol of the Mealy product the event belongs to (`LLM_CALL`, `TOOL_CALL`, `CONTEXT_ASSEMBLE`, `RUN_START`, ...) |
| `summand` | Which machine produced it (`model`, `tool`, `context`, `orchestrator`, ...) |
| `block`, `layer` | Coarse grouping (`execution`, `context`, `structural`; `execution`, `semantic`, `structure`) |
| `policy_name` | Governance decision attribution (on `governance_decision` events) |
| `call_id`, `parent_call_id`, `correlation_id` | Interval nesting and envelope correlation |
| `part_id`, `source`, `section_id` | Context provenance (on `context_part_contributed` events) |

Pipeline steps such as `extract_trace_stats` read these fields.

---

## Lab-specific vocabularies

Paper labs may define local enums inside the lab (for example lifecycle states used by a figure step).
Keep lab vocabularies **inside the lab**; do not extend the global event kinds without updating
`trajectory-schema.md` and the `Kind` constants in `lab/src/mas/lab/evaluation/events.py`.

---

## Related docs

- [trajectory-schema.md](trajectory-schema.md)
- [semantic-protocols.md](semantic-protocols.md)
- [context-segmentation.md](context-segmentation.md)
