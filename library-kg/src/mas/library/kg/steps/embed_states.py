#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""EmbedStatesStep — embed every State node in the KG.

Reads ``kg.json`` when present (optional normalization extension), extracts State nodes,
and computes a vector embedding for each State's `content` field.

This implements the ontology relation:
    State --hasEmbedding--> VectorEmbedding

The output follows the same storage convention as EmbedStep but operates
on individual KG States (not trajectory-level session I/O):

Storage layout::

    {run_dir}/embeddings/
        states.jsonl      ← one record per State node

Each record::

    {
      "run_id":        str,
      "state_id":      str,       # stateNodeId from KG
      "call_id":       str,       # sourceCallId (links to AgentCall/LLMCall)
      "semantic_type": str,       # "initial" | "final"
      "content":       str,       # raw text (truncated for storage)
      "model":         str,
      "dim":           int,
      "vector":        list[float],
    }

Config keys::

    model: str            embedding model  (default: "text-embedding-3-small")
    api_base: str         OpenAI-compatible endpoint
    api_key: SecretRef    secret for the API key (default {secret: openai})
    batch_size: int       number of texts per API call (default: 64)
    overwrite: bool       re-embed already-present states (default: false)
    max_content: int      max characters stored in record (default: 500)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from mas.lab.benchmark.pipeline import PipelineStep, StepOutput
from mas.lab.benchmark.pipeline.executor import ExecutionContext

from mas.library.kg.embeddings.state_embeddings import default_openai_embed_fn

logger = logging.getLogger(__name__)

# Conservative char cap before handing text to the embeddings API — shared
# with embeddings/state_embeddings.py's own per-text truncation.
_MAX_EMBED_INPUT_CHARS = 4096


class EmbedStatesStep(PipelineStep):
    """Embed every State node in the KG (hasEmbedding relation)."""

    type = "embed_states"

    async def execute(self, ctx: ExecutionContext) -> StepOutput:
        bench_dir = ctx.output_dir
        kg_path = bench_dir / "kg.json"

        if not kg_path.exists():
            logger.error("kg.json not found — embed_states requires a knowledge graph artifact")
            return StepOutput(metadata={"embedded": 0})

        model = self.config.get("model", "text-embedding-3-small")
        api_base = self.config.get("api_base", "")
        batch_size = int(self.config.get("batch_size", 64))
        overwrite = self.config.get("overwrite", False)
        max_content = int(self.config.get("max_content", 500))

        # Load KG
        kg = json.loads(kg_path.read_text(encoding="utf-8"))
        nodes = kg.get("nodes", [])
        run_id = kg.get("run_id", "")

        # Extract State nodes
        states = [n for n in nodes if n.get("node_type") == "State"]
        if not states:
            logger.warning("EmbedStates: no State nodes found in kg.json")
            return StepOutput(metadata={"embedded": 0})

        # Output
        embeddings_dir = bench_dir / "embeddings"
        embeddings_dir.mkdir(parents=True, exist_ok=True)
        out_path = embeddings_dir / "states.jsonl"

        # Skip already-embedded states
        existing_ids: set[str] = set()
        if out_path.exists() and not overwrite:
            for line in out_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        existing_ids.add(json.loads(line).get("state_id", ""))
                    except json.JSONDecodeError:
                        logger.debug("suppressed", exc_info=True)

        to_embed = [
            s
            for s in states
            if s.get("id", s.get("stateNodeId", "")) not in existing_ids
            and s.get("content", "").strip()
        ]

        if not to_embed:
            logger.info("EmbedStates: all %d states already embedded", len(existing_ids))
            return StepOutput(
                data={"embeddings_path": str(out_path)},
                files=[out_path],
                metadata={"embedded": 0, "total_states": len(states)},
            )

        from mas.library.standard.lib.openai_client import reveal_openai_key

        # The openai default secret goes only to its own endpoint; other bases need an api_key.
        api_key = reveal_openai_key(self.config.get("api_key"), api_base)

        # Extract texts
        texts = [s.get("content", "")[:_MAX_EMBED_INPUT_CHARS] for s in to_embed]  # API limit guard

        logger.info("EmbedStates: embedding %d states (model=%s)", len(texts), model)
        embed_fn = default_openai_embed_fn(model=model, api_base=api_base, api_key=api_key)
        vectors: list[Any] = []
        for i in range(0, len(texts), batch_size):
            vectors.extend(embed_fn(texts[i : i + batch_size]))

        ts = datetime.now(timezone.utc).isoformat()
        embedded = 0
        with open(out_path, "a", encoding="utf-8") as f:
            for state, vector in zip(to_embed, vectors):
                if vector is None:
                    continue
                state_id = state.get("id", state.get("stateNodeId", ""))
                rec = {
                    "run_id": run_id,
                    "state_id": state_id,
                    "call_id": state.get("sourceCallId", ""),
                    "semantic_type": state.get("semanticType", ""),
                    "content": state.get("content", "")[:max_content],
                    "model": model,
                    "dim": len(vector),
                    "vector": vector,
                    "computed_at": ts,
                }
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                embedded += 1

        logger.info("EmbedStates done: %d/%d states embedded", embedded, len(states))
        return StepOutput(
            data={"embeddings_path": str(out_path)},
            files=[out_path],
            metadata={"embedded": embedded, "total_states": len(states)},
        )
