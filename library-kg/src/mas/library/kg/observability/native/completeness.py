#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Trace-vs-KG completeness check for the native events.jsonl path.

Native counterpart of an OTel completeness check: every native event must
be traceable to a KG node, or its ``kind`` must be a documented no-op
(``KIND_TO_CLASS[kind] is None``).

- Interval events (``kind`` matches ``_(start|end)$``) carry ``call_id``;
  coverage means some node's ``callId`` matches.
- Point-in-time / annotation events (routing, context contributions,
  governance, ...) carry no ``call_id``; their node identity is instead a
  deterministic hash of (run_id, agent_id, kind, timestamp, span_id) --
  see core.graph_builder._annotation_id. Coverage is recomputing that same
  id and checking a node exists with it.
- A ``kind`` that maps to ``None`` in KIND_TO_CLASS (or isn't in
  KIND_TO_CLASS at all) is legitimately covered by definition (deliberately
  suppressed / no ontology class) -- UNLESS it isn't in KIND_TO_CLASS at
  all, in which case it's a genuinely unknown kind that strict=False
  processing would have silently dropped, and that IS a real gap.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from mas.library.kg.core.event_mappings import KIND_TO_CLASS, TRANSPORT_EVENT_KINDS
from mas.library.kg.core.graph_builder import _annotation_id
from mas.library.kg.core.native_graph_builder import catalog_node_id

CheckResult = Tuple[bool, List[Dict[str, Any]]]


def check_native_trace_completeness(
    events: List[Dict[str, Any]],
    nodes: List[Dict[str, Any]],
    run_id: str,
) -> CheckResult:
    """Every native event must be traceable to a KG node, or its ``kind``
    must be a documented, deliberate no-op (``KIND_TO_CLASS[kind] is None``).

    Returns ``(True, [])`` when every event is accounted for, else
    ``(False, violations)`` with one entry per unaccounted event
    (``kind``, ``call_id``, ``timestamp``).
    """
    call_ids_present: set[str] = {str(n.get("callId")) for n in nodes if n.get("callId")}
    node_ids_present: set[str] = {str(n.get("id")) for n in nodes if n.get("id")}

    violations: List[Dict[str, Any]] = []
    for ev in events:
        kind = str(ev.get("kind") or "")
        if kind not in KIND_TO_CLASS:
            # Genuinely unknown kind -- strict=False processing drops this
            # silently. A real gap unless some node happens to reference it
            # anyway (defensive; shouldn't normally happen).
            pass
        elif KIND_TO_CLASS[kind] is None:
            continue  # deliberately suppressed by design (legacy_kinds)

        if kind in TRANSPORT_EVENT_KINDS:
            if str(ev.get("protocol") or "") == "mcp" and kind.startswith("transport.message"):
                continue
            attrs = ev.get("attributes") or {}
            if not isinstance(attrs, dict):
                attrs = {}
            candidates = [
                str(attrs.get("source") or ""),
                str(attrs.get("target") or ""),
                str(ev.get("agent_id") or ""),
            ]
            card = attrs.get("card") or {}
            if isinstance(card, dict):
                candidates.append(str(card.get("name") or ""))
            if any(c and c in node_ids_present for c in candidates):
                continue
            tools = attrs.get("tools")
            entries = tools.get("tools") if isinstance(tools, dict) else None
            if isinstance(entries, list) and any(
                catalog_node_id("Tool", str(item.get("name") or "")) in node_ids_present
                for item in entries
                if isinstance(item, dict) and item.get("name")
            ):
                continue

        # Try both identity schemes regardless of suffix shape: several
        # _start/_end-suffixed kinds (agent_communication_*, checkpoint_*)
        # are CallAnnotation-mapped and use the annotation-id scheme, not
        # call_id, even though they carry a call_id field (it identifies
        # the *enclosing* call, not this annotation's own identity).
        call_id = str(ev.get("call_id") or "")
        if call_id and call_id in call_ids_present:
            continue
        expected_id = f"ann-{_annotation_id(ev, run_id)}"
        if expected_id in node_ids_present:
            continue

        violations.append(
            {"kind": kind, "call_id": call_id or None, "timestamp": ev.get("timestamp")}
        )

    return len(violations) == 0, violations
