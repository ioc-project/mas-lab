#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Worker registry — thread-safe in-process store."""
from __future__ import annotations

import threading
from typing import Dict, List, Optional

from mas.lab.controller.worker_model import WorkerKind, WorkerRecord, WorkerStatus, new_worker_id


class WorkerRegistry:
    def __init__(self) -> None:
        self._workers: Dict[str, WorkerRecord] = {}
        self._lock = threading.Lock()

    def create(
        self,
        kind: WorkerKind,
        *,
        spec: Optional[dict] = None,
        command: str = "",
        endpoint: str = "",
        parent_id: Optional[str] = None,
        worker_id: Optional[str] = None,
    ) -> WorkerRecord:
        prefix = {"application": "app", "benchmark": "bench", "pipeline": "pipe"}.get(
            kind.value, "w"
        )
        record = WorkerRecord(
            id=worker_id or new_worker_id(prefix),
            kind=kind,
            spec=dict(spec or {}),
            command=command,
            endpoint=endpoint,
            parent_id=parent_id,
        )
        with self._lock:
            self._workers[record.id] = record
        return record

    def get(self, worker_id: str) -> Optional[WorkerRecord]:
        with self._lock:
            return self._workers.get(worker_id)

    def list_workers(
        self,
        *,
        kind: Optional[WorkerKind] = None,
        status: Optional[WorkerStatus] = None,
    ) -> List[WorkerRecord]:
        with self._lock:
            items = list(self._workers.values())
        if kind is not None:
            items = [w for w in items if w.kind == kind]
        if status is not None:
            items = [w for w in items if w.status == status]
        items.sort(key=lambda w: w.created_at, reverse=True)
        return items

    def update(self, worker_id: str, **fields: object) -> Optional[WorkerRecord]:
        with self._lock:
            record = self._workers.get(worker_id)
            if record is None:
                return None
            for key, value in fields.items():
                if hasattr(record, key):
                    setattr(record, key, value)
            return record

    def cancel(self, worker_id: str) -> bool:
        record = self.get(worker_id)
        if record is None:
            return False
        if record.status in (WorkerStatus.COMPLETED, WorkerStatus.FAILED, WorkerStatus.CANCELLED):
            return True
        record.status = WorkerStatus.CANCELLED
        record.finished_at = record.finished_at or __import__("time").time()
        return True

    def drop(self, worker_id: str) -> bool:
        with self._lock:
            return self._workers.pop(worker_id, None) is not None
