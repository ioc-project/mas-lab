#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Handler for the ``processing_call_*`` / ``workflow_transition_*`` event
kinds (ProcessingCall)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List

if TYPE_CHECKING:
    from mas.library.kg.core.native_graph_builder import NativeGraphBuilder


def render_assembled_messages(messages: Any) -> str:
    """Render a context-assembly event's own ``messages`` list to readable text.

    The runtime's ``context_assembly`` step (``boundary_handlers.py``'s
    ``_boundary_context_assembled``) carries the real assembled prompt in
    this field on both ``processing_call_start`` and ``_end`` -- it does
    not need a sibling LLMCall's prompt reconstructed by timestamp-matching
    (a prior, now-removed heuristic did that). Its own ``output`` field is
    a legacy placeholder ("assembled") that no consumer should read for
    content; this is the real data, read directly off this one event.
    """
    if not isinstance(messages, list):
        return ""
    lines: List[str] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "?").strip().lower()
        content = msg.get("content") or ""
        if isinstance(content, list):
            content = " ".join(
                str(part.get("text", "")) for part in content if isinstance(part, dict)
            )
        content = str(content).strip()
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)


def handle_processing_call(
    event: Dict[str, Any], builder: "NativeGraphBuilder"
) -> List[Dict[str, Any]]:
    node = builder.upsert_call(event, "ProcessingCall")
    if node is None:
        return []
    kind = event.get("kind", "")

    if kind.endswith("_start"):
        node.setdefault("processingName", event.get("processing_name") or node.get("kindBase", ""))
        node.setdefault("processingType", event.get("processing_type") or "")
        node.setdefault("inputContent", event.get("input") or event.get("payload") or "")
    else:
        rendered = render_assembled_messages(event.get("messages"))
        node["processingOutput"] = rendered or event.get("output") or ""

    return [node]
