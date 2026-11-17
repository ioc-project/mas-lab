#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Handler for the ``memory_call_*`` / ``memory_store_*`` / ``memory_retrieve_*`` (MemoryCall)
and ``rag_query_*`` (RAGQuery, a MemoryCall that retrieves) event kinds.

A memory call accesses one ``Memory`` store, named by the record: ``memory_name``, else
``collection``, else ``memory_type``. A record that names none adds no ``Memory`` and no
``executesMemory`` edge (nothing is invented). The operation is the one the kind says
(``memory_store`` -> ``store``, ``memory_retrieve`` and ``rag_query`` -> ``retrieve``),
or the record's own ``operation``. Its States come from ``state_in`` / ``state_out``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List

if TYPE_CHECKING:
    from mas.library.kg.core.native_graph_builder import NativeGraphBuilder

_OPERATION_OF_KIND = {
    "memory_store": "store",
    "memory_retrieve": "retrieve",
    "rag_query": "retrieve",
}


def _first(event: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = event.get(key)
        if value not in (None, ""):
            return value
    return None


def handle_memory_call(
    event: Dict[str, Any], builder: "NativeGraphBuilder"
) -> List[Dict[str, Any]]:
    local_name = str(event.get("mas_class") or "MemoryCall")
    node = builder.call_partial(event, local_name)
    if node is None:
        return []
    base = str(node.get("kindBase") or "")
    operation = _OPERATION_OF_KIND.get(base) or event.get("operation")
    if operation:
        node["memoryOperation"] = str(operation)
    query = _first(event, "query", "query_text", "key")
    if query is not None:
        node["memoryQuery"] = str(query)
    count = _first(event, "result_count", "results_count", "retrieved_doc_count")
    if count is not None:
        node["resultCount"] = int(count)
    if local_name == "RAGQuery":
        top_k = _first(event, "top_k")
        if top_k is not None:
            node["topK"] = int(top_k)

    name = _first(event, "memory_name", "collection", "memory_type")
    if name is not None:
        node["name"] = str(name)  # named by the Memory it accesses
    builder.emit_call(event, node)
    if name is not None:
        extra = {"memoryType": str(event["memory_type"])} if event.get("memory_type") else {}
        memory = builder.catalog("Memory", str(name), **extra)
        builder.executes(node, "executesMemory", memory)
        agent_id = str(event.get("agent_id") or "")
        if agent_id:
            builder.add_edge(
                "usesMemory", builder.catalog("Agent", agent_id, node_id=agent_id), memory
            )
    return [node]
