#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Publish live-run records onto the installed pipeline channel."""

from __future__ import annotations

from typing import Any

from mas.lab.benchmark.pipeline.channel import get_channel, get_stream_id


class LiveChannelSink:
    """Per-record emit onto the current run stream. Downstream DAG nodes fold."""

    def emit(self, record: dict[str, Any]) -> None:
        ch = get_channel()
        sid = get_stream_id()
        if ch is None or not sid:
            return
        ch.publish_sync(sid, record)
