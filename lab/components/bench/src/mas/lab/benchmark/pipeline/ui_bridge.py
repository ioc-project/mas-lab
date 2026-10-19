#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Channel → demo-ui ingest subscriber. UI protocol (kind: metric) is unchanged."""

from __future__ import annotations

import logging
from collections import Counter
from contextvars import ContextVar, Token
from typing import Any

from mas.lab.benchmark.pipeline.channel import frame_at, get_channel, get_stream_id
from mas.lab.benchmark.pipeline.keys import RunKey
from mas.lab.benchmark.pipeline.kinds import is_control_kind, is_lifecycle_kind

logger = logging.getLogger(__name__)

_demo_ui_scope: ContextVar[dict[str, Any] | None] = ContextVar("mas_demo_ui_scope", default=None)


def push_demo_ui_scope(extra: dict[str, Any]) -> Token:
    current = dict(_demo_ui_scope.get() or {})
    current.update({key: value for key, value in extra.items() if value not in (None, "")})
    return _demo_ui_scope.set(current)


def pop_demo_ui_scope(token: Token) -> None:
    _demo_ui_scope.reset(token)


def current_demo_ui_scope() -> dict[str, Any]:
    return dict(_demo_ui_scope.get() or {})


def metric_envelope(
    metrics: dict[str, Any],
    *,
    name: str = "trace_stats",
    key: RunKey | None = None,
    partial: bool = False,
) -> dict[str, Any]:
    record: dict[str, Any] = {"kind": "metric", "name": name, "metrics": dict(metrics)}
    if partial:
        record["partial"] = True
    if key is not None:
        record["scenario"] = key.scenario
        record["test"] = key.item
        record["run"] = key.run
    return record


def structure_envelope(
    *,
    key: RunKey,
    offset: int,
    digest: str,
    kinds: dict[str, int],
    artefacts: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "kind": "control.structure",
        "stream_id": key.stream_id(),
        "offset": offset,
        "digest": digest,
        "kinds": dict(kinds),
        "artefacts": list(artefacts or []),
        "scenario": key.scenario,
        "test": key.item,
        "run": key.run,
        "experiment": key.experiment,
    }
    if extra:
        record.update({k: v for k, v in extra.items() if v not in (None, "")})
    return record


def scope_progress_envelope(
    *,
    scope: str,
    done: int,
    total: int,
    key: RunKey | None = None,
    partial: bool = True,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "kind": "scope.progress",
        "scope": scope,
        "done": done,
        "total": total,
        "partial": partial,
    }
    if key is not None:
        record["scenario"] = key.scenario
        record["test"] = key.item
        record["run"] = key.run
    return record


def post_ingest(record: dict[str, Any], url: str | None = None, ref: dict[str, Any] | None = None) -> None:
    """Subscriber adapter: channel records → demo-ui ``POST /api/ingest``."""
    import json
    import os
    import urllib.request

    dest = str(url or os.environ.get("MAS_DEMO_UI_INGEST_URL") or "").strip()
    if not dest:
        return
    extra_ref = {**current_demo_ui_scope(), **dict(ref or {})}
    raw_ref = os.environ.get("MAS_DEMO_UI_REF") or ""
    if raw_ref.strip():
        try:
            parsed = json.loads(raw_ref)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            extra_ref = {**parsed, **extra_ref}
    stamped = dict(record)
    for key, value in current_demo_ui_scope().items():
        stamped.setdefault(key, value)
    body = json.dumps({"ref": extra_ref, "record": stamped, "replay": False}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    token = str(os.environ.get("MAS_DEMO_UI_TOKEN") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(dest, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=0.4) as resp:
            resp.read()
    except Exception:
        return


def metric_from_snapshot(metrics: dict[str, Any], *, key: RunKey, channel=None) -> dict[str, Any]:
    """POST a metric tick to ingest. Never publish onto the native run stream (digest)."""
    del channel
    record = metric_envelope(metrics, key=key)
    post_ingest(record)
    return record


class IngestSubscriber:
    """Subscribe to the channel and forward native / metric / control / artefact records."""

    def __init__(self, url: str | None = None) -> None:
        self.url = url
        self._task = None
        self._kinds: Counter[str] = Counter()
        self._since_dump = 0

    def on_frame(self, frame, *, key: RunKey | None = None) -> None:
        body = frame.body if hasattr(frame, "body") else frame
        if not isinstance(body, dict):
            return
        kind = str(body.get("kind") or getattr(frame, "kind", "") or "")
        self._kinds[kind] += 1
        self._since_dump += 1
        post_ingest(body, url=self.url)
        if body.get("kind") == "metric":
            return
        rec = body.get("record") if isinstance(body.get("record"), dict) else None
        if rec and str(body.get("kind") or "").startswith("artifact."):
            post_ingest(metric_envelope(rec, key=key), url=self.url)
        if key is not None and (
            self._since_dump >= 8
            or kind.endswith("_end")
            or is_lifecycle_kind(kind)
            or is_control_kind(kind)
        ):
            self._since_dump = 0
            dump = structure_envelope(
                key=key,
                offset=int(getattr(frame, "offset", 0) or 0),
                digest=str(getattr(frame, "digest", "") or ""),
                kinds=dict(self._kinds),
            )
            post_ingest(dump, url=self.url)

    async def run(self, channel, stream_id: str, key: RunKey) -> None:
        async for frame in channel.subscribe(stream_id, overflow="drop-newest"):
            self.on_frame(frame, key=key)
        post_ingest(
            {
                "kind": "run.terminal",
                "complete": True,
                "stream_id": stream_id,
                "scenario": key.scenario,
                "test": key.item,
                "run": key.run,
                "experiment": key.experiment,
            },
            url=self.url,
        )


def forward_native_to_channel(event: dict[str, Any]) -> None:
    """EventStream subscriber: projected native records only, not Mealy hops."""
    if not isinstance(event, dict) or event.get("source") == "transition":
        return
    from mas.runtime.boundary.obs.event_stream import emit_to_channel

    emit_to_channel(event)


class LiveRunSink:
    """Publish native records onto the channel and POST ingest once. DAG nodes fold."""

    def __init__(self, url: str | None = None) -> None:
        self._sub = IngestSubscriber(url=url)

    def emit(self, record: dict[str, Any]) -> None:
        ch = get_channel()
        sid = get_stream_id()
        if ch is None or not sid:
            return
        offset = ch.publish_sync(sid, record)
        frame = frame_at(ch, sid, offset)
        if frame is not None:
            self._sub.on_frame(frame)
        else:
            post_ingest(record, url=self._sub.url)
