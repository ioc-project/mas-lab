#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Handler for the ``processing_call_*`` / ``workflow_transition_*`` event kinds
(ProcessingCall).

Context assembly, the ``delegate`` / ``reply`` agent-boundary steps and the
``aggregate`` join of a parallel group are all ProcessingCalls. Their States come from
the record's own ``state_in`` / ``state_out`` (a join lists several input ids).

The timing brackets of a check (``governance_authorize_*``, ``governance_validate_*``,
``obs_wrap_gov_*``, ``checkpoint_*``) are ProcessingCalls too: the name is the kind without
its suffix, verbatim, and so is the ``processingType``. They are contained by the call the
record names as its parent (the agent visit they run in; ``checked_call_id`` names the call
they check). A record that states a decision also gives a GovernanceEvent that ``annotates``
the bracket; one that states none gives none. States come from the record's ``state_in`` /
``state_out`` (the State the check reads and lets through).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List

from mas.library.kg.core.event_mappings import CHECK_INTERVALS
from mas.library.kg.observability.native.handlers.annotation import (
    annotation_node,
    stated_decision,
)

if TYPE_CHECKING:
    from mas.library.kg.core.native_graph_builder import NativeGraphBuilder


def render_assembled_messages(messages: Any) -> str:
    """Render a context-assembly event's own ``messages`` list to readable text.

    The runtime's ``context_assembly`` step carries the real assembled prompt in this
    field on both ``processing_call_start`` and ``_end``. Its own ``output`` field is a
    legacy placeholder ("assembled") that no consumer should read for content.
    """
    if not isinstance(messages, list):
        return ""
    lines: List[str] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "?").strip().lower()
        from mas.runtime.contracts.content_types import text_of

        content = text_of(msg.get("content"), media="placeholder").strip()
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)


def handle_processing_call(
    event: Dict[str, Any], builder: "NativeGraphBuilder"
) -> List[Dict[str, Any]]:
    node = builder.call_partial(event, "ProcessingCall")
    if node is None:
        return []
    kind = event.get("kind", "")
    name = str(event.get("processing_name") or node.get("kindBase") or "")
    node["processingName"] = name
    check = node.get("kindBase") in CHECK_INTERVALS
    processing_type = str(event.get("processing_type") or (name if check else ""))

    if kind.endswith("_start"):
        node["processingType"] = processing_type
        node["inputContent"] = event.get("input") or event.get("payload") or ""
    else:
        rendered = render_assembled_messages(event.get("messages"))
        node["processingOutput"] = rendered or event.get("output") or ""

    builder.emit_call(event, node)
    if check and kind.endswith("_end") and stated_decision(event):
        if builder.class_enabled("GovernanceEvent"):
            point = annotation_node(event, "GovernanceEvent", builder)
            builder.add_edge("annotates", point["id"], node["id"])
    if name:
        extra = {"processingType": processing_type} if processing_type else {}
        builder.executes(node, "executesProcessing", builder.catalog("Processing", name, **extra))
    return [node]
