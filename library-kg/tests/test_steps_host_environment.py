#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""KG steps read connection settings and keys from the host environment."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from mas.runtime import hostenv

from mas.library.kg.steps import embed_states as es
from mas.library.kg.steps import neo4j_dump as nd


def test_neo4j_dump_connection_comes_from_the_host_environment(monkeypatch, tmp_path) -> None:
    seen: dict = {}

    def fake_fetch(**kw):
        seen.update(kw)
        return {"nodes": [{"id": "n"}], "edges": []}

    monkeypatch.setattr("mas.library.kg.neo4j.dump.fetch_kg_from_neo4j", fake_fetch)
    env = {
        "NEO4J_URI": "bolt://db:7687",
        "NEO4J_USER": "kg",
        "NEO4J_DB_AGENT": "agents",
        "PW_PROBE": "pw",
    }
    out = tmp_path / "kg" / "out.jsonld"
    with hostenv.overlay(env):
        artifact = nd.run_neo4j_dump(session_id="s", password_env="PW_PROBE", output_path=out)
    assert (seen["uri"], seen["username"], seen["password"], seen["database"]) == (
        "bolt://db:7687",
        "kg",
        "pw",
        "agents",
    )
    assert artifact.node_count == 1 and out.is_file()
    with pytest.raises(ValueError, match="session_id or run_id"):
        nd.run_neo4j_dump()


def test_embed_states_reads_its_key_and_skips_embedded_states(monkeypatch, tmp_path) -> None:
    kg = {"run_id": "r", "nodes": [{"node_type": "State", "id": "s1", "content": "hello"}]}
    (tmp_path / "kg.json").write_text(json.dumps(kg), encoding="utf-8")
    seen: dict = {}

    def fake_factory(**kw):
        seen.update(kw)
        return lambda texts: [[1.0, 2.0] for _ in texts]

    monkeypatch.setattr(es, "default_openai_embed_fn", fake_factory)
    step = es.EmbedStatesStep(name="e", config={"api_key_env": "EMBED_PROBE"})
    ctx = SimpleNamespace(output_dir=tmp_path)
    with hostenv.overlay({"EMBED_PROBE": "k-3"}):
        first = asyncio.run(step.execute(ctx))
    assert seen["api_key"] == "k-3" and first.metadata["embedded"] == 1

    out = tmp_path / "embeddings" / "states.jsonl"
    with out.open("a", encoding="utf-8") as fh:
        fh.write("{broken\n")
    again = asyncio.run(step.execute(ctx))
    assert again.metadata["embedded"] == 0
