#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Publish run lifecycle records onto the telemetry channel."""

from __future__ import annotations

import logging
from typing import Any

from mas.lab.benchmark.pipeline.channel import get_channel, get_stream_id, set_stream_id
from mas.lab.benchmark.pipeline.counters import counters
from mas.lab.benchmark.pipeline.keys import RunKey

logger = logging.getLogger(__name__)

_TERMINAL = frozenset({"ok", "cached", "failed", "skipped"})


def publish_lifecycle(kind: str, key: RunKey, **extra: Any) -> None:
    channel = get_channel()
    if channel is None:
        return
    record = {
        "kind": kind,
        "experiment": key.experiment,
        "scenario": key.scenario,
        "item": key.item,
        "run": key.run,
        "attempt": key.attempt,
        "status": extra.pop("status", None),
    }
    record.update({k: v for k, v in extra.items() if v not in (None, "")})
    record = {k: v for k, v in record.items() if v is not None}
    try:
        channel.publish_sync(key.stream_id(), record)
        counters().bump(kind)
    except Exception:
        logger.debug("lifecycle publish failed", exc_info=True)


def begin_run(key: RunKey, **extra: Any):
    token = set_stream_id(key.stream_id())
    publish_lifecycle("run.started", key, **extra)
    return token


def attempt_reset(key: RunKey, **extra: Any) -> None:
    publish_lifecycle("run.attempt_reset", key, **extra)


def terminal_run(key: RunKey, status: str, **extra: Any) -> None:
    status = status if status in _TERMINAL else ("failed" if status == "error" else status)
    if status == "error":
        status = "failed"
    publish_lifecycle("run.terminal", key, status=status, **extra)
    channel = get_channel()
    if channel is None:
        return
    try:
        channel.seal_sync(key.stream_id())
    except Exception:
        logger.debug("seal failed", exc_info=True)


def current_run_stream() -> str | None:
    return get_stream_id()
