#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest

from mas.lab.benchmark.pipeline.artifact import (
    Artifact,
    ArtifactError,
    ArtifactType,
    wrap_step_output,
)
from mas.lab.benchmark.pipeline.keys import RunKey
from mas.lab.benchmark.pipeline.persist import digest_of_records, persist_artifact
from mas.lab.benchmark.pipeline.types import KG, TRACE_STATS


def _key() -> RunKey:
    return RunKey("e", "s", "item1", "r1")


def test_artifact_batch_digest_invariant(tmp_path) -> None:
    recs = [{"n": i, "v": i * 0.1} for i in range(10)]
    one = Artifact(name="data", key=_key(), spec=ArtifactType(name="data", shape="records"))
    for rec in recs:
        one.append(rec)
    one.seal()
    many = Artifact(name="data", key=_key(), spec=ArtifactType(name="data", shape="records"))
    many.append(recs)
    many.seal()
    assert one.digest == many.digest == digest_of_records(recs)
    persist_artifact(one, tmp_path / "a.jsonl")
    persist_artifact(many, tmp_path / "b.jsonl")
    assert one.digest == many.digest


def test_artifact_record_validation() -> None:
    pytest.importorskip("jsonschema")
    spec = ArtifactType(
        name="m",
        shape="records",
        schema={"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}},
        on_invalid="error",
    )
    art = Artifact(name="m", key=_key(), spec=spec)
    with pytest.raises(ArtifactError) as exc:
        art.append({"nope": 1})
    assert exc.value.offset == 0
    spec_drop = ArtifactType(name="m", shape="records", schema=spec.schema, on_invalid="drop")
    dropped = Artifact(name="m", key=_key(), spec=spec_drop)
    dropped.append({"nope": 1})
    dropped.append({"id": "ok"})
    dropped.seal()
    assert [r.get("id") for r in dropped.records()] == ["ok"]
    spec_q = ArtifactType(name="m", shape="records", schema=spec.schema, on_invalid="quarantine")
    quarantined = Artifact(name="m", key=_key(), spec=spec_q)
    quarantined.append({"nope": 1})
    quarantined.append({"id": "ok"})
    quarantined.seal()
    rows = quarantined.records()
    assert rows[0].get("_quarantine") is True
    assert rows[0].get("offset") == 0
    assert rows[1].get("id") == "ok"


def test_artifact_batch_validation_vectorized() -> None:
    pytest.importorskip("jsonschema")
    from mas.lab.benchmark.pipeline.artifact import validate_batch

    spec = ArtifactType(
        name="m",
        shape="records",
        schema={"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}},
        on_invalid="drop",
    )
    checked = validate_batch(spec, [{"nope": 1}, {"id": "a"}, {"id": "b"}], start_offset=0)
    assert checked[0] is None
    assert [r.get("id") for r in checked[1:] if r] == ["a", "b"]


def test_artifact_snapshot_latest() -> None:
    art = Artifact(name="trace_stats", key=_key(), spec=TRACE_STATS)
    art.snapshot({"n_llm_calls": 1})
    art.snapshot({"n_llm_calls": 2})
    assert art.latest() == {"n_llm_calls": 2}
    assert art.records() == [{"n_llm_calls": 2}]


def test_artifact_upsert_key() -> None:
    spec = ArtifactType(name="c", shape="records", mode="upsert", key=["id"])
    art = Artifact(name="c", key=_key(), spec=spec)
    art.upsert({"id": "a", "v": 1})
    art.upsert({"id": "a", "v": 2})
    art.seal()
    assert art.records() == [{"id": "a", "v": 2}]


def test_artifact_seal_invariants() -> None:
    art = Artifact(name="kg", key=_key(), spec=KG)
    art.append({"kind": "edge", "src": "n1", "dst": "n2", "rel": "x"})
    art.append({"kind": "node", "id": "n1"})
    with pytest.raises(ArtifactError):
        art.seal()
    art2 = Artifact(name="kg", key=_key(), spec=KG)
    art2.append({"kind": "edge", "src": "n1", "dst": "n2", "rel": "x"})
    art2.append({"kind": "node", "id": "n1"})
    art2.append({"kind": "node", "id": "n2"})
    art2.seal()


def test_artifact_reset_on_retry() -> None:
    art = Artifact(name="events", key=_key(), spec=ArtifactType(name="events", shape="records"))
    art.append({"kind": "llm_call_end"})
    assert not art.sealed
    # attempt reset = drop fold; a new artifact is used for the next attempt
    art2 = Artifact(
        name="events",
        key=RunKey("e", "s", "item1", "r1", attempt=2),
        spec=ArtifactType(name="events", shape="records"),
    )
    art2.append({"kind": "llm_call_end"})
    art2.seal()
    assert art.digest != ""
    assert art2.sealed


def test_legacy_adapter_wraps_stepoutput() -> None:
    wrapped = wrap_step_output({"score": 1}, [], _key(), name="metrics")
    assert wrapped.records() == [{"score": 1}]
    pd = pytest.importorskip("pandas")
    frame = pd.DataFrame([{"a": 1}, {"a": 2}])
    rows = wrap_step_output(frame, [], _key(), name="data")
    assert len(rows.records()) == 2


def test_artifact_persist_policy(tmp_path) -> None:
    mem = Artifact(
        name="trace_stats",
        key=_key(),
        spec=ArtifactType(name="trace_stats", mode="snapshot", persist="never"),
    )
    mem.snapshot({"n": 1})
    mem.seal()
    assert persist_artifact(mem, tmp_path / "x.json") is None
    disk = Artifact(
        name="metrics",
        key=_key(),
        spec=ArtifactType(name="metrics", persist="always"),
    )
    disk.append({"ok": True})
    disk.seal()
    path = persist_artifact(disk, tmp_path / "metrics.json", fmt="json")
    assert path is not None and path.is_file()


def test_artifact_spill_over_bound(tmp_path) -> None:
    from mas.lab.benchmark.pipeline.persist import spill_over_bound

    art = Artifact(
        name="events",
        key=_key(),
        spec=ArtifactType(name="events", shape="records"),
    )
    art.append([{"i": i} for i in range(5)])
    art.seal()
    digest = art.digest
    assert spill_over_bound(art, bound=10, dest=tmp_path / "no.jsonl") is None
    spilled = spill_over_bound(art, bound=2, dest=tmp_path / "spill.jsonl")
    assert spilled is not None and spilled.is_file()
    assert art.digest == digest


def test_artifact_live_vs_sealed_same_consumer() -> None:
    live = Artifact(name="data", key=_key(), spec=ArtifactType(name="data", shape="records"))
    live.append([{"x": 1}, {"x": 2}])
    live.seal()
    sealed = Artifact(name="data", key=_key(), spec=ArtifactType(name="data", shape="records"))
    sealed.append([{"x": 1}, {"x": 2}])
    sealed.seal()
    assert live.digest == sealed.digest
    assert live.materialize() == sealed.materialize()
