#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""KG steps read hosts from hostenv and secrets from SecretRef."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from mas.runtime import hostenv
from secret_fakes import bound_secrets

from mas.library.kg.steps import embed_states as es
from mas.library.kg.steps import neo4j_dump as nd


def test_neo4j_dump_connection_comes_from_the_host_and_secret_store(monkeypatch, tmp_path) -> None:
    seen: dict = {}

    def fake_fetch(**kw):
        seen.update(kw)
        return {"nodes": [{"id": "n"}], "edges": []}

    monkeypatch.setattr("mas.library.kg.neo4j.dump.fetch_kg_from_neo4j", fake_fetch)
    env = {
        "NEO4J_URI": "bolt://db:7687",
        "NEO4J_USER": "kg",
        "NEO4J_DB_AGENT": "agents",
    }
    out = tmp_path / "kg" / "out.jsonld"
    with hostenv.overlay(env), bound_secrets({"neo4j": "pw"}):
        artifact = nd.run_neo4j_dump(session_id="s", output_path=out, password={"secret": "neo4j"})
    assert (seen["uri"], seen["username"], seen["password"], seen["database"]) == (
        "bolt://db:7687",
        "kg",
        "pw",
        "agents",
    )
    assert artifact.node_count == 1 and out.is_file()
    with pytest.raises(ValueError, match="session_id or run_id"):
        nd.run_neo4j_dump()


def test_neo4j_steps_send_no_password_without_a_stored_secret_and_refuse_a_missing_required_one(
    monkeypatch,
) -> None:
    # kills: a passwordless local Neo4j refused for a default secret nobody set
    from mas.runtime.secrets.errors import SecretMissing

    from mas.library.kg.steps import neo4j_push as np_

    seen: dict = {}
    monkeypatch.setattr(
        "mas.library.kg.neo4j.dump.fetch_kg_from_neo4j",
        lambda **kw: seen.update(kw) or {"nodes": [], "edges": []},
    )
    nd.run_neo4j_dump(session_id="s")
    assert seen["password"] == ""
    assert np_._resolve_conn(None, None, None, None)["password"] == ""
    with pytest.raises(SecretMissing, match="neo4j"):
        nd.run_neo4j_dump(session_id="s", password={"secret": "neo4j"})
    with pytest.raises(SecretMissing, match="neo4j"):
        np_._resolve_conn(None, None, {"secret": "neo4j"}, None)
    with bound_secrets({"neo4j": "pw"}):
        assert np_._resolve_conn(None, None, {"secret": "neo4j"}, None)["password"] == "pw"


def test_embed_states_reads_its_key_and_skips_embedded_states(monkeypatch, tmp_path) -> None:
    kg = {"run_id": "r", "nodes": [{"node_type": "State", "id": "s1", "content": "hello"}]}
    (tmp_path / "kg.json").write_text(json.dumps(kg), encoding="utf-8")
    seen: dict = {}

    def fake_factory(**kw):
        seen.update(kw)
        return lambda texts: [[1.0, 2.0] for _ in texts]

    monkeypatch.setattr(es, "default_openai_embed_fn", fake_factory)
    step = es.EmbedStatesStep(name="e", config={"api_key": {"secret": "openai"}})
    ctx = SimpleNamespace(output_dir=tmp_path)
    with bound_secrets({"openai": "k-3"}):
        first = asyncio.run(step.execute(ctx))
    assert seen["api_key"] == "k-3" and first.metadata["embedded"] == 1

    out = tmp_path / "embeddings" / "states.jsonl"
    with out.open("a", encoding="utf-8") as fh:
        fh.write("{broken\n")
    again = asyncio.run(step.execute(ctx))
    assert again.metadata["embedded"] == 0
