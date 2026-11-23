#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""ListClickhouseSessionsStep — list session IDs from ClickHouse ``sessions_mapping``.

Queries the ``sessions_mapping`` table filtered by ``dataset_id`` and returns
the distinct ``session_id`` values.  The connection (host, port, user, database)
comes from the user connections or the ``CLICKHOUSE_*`` host values, and the
password from the secret store when the query is sent through
:func:`mas.library.telemetry.clickhouse_http.query`, like the other mas-lab
ClickHouse commands.

Configuration
-------------
dataset_id   str   Required.  ClickHouse dataset UUID.
                   Example: ``"5f316b2b-8840-47d5-9174-d902eecd51cb"``
limit        int   Max rows to return.  0 = no limit (default).

Step output
-----------
data:
  session_ids  list[str]   Ordered list of session IDs.
  count        int         Length of session_ids.
  dataset_id   str         The dataset_id used.
files: (none)
"""

from __future__ import annotations

import json
import logging
from typing import List, Optional

from mas.lab.benchmark.pipeline import PipelineStep, StepOutput
from mas.lab.benchmark.pipeline.executor import ExecutionContext

logger = logging.getLogger(__name__)


class ListClickhouseSessionsStep(PipelineStep):
    """List session IDs from ClickHouse ``sessions_mapping`` by ``dataset_id``."""

    type = "list_clickhouse_sessions"

    async def execute(self, ctx: ExecutionContext) -> StepOutput:
        config = self.config

        dataset_id: Optional[str] = config.get("dataset_id") or None
        limit: int = int(config.get("limit", 0))

        if not dataset_id:
            raise ValueError(f"ListClickhouseSessionsStep '{self.name}': 'dataset_id' is required.")

        limit_fragment = f"LIMIT {limit}" if limit > 0 else ""
        query = (
            f"SELECT DISTINCT session_id FROM sessions_mapping "
            f"WHERE dataset_id = '{dataset_id}' "
            f"ORDER BY session_id "
            f"{limit_fragment} "
            f"FORMAT JSONEachRow"
        ).strip()

        logger.info("ListClickhouseSessions: dataset_id=%s  query=%s", dataset_id, query)

        if ctx.dry_run:
            logger.info("[dry-run] Would query ClickHouse; returning empty list.")
            return StepOutput(
                data={"session_ids": [], "count": 0, "dataset_id": dataset_id},
                files=[],
                metadata={"dry_run": True, "query": query},
            )

        from mas.lab.connections import resolve_clickhouse_conn, resolve_clickhouse_password
        from mas.library.telemetry import clickhouse_http

        conn = resolve_clickhouse_conn()
        raw = clickhouse_http.query(conn, query, password=resolve_clickhouse_password())

        session_ids: List[str] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                sid = row.get("session_id")
                if sid:
                    session_ids.append(str(sid))
            except json.JSONDecodeError:
                logger.warning("ListClickhouseSessions: unparseable line: %r", line)

        logger.info(
            "ListClickhouseSessions: found %d sessions for dataset_id=%s",
            len(session_ids),
            dataset_id,
        )

        return StepOutput(
            data={"session_ids": session_ids, "count": len(session_ids), "dataset_id": dataset_id},
            files=[],
            metadata={"query": query},
        )
