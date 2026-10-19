#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Discrete-event replay clock.

All sources are preloaded, merged by timestamp, then emitted on one clock.
Lookahead is the whole timeline (not a streaming parse of several files).
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(order=True)
class Scheduled:
    ts: float
    seq: int
    tree: str = field(compare=False, default="runtime")
    ref: Any = field(compare=False, default=None)
    record: dict[str, Any] = field(compare=False, default_factory=dict)
    envelope: dict[str, Any] | None = field(compare=False, default=None)


def event_ts_s(record: dict[str, Any] | None) -> float | None:
    if not isinstance(record, dict):
        return None
    raw = record.get("timestamp", record.get("ts", record.get("t")))
    if isinstance(raw, (int, float)):
        value = float(raw)
        return value / 1000.0 if value > 1e12 else value
    if isinstance(raw, str) and raw.strip():
        try:
            return float(raw)
        except ValueError:
            from datetime import datetime

            try:
                return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
            except ValueError:
                return None
    return None


def envelope_ts_s(row: dict[str, Any]) -> float | None:
    ts = event_ts_s(row)
    if ts is not None:
        return ts
    payload = row.get("payload")
    if isinstance(payload, dict):
        return event_ts_s(payload)
    return None


def merge_timeline(lanes: list[list[Scheduled]]) -> list[Scheduled]:
    """Stable merge of preloaded lanes ordered by (ts, seq)."""
    merged: list[Scheduled] = []
    seq = 0
    for lane in lanes:
        for item in lane:
            merged.append(
                Scheduled(
                    ts=item.ts,
                    seq=seq,
                    tree=item.tree,
                    ref=item.ref,
                    record=item.record,
                    envelope=item.envelope,
                )
            )
            seq += 1
    merged.sort()
    return merged


def _coherent_fallback(times: list[float | None]) -> float:
    """Prefer unix event time over epoch-0 placeholders mixed into the same file."""
    unix = [t for t in times if t is not None and t > 1e9]
    if unix:
        return unix[0]
    relative = [t for t in times if t is not None and t != 0.0]
    return relative[0] if relative else 0.0


def _fill_ts(raw: float | None, fallback: float, *, unix: bool) -> float:
    if raw is None:
        return fallback
    if unix and raw < 1e9:
        return fallback
    return raw


def records_to_scheduled(
    records: list[dict[str, Any]],
    *,
    ref: Any = None,
    tree: str = "runtime",
) -> list[Scheduled]:
    times = [event_ts_s(record) for record in records]
    unix = any(t is not None and t > 1e9 for t in times)
    fallback = _coherent_fallback(times)
    out: list[Scheduled] = []
    for i, record in enumerate(records):
        ts = _fill_ts(times[i], fallback, unix=unix)
        fallback = ts
        out.append(Scheduled(ts=ts, seq=i, tree=tree, ref=ref, record=record))
    return out


def envelopes_to_scheduled(
    rows: list[dict[str, Any]],
    *,
    tree: str,
) -> list[Scheduled]:
    times = [envelope_ts_s(row) if isinstance(row, dict) else None for row in rows]
    unix = any(t is not None and t > 1e9 for t in times)
    fallback = _coherent_fallback(times)
    out: list[Scheduled] = []
    for i, row in enumerate(rows):
        ts = _fill_ts(times[i], fallback, unix=unix)
        fallback = ts
        out.append(Scheduled(ts=ts, seq=i, tree=tree, envelope=row if isinstance(row, dict) else {}))
    return out


async def pump_timeline(
    items: list[Scheduled],
    *,
    speed: float,
    emit: Callable[[Scheduled], Awaitable[None] | None],
    cancelled: Callable[[], bool] | None = None,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    """Emit preloaded events on a shared clock. ``speed`` 0 is instant, 1 realtime."""
    if not items:
        return
    t0 = items[0].ts
    t1 = items[-1].ts
    n_total = len(items)
    tree_totals = Counter(item.tree for item in items)
    tree_done: Counter[str] = Counter()
    clock = asyncio.get_running_loop()
    wall0 = clock.time()
    prev_ts = t0
    jump = 3600.0
    for i, item in enumerate(items, 1):
        if cancelled and cancelled():
            return
        if speed > 0:
            gap = item.ts - prev_ts
            clock_break = gap > jump or gap < -1e-9 or (item.ts > 1e9 and prev_ts < 1e7)
            if clock_break:
                t0 = item.ts - (clock.time() - wall0) * speed
                delay = 0.0
            else:
                delay = (item.ts - t0) / speed - (clock.time() - wall0)
            if delay > 0:
                await asyncio.sleep(delay)
        prev_ts = item.ts
        result = emit(item)
        if asyncio.iscoroutine(result):
            await result
        await asyncio.sleep(0)
        tree_done[item.tree] += 1
        if on_progress is not None:
            on_progress(
                {
                    "t": item.ts,
                    "t0": t0,
                    "t1": t1,
                    "n": i,
                    "n_total": n_total,
                    "tree": item.tree,
                    "speed": speed,
                    "trees": {
                        name: {"n": tree_done[name], "n_total": tree_totals[name]}
                        for name in tree_totals
                    },
                }
            )
