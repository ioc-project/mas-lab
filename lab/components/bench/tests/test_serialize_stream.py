#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
from pathlib import Path

from mas.lab.benchmark.pipeline.channel import InprocChannel, set_channel, set_stream_id
from mas.library.lab.steps.data.deserialize import decode_streaming
from mas.library.lab.steps.data.serialize import encode_streaming
from mas.library.standard.lib.jsonl_iter import iter_jsonl_records, peek_jsonl


class _BatchCodec:
    artifact_kind = "events"
    store_type = "memory"

    def __init__(self) -> None:
        self.calls: list[list] = []

    def encode(self, artifact, **opts):  # noqa: ANN001
        self.calls.append(list(artifact) if isinstance(artifact, list) else [artifact])

    def encode_iter(self, records, **opts):  # noqa: ANN001
        batch_size = int(opts.get("batch_size") or 2)
        batch: list = []
        n = 0
        for rec in records:
            batch.append(rec)
            n += 1
            if len(batch) >= batch_size:
                self.encode(batch, **opts)
                batch = []
        if batch:
            self.encode(batch, **opts)
        return n

    def decode(self, **opts):  # noqa: ANN001
        return [{"fallback": True}]


def test_iter_jsonl_never_needs_read_text(tmp_path: Path) -> None:
    dest = tmp_path / "events.jsonl"
    dest.write_text(
        "\n".join(json.dumps({"i": i, "kind": "x"}) for i in range(5)) + "\n",
        encoding="utf-8",
    )
    batches = list(iter_jsonl_records(dest, batch_size=2))
    assert [len(b) for b in batches] == [2, 2, 1]
    assert peek_jsonl(dest) == {"i": 0, "kind": "x"}


def test_encode_streaming_jsonl_is_batched(tmp_path: Path) -> None:
    dest = tmp_path / "spans.jsonl"
    dest.write_text(
        "\n".join(json.dumps({"context": {"trace_id": "a"}, "i": i}) for i in range(5)),
        encoding="utf-8",
    )
    codec = _BatchCodec()
    assert encode_streaming(codec, dest, {"batch_size": 2}) is True
    assert len(codec.calls) == 3
    assert sum(len(c) for c in codec.calls) == 5


def test_encode_streaming_falls_back_for_document() -> None:
    codec = _BatchCodec()
    assert encode_streaming(codec, {"nodes": [1], "edges": []}, {}) is False
    assert codec.calls == [[{"nodes": [1], "edges": []}]]


def test_encode_streaming_from_channel() -> None:
    ch = InprocChannel()
    set_channel(ch)
    sid = "e/s/i/r1#1"
    set_stream_id(sid)
    ch.publish_sync(sid, {"kind": "llm_call_end", "n": 1})
    ch.publish_sync(sid, {"kind": "tool_call_end", "n": 2})
    codec = _BatchCodec()
    assert encode_streaming(codec, {"stream_id": sid}, {"batch_size": 10}) is True
    flat = [rec for batch in codec.calls for rec in batch]
    assert [r["kind"] for r in flat] == ["llm_call_end", "tool_call_end"]


def test_encode_streaming_never_calls_read_text(tmp_path: Path, monkeypatch) -> None:
    dest = tmp_path / "events.jsonl"
    dest.write_text(
        "\n".join('{"i": %d}' % i for i in range(4)) + "\n",
        encoding="utf-8",
    )

    def _boom(*_a, **_k):
        raise AssertionError("read_text must not load the whole JSONL file")

    monkeypatch.setattr(Path, "read_text", _boom)
    codec = _BatchCodec()
    assert encode_streaming(codec, dest, {"batch_size": 2}) is True
    assert sum(len(c) for c in codec.calls) == 4


def test_decode_streaming_jsonl_and_fallback(tmp_path: Path) -> None:
    dest = tmp_path / "in.jsonl"
    dest.write_text(json.dumps({"kind": "x"}) + "\n", encoding="utf-8")
    codec = _BatchCodec()
    rows = decode_streaming(codec, {"path": str(dest)})
    assert rows == [{"kind": "x"}]
    assert decode_streaming(codec, {}) == [{"fallback": True}]
