#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Paths of the lab steps and codecs that hold secrets: OTLP push, embeddings, LLM call, metrics collection."""

from __future__ import annotations

import asyncio
import csv
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fake_openai import FakeOpenAI
from mas.lab.infra.datastore import DatastoreSpec
from mas.library.lab.codecs import otel_codecs as oc
from mas.library.lab.steps.data import deserialize as de
from mas.library.lab.steps.data import embed_trajectories as et
from mas.library.lab.steps.data import generate_dataset as gd
from mas.library.lab.steps.data import serialize as ser
from mas.library.lab.steps.eval import collect_metrics as cm
from mas.library.lab.steps.services import export_otel as eo
from mas.runtime import hostenv


def test_otlp_codec_pushes_a_list_through_a_file_and_refuses_other_types(monkeypatch) -> None:
    seen: dict = {}

    def fake_push(path, endpoint, **kw):
        seen.update(endpoint=endpoint, lines=Path(path).read_text().splitlines(), **kw)
        return {"status": "dry-run", "detail": "ok"}

    monkeypatch.setattr("mas.library.telemetry.collector.otlp.push_file", fake_push)
    codec = oc.OtlpOtelCodec(DatastoreSpec(uri="http://collector:4318/"))
    codec.encode([{"kind": "llm_call_start"}], dry_run=True, batch_size=5)
    assert seen["endpoint"] == "http://collector:4318"
    assert seen["lines"] == ['{"kind": "llm_call_start"}'] and seen["dry_run"] and seen["batch_size"] == 5
    with pytest.raises(TypeError, match="expected list"):
        codec.encode(42)


def test_embeddings_go_through_the_client_and_a_failed_batch_is_none() -> None:
    reply = lambda body: {"role": "assistant", "content": ""}  # noqa: E731  (the chat route is unused)
    with FakeOpenAI(reply) as server:
        vectors = et._embed_batch(["a", "b"], "m", server.url, "k", 1)
    # the fake serves chat completions only: each embeddings batch fails and yields None, never an exception
    assert vectors == [None, None]


def test_llm_call_returns_the_reply_text() -> None:
    with FakeOpenAI(lambda body: {"role": "assistant", "content": "ok"}) as server:
        out = gd._call_llm("p", "gpt-4o", server.url, "k", 0.0)
    assert out == "ok"


def test_infra_bundle_is_looked_up_beside_the_pipeline(tmp_path) -> None:
    ctx = SimpleNamespace(pipeline=SimpleNamespace(config_path=tmp_path / "exp.yaml"))
    with pytest.raises(FileNotFoundError, match="nobundle"):
        ser._load_bundle_store("s", "nobundle", "kg", "read", ctx)
    config = {"artifact_kind": "kg", "source_step": "x", "infra": "nobundle"}
    for step in (ser.SerializeStep(name="s", config=config), de.DeserializeStep(name="d", config=config)):
        with pytest.raises(FileNotFoundError, match="nobundle"):
            asyncio.run(step.execute(ctx))


def test_export_otel_converts_once_then_keeps_the_json_output(tmp_path, monkeypatch) -> None:
    (tmp_path / "events.jsonl").write_text("{}\n", encoding="utf-8")
    step = eo.ExportOtelStep(name="x", config={"events_jsonl": "events.jsonl"})
    ctx = SimpleNamespace(output_dir=tmp_path, template_vars={})
    monkeypatch.setattr(
        "mas.library.telemetry.collector.otlp.convert_file_to_otlp_jsonl", lambda *a: {"status": "error", "detail": "x"}
    )
    asyncio.run(step.execute(ctx))  # first run converts
    (tmp_path / "otel_spans.jsonl").write_text("old", encoding="utf-8")
    out = asyncio.run(step.execute(ctx))
    assert out.metadata["status"] == "skipped"
    assert (tmp_path / "otel_spans.jsonl").read_text() == "old"


def _run_dir(root: Path, scenario: str, run_ref: str | None = None) -> Path:
    run = root / scenario / "item1" / "r1"
    run.mkdir(parents=True)
    (run / "run_info.json").write_text("{broken", encoding="utf-8")
    (run / "metrics.json").write_text(json.dumps({"session": {"quality": {"value": 0.5}}}), encoding="utf-8")
    if run_ref:
        (run / ".run_ref").write_text(run_ref, encoding="utf-8")
    return run


def test_collect_metrics_counts_cached_events_and_filters_scenarios(tmp_path) -> None:
    cache = tmp_path / "cache"
    (cache / "h1" / "traces").mkdir(parents=True)
    (cache / "h1" / "result.json").write_text(json.dumps({"elapsed_ms": 2000, "status": "ok"}), encoding="utf-8")
    lines = [json.dumps({"kind": "llm_call_start"}), "", "{broken", json.dumps({"kind": "tool_call_start"})]
    (cache / "h1" / "traces" / "events.jsonl").write_text("\n".join(lines), encoding="utf-8")
    (cache / "h2").mkdir()
    (cache / "h2" / "result.json").write_text("{broken", encoding="utf-8")
    out = tmp_path / "out"
    _run_dir(out, "keep", "h1")
    _run_dir(out, "drop")
    _run_dir(out, "bad-cache", "h2")
    ctx = SimpleNamespace(output_dir=out, pipeline=SimpleNamespace(config_path=None), template_vars={})
    step = cm.CollectMetricsStep(name="c", config={"scenarios": ["keep", "bad-cache"]})
    with hostenv.overlay({"MAS_TRACE_CACHE": str(cache)}):
        asyncio.run(step.execute(ctx))
    rows = list(csv.DictReader((next(out.rglob("results.csv"))).open()))
    kept = next(r for r in rows if r["scenario"] == "keep")
    assert {r["scenario"] for r in rows} == {"keep", "bad-cache"}
    assert (kept["latency_s"], kept["n_llm_calls"], kept["n_tool_calls"]) == ("2.0", "1", "1")


def test_collect_metrics_dataset_problems_skip_enrichment(tmp_path) -> None:
    anchor = SimpleNamespace(config_path=tmp_path / "exp.yaml")
    assert cm._load_dataset_lookup({"path": "nope.yaml"}, config_path=anchor.config_path) == {}
    bad = tmp_path / "bad.yaml"
    bad.write_text(":\n  - [unclosed", encoding="utf-8")
    assert cm._load_dataset_lookup({"path": "bad.yaml"}, config_path=anchor.config_path) == {}
