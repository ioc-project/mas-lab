#!/usr/bin/env python3
"""Regenerate library-kg/schemas/events.schema.json from KIND_TO_CLASS."""

from __future__ import annotations

import json
from pathlib import Path

from mas.library.kg.core.event_mappings import KIND_TO_CLASS

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "schemas" / "events.schema.json"

KNOWN_KINDS = sorted(k for k, cls in KIND_TO_CLASS.items() if cls is not None)
LEGACY_KINDS = sorted(k for k, cls in KIND_TO_CLASS.items() if cls is None)
ALL_KINDS = sorted(KIND_TO_CLASS.keys())

schema = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "mas-lab/events/v1",
    "title": "MAS native observability event",
    "description": (
        "One line of events.jsonl. Envelope (L1) + kind enum from KIND_TO_CLASS. "
        "Per-kind payloads: events.spec.yaml + EventValidator."
    ),
    "type": "object",
    "required": ["kind", "timestamp", "run_id", "agent_id"],
    "properties": {
        "kind": {
            "type": "string",
            "enum": ALL_KINDS,
            "description": "Event type; maps to mas_class via native normalizer.",
        },
        "timestamp": {"type": "number", "description": "Unix epoch seconds"},
        "run_id": {"type": "string"},
        "agent_id": {"type": "string"},
        "mas_id": {"type": "string"},
        "call_id": {
            "type": "string",
            "description": "Stable id for interval kinds; mirrors mas.call.id on OTel export",
        },
        "parent_call_id": {"type": ["string", "null"]},
        "correlation_id": {"type": ["string", "number"]},
        "span_id": {
            "type": "string",
            "description": "OTel interop only; not a graph join key",
        },
        "session_id": {"type": "string"},
        "block": {
            "type": "string",
            "enum": ["structural", "execution", "context", "trajectory", "governance"],
        },
        "summand": {
            "type": "string",
            "enum": ["model", "tool", "context", "governance", "orchestrator"],
        },
        "mealy_symbol": {"type": "string"},
        "layer": {
            "type": "string",
            "enum": ["structure", "execution", "semantic", "provenance", "governance"],
        },
        "input": {"type": "string"},
        "output": {"type": "string"},
        "status": {"type": "string"},
        "context": {"type": "object"},
        "model": {"type": "string"},
        "messages": {"type": "array"},
        "temperature": {"type": ["number", "null"]},
        "max_tokens": {"type": ["integer", "null"]},
        "latency_ms": {"type": "number"},
        "response": {"type": "object"},
        "finish_reason": {"type": ["string", "null"]},
        "tokens_used": {"type": ["integer", "null"]},
        "tool_name": {"type": "string"},
        "arguments": {"type": ["object", "null"]},
        "result": {},
        "segments": {"type": "object"},
        "total_tokens": {"type": "integer"},
        "part_id": {"type": "string"},
        "source": {"type": "string"},
        "content": {"type": "string"},
        "access_mechanism": {"type": "string"},
        "cause": {"type": "string"},
        "retained": {"type": "boolean"},
        "parents": {"type": "array"},
        "target_agent_id": {"type": "string"},
        "source_agent_id": {"type": "string"},
        "task": {"type": "string"},
        "message_type": {"type": "string"},
        "worker_id": {"type": "string"},
        "endpoint": {"type": "string"},
        "spec_id": {"type": "string"},
        "spec_version": {"type": "string"},
        "catalog": {"type": "object"},
    },
    "allOf": [
        {
            "if": {
                "properties": {"kind": {"pattern": "_(start|end)$"}},
                "required": ["kind"],
            },
            "then": {"required": ["call_id"]},
        },
        {
            "if": {
                "properties": {"kind": {"enum": KNOWN_KINDS}},
                "required": ["kind"],
            },
            "then": {
                "not": {"required": ["__invalid__"]},
            },
        },
    ],
    "additionalProperties": True,
}

OUT.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
print(f"wrote {OUT} ({len(ALL_KINDS)} kinds, {len(LEGACY_KINDS)} legacy)")
