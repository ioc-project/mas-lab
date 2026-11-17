#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Handler for ``infrastructure_info``: the ``Worker`` a process reports it runs on.

The record names its worker (``worker_id``), the process (``worker_pid``) and the
durable backend. The execution the record sits under (``parent_call_id``) ``runsOn`` that Worker; a
record that names no parent gives the Worker node alone.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List

from mas.library.kg.core.native_graph_builder import call_ref

if TYPE_CHECKING:
    from mas.library.kg.core.native_graph_builder import NativeGraphBuilder


def handle_worker(event: Dict[str, Any], builder: "NativeGraphBuilder") -> List[Dict[str, Any]]:
    worker_id = str(event.get("worker_id") or "")
    if not worker_id:
        return []
    extra: Dict[str, Any] = {}
    if event.get("worker_pid") not in (None, ""):
        extra["processId"] = str(event["worker_pid"])
    if event.get("durable_backend"):
        extra["durableBackend"] = str(event["durable_backend"])
    worker = builder.catalog("Worker", worker_id, node_id=f"worker:{worker_id}", **extra)
    run_id = str(event.get("run_id") or "")
    target = str(event.get("parent_call_id") or "")
    if target:
        builder.add_edge("runsOn", call_ref(run_id, target), worker)
    return []
