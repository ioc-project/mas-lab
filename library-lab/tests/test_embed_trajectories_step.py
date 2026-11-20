#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""EmbedStep reads its key through the host environment and skips what it already embedded."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from mas.library.lab.steps.data import embed_trajectories as et
from mas.runtime import hostenv


def _run(tmp_path, monkeypatch, **config):
    seen: dict = {}

    def fake_embed(texts, **kw):
        seen["texts"], seen["kw"] = list(texts), kw
        return [None if t == "skip" else [0.5, 0.25] for t in texts]

    monkeypatch.setattr(et, "_embed_batch", fake_embed)
    step = et.EmbedStep(name="e", config={"levels": ["session"], "fields": ["input"], **config})
    out = asyncio.run(step.execute(SimpleNamespace(output_dir=tmp_path)))
    return out, seen


def test_missing_trajectories_embed_nothing(tmp_path, monkeypatch) -> None:
    out, _ = _run(tmp_path, monkeypatch)
    assert out.metadata == {"embedded": 0}


def test_key_comes_from_the_host_environment_and_a_rerun_skips_done_runs(tmp_path, monkeypatch) -> None:
    rows = [
        {"run_id": "a", "session": {"input": "one"}},
        {"run_id": "b", "session": {"input": "skip"}},
        {"run_id": "c", "session": {"input": "three"}, "_extraction_error": "bad"},
    ]
    (tmp_path / "trajectories.jsonl").write_text("\n".join([*map(json.dumps, rows), "{broken"]), encoding="utf-8")
    with hostenv.overlay({"EMBED_KEY_PROBE": "k-1"}):
        out, seen = _run(tmp_path, monkeypatch, api_key_env="EMBED_KEY_PROBE", api_base="http://x", batch_size=2)
    assert seen["kw"]["api_key"] == "k-1" and seen["kw"]["batch_size"] == 2
    assert seen["texts"] == ["one", "skip"]
    assert out.metadata == {"embedded": 1}
    written = tmp_path / "embeddings" / "session__input.jsonl"
    assert [json.loads(line)["run_id"] for line in written.read_text().splitlines()] == ["a"]

    with written.open("a", encoding="utf-8") as fh:
        fh.write("{broken\n")
    again, seen2 = _run(tmp_path, monkeypatch, max_items=2)
    assert seen2["texts"] == ["skip"]
    assert again.metadata == {"embedded": 0}
