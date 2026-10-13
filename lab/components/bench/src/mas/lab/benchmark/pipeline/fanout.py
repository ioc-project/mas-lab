#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Fork vs join classification and record fan-out on the telemetry channel.

**Fork** — several steps share one upstream artifact (same stream). Each
consumer gets every record; they may run at record granularity in parallel.

**Join** (branch collect) — one step waits on two or more different artifacts.
It starts only after every input stream is sealed (or the producer steps are
done). Records are not zipped unless the step itself does that.

**Seq** — one producer, one consumer: still a stream when the consumer is
incremental, otherwise the consumer waits for the producer to finish.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Literal

from mas.lab.benchmark.pipeline.incremental import (
    declared_callable,
    default_on_batch,
    folds_batches,
)
from mas.lab.benchmark.pipeline.keys import RunKey

_BATCH = 256

EdgeKind = Literal["fork", "join", "seq"]


@dataclass(frozen=True)
class Edge:
    src: str
    dst: str
    kind: EdgeKind
    artifact: str


def classify_edges(nodes: Iterable[Any]) -> list[Edge]:
    """Classify DAG edges from ``depends_on``.

    * Multiple destinations sharing one source → **fork** (same artifact).
    * A destination with two or more sources → **join** (different artifacts).
    * Single source, single destination → **seq**.
    """
    items = list(nodes)
    consumers: dict[str, list[str]] = {}
    for node in items:
        for dep in list(node.depends_on or []):
            consumers.setdefault(dep, []).append(node.name)
    edges: list[Edge] = []
    for node in items:
        deps = list(node.depends_on or [])
        if len(deps) >= 2:
            for dep in deps:
                edges.append(Edge(src=dep, dst=node.name, kind="join", artifact=dep))
            continue
        if len(deps) == 1:
            src = deps[0]
            kind: EdgeKind = "fork" if len(consumers.get(src, [])) > 1 else "seq"
            edges.append(Edge(src=src, dst=node.name, kind=kind, artifact=src))
    return edges


def edge_kind(edges: list[Edge], src: str, dst: str) -> EdgeKind | None:
    for edge in edges:
        if edge.src == src and edge.dst == dst:
            return edge.kind
    return None


def is_incremental(step: Any) -> bool:
    return folds_batches(step)


def uses_record_fold(step: Any) -> bool:
    """True when the scheduler should pump batches instead of ``execute``."""
    return folds_batches(step)


def wait_for_start(edges: list[Edge], src: str, dst: str, consumer_incremental: bool) -> bool:
    """True when *dst* may start as soon as *src* is running (record-level)."""
    if not consumer_incremental:
        return False
    kind = edge_kind(edges, src, dst)
    return kind in {"fork", "seq"}


def stream_sealed(channel: Any, stream_id: str) -> bool:
    if channel is None:
        return False
    return bool(channel.is_sealed(stream_id))


async def pump_stream(
    channel: Any,
    stream_id: str,
    step: Any,
    ctx: Any,
    *,
    key: RunKey | None = None,
    batch_size: int = _BATCH,
) -> Any:
    """Fold *stream_id* into *step* in batches, then ``finish``."""
    from mas.lab.benchmark.pipeline.channel import native_body

    begin = declared_callable(step, "begin")
    if begin is not None:
        begin(step, key or RunKey("e", "s", "i", "r1"), ctx)
    cap = max(1, batch_size)
    pending: list[dict[str, Any]] = []
    async for frames in channel.subscribe_batches(stream_id):
        for frame in frames:
            pending.append(native_body(frame))
            if len(pending) >= cap:
                default_on_batch(step, pending, ctx)
                pending = []
        if pending:
            default_on_batch(step, pending, ctx)
            pending = []
    if pending:
        default_on_batch(step, pending, ctx)
    finish = declared_callable(step, "finish")
    if finish is not None:
        return await finish(step, ctx)
    execute = declared_callable(step, "execute")
    if execute is not None:
        return await execute(step, ctx)
    return None


def publish_output_stream(channel: Any, stream_id: str, output: Any) -> int:
    """Publish records from a step output onto *stream_id* and seal it."""
    n = 0
    for rec in iter_output_records(output):
        channel.publish_sync(stream_id, rec)
        n += 1
    channel.seal_sync(stream_id)
    return n


def iter_output_records(output: Any) -> Iterable[dict[str, Any]]:
    from mas.lab.benchmark.pipeline.models import StepOutput

    files: list = []
    if isinstance(output, StepOutput):
        data = output.data
        files = list(output.files or [])
    elif isinstance(output, dict):
        data = output
    elif isinstance(output, list):
        for item in output:
            if isinstance(item, dict):
                yield item
        return
    else:
        return
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                yield item
        return
    if isinstance(data, dict):
        for key in ("records", "events", "spans", "rows"):
            rows = data.get(key)
            if isinstance(rows, list):
                for item in rows:
                    if isinstance(item, dict):
                        yield item
                return
        if data.get("kind"):
            yield data
            return
    for path in files:
        text = str(path)
        if text.endswith((".jsonl", ".ndjson")):
            try:
                from mas.library.standard.lib.jsonl_iter import iter_jsonl_records

                for batch in iter_jsonl_records(path, batch_size=256):
                    yield from batch
            except Exception:
                from pathlib import Path
                import json

                dest = Path(path)
                if dest.is_file():
                    with dest.open("r", encoding="utf-8") as handle:
                        for line in handle:
                            line = line.strip()
                            if not line:
                                continue
                            parsed = json.loads(line)
                            if isinstance(parsed, dict):
                                yield parsed
