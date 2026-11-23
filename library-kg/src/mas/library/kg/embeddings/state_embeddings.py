#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Embed State nodes and materialise ``hasEmbedding`` in the ontology KG.

Works on:
  • a full ``kg.jsonld`` dict (batch pipeline step),
  • a list of State node dicts,
  • or a stream of graph ops (``AddNode`` for ``node_type=State``).

The embedding API is shared; only the *carrier* differs (full KG vs patch stream).
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

EmbedFn = Callable[[Sequence[str]], List[Optional[List[float]]]]


def iter_state_nodes(
    kg_or_nodes: Dict[str, Any] | List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Return State nodes from a KG dict or a bare node list."""
    if isinstance(kg_or_nodes, list):
        nodes = kg_or_nodes
    else:
        nodes = list(kg_or_nodes.get("nodes") or [])
    return [
        n
        for n in nodes
        if str(n.get("node_type") or "") == "State" and str(n.get("content") or "").strip()
    ]


def _embedding_node_id(state_id: str, model: str) -> str:
    digest = hashlib.sha256(f"{state_id}:{model}".encode()).hexdigest()[:16]
    return f"emb-{digest}"


def embedding_graph_ops(
    state: Dict[str, Any],
    *,
    vector: List[float],
    model: str,
    run_id: str = "",
    max_vector_store: int = 0,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Return ``(VectorEmbedding node, hasEmbedding edge)`` for one State."""
    state_id = str(state.get("id") or state.get("stateNodeId") or "")
    emb_id = _embedding_node_id(state_id, model)
    ts = datetime.now(timezone.utc).isoformat()
    node: Dict[str, Any] = {
        "node_type": "VectorEmbedding",
        "id": emb_id,
        "embeddingId": emb_id,
        "stateNodeId": state_id,
        "model": model,
        "dim": len(vector),
        "runId": run_id or state.get("runId", ""),
        "computedAt": ts,
        "contentHash": state.get("contentHash") or "",
    }
    node["vector"] = vector[:max_vector_store] if max_vector_store > 0 else vector
    edge: Dict[str, Any] = {
        "edge_type": "hasEmbedding",
        "from_id": state_id,
        "from_type": "state",
        "to_id": emb_id,
        "to_type": "embedding",
        "model": model,
        "dim": len(vector),
    }
    return node, edge


def patches_for_state_embedding(
    state: Dict[str, Any],
    *,
    vector: List[float],
    model: str,
    run_id: str = "",
) -> List[Dict[str, Any]]:
    """Serialised graph ops (JSON-safe) for one embedded State — stream-friendly."""
    node, edge = embedding_graph_ops(state, vector=vector, model=model, run_id=run_id)
    return [
        {
            "op": "AddNode",
            "node_id": node["id"],
            "node_type": "VectorEmbedding",
            "attrs": {k: v for k, v in node.items() if k not in ("id", "node_type")},
        },
        {
            "op": "AddEdge",
            "src_id": edge["from_id"],
            "dst_id": edge["to_id"],
            "rel": "hasEmbedding",
            "attrs": {"model": model, "dim": len(vector)},
        },
    ]


def stream_embedding_ops(
    graph_ops: Iterable[Dict[str, Any]],
    *,
    embed_fn: EmbedFn,
    model: str,
    run_id: str = "",
) -> Iterator[Dict[str, Any]]:
    """Pass-through graph ops; emit embedding ops after each State ``AddNode``."""
    pending_states: List[Dict[str, Any]] = []
    for op in graph_ops:
        yield op
        if op.get("op") != "AddNode":
            continue
        if str(op.get("node_type") or "") != "State":
            continue
        attrs = dict(op.get("attrs") or {})
        content = str(attrs.get("content") or "").strip()
        if not content:
            continue
        state = {"id": op.get("node_id"), **attrs}
        pending_states.append(state)

    if not pending_states:
        return
    texts = [str(s.get("content") or "")[:4096] for s in pending_states]
    vectors = embed_fn(texts)
    for state, vector in zip(pending_states, vectors):
        if not vector:
            continue
        for emb_op in patches_for_state_embedding(
            state,
            vector=vector,
            model=model,
            run_id=run_id,
        ):
            yield emb_op


def attach_embeddings_to_kg(
    kg: Dict[str, Any],
    *,
    embed_fn: EmbedFn,
    model: str,
    batch_size: int = 64,
    skip_existing: bool = True,
) -> Dict[str, Any]:
    """Return a new KG with VectorEmbedding nodes and ``hasEmbedding`` edges."""
    states = iter_state_nodes(kg)
    if not states:
        return kg

    run_id = str(kg.get("run_id") or "")
    nodes = list(kg.get("nodes") or [])
    edges = list(kg.get("edges") or [])

    existing_emb_states: set[str] = set()
    if skip_existing:
        for e in edges:
            if e.get("edge_type") == "hasEmbedding":
                existing_emb_states.add(str(e.get("from_id") or ""))

    to_embed = [
        s
        for s in states
        if str(s.get("id") or s.get("stateNodeId") or "") not in existing_emb_states
    ]
    if not to_embed:
        return kg

    texts = [str(s.get("content") or "")[:4096] for s in to_embed]
    all_vectors: List[Optional[List[float]]] = []
    for i in range(0, len(texts), batch_size):
        all_vectors.extend(embed_fn(texts[i : i + batch_size]))

    for state, vector in zip(to_embed, all_vectors):
        if not vector:
            continue
        emb_node, emb_edge = embedding_graph_ops(
            state,
            vector=vector,
            model=model,
            run_id=run_id,
        )
        nodes.append(emb_node)
        edges.append(emb_edge)

    out = dict(kg)
    out["nodes"] = nodes
    out["edges"] = edges
    metadata = dict(out.get("metadata") or {})
    metadata["embeddings"] = {
        "model": model,
        "state_count": len(to_embed),
        "attached": sum(1 for v in all_vectors if v),
    }
    out["metadata"] = metadata
    return out


def default_openai_embed_fn(
    *,
    model: str,
    api_base: str = "",
    api_key: str = "",
) -> EmbedFn:
    """Build an embed function using the OpenAI-compatible embeddings API."""

    def _fn(texts: Sequence[str]) -> List[Optional[List[float]]]:
        from mas.library.standard.lib.openai_client import build_openai_client

        try:
            client = build_openai_client(api_key, api_base)
        except ImportError:
            logger.error("openai package required for embeddings")
            return [None] * len(texts)
        non_empty = [(i, t) for i, t in enumerate(texts) if str(t).strip()]
        batch_results: List[Optional[List[float]]] = [None] * len(texts)
        if non_empty:
            try:
                resp = client.embeddings.create(
                    model=model,
                    input=[t for _, t in non_empty],
                )
                for j, (orig_idx, _) in enumerate(non_empty):
                    batch_results[orig_idx] = resp.data[j].embedding
            except Exception as exc:
                from mas.runtime.reliability.classify import error_summary

                # A failed batch returns None for each of its texts: the callers count
                # what was embedded against the total. The log says the error's type
                # and status only, never its text (an SDK error's text is the
                # provider's body, which may echo the key) nor its traceback.
                logger.error(
                    "Embedding API error for a batch of %d text(s): %s",
                    len(non_empty),
                    error_summary(exc),
                )
        return batch_results

    return _fn
