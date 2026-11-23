#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""EmbedStep — pipeline step that computes and stores embeddings for
trajectory fields (session input, session output, eventually agent-level).

Storage layout::

    output/benchmark/
      trajectories.jsonl              ← input
      embeddings/
        session__input.jsonl          ← one file per (level, field)
        session__output.jsonl
        agent__<id>__output.jsonl     ← future

Each record::

    {
      "run_id":   str,
      "level":    str,           # "session" | "agent:<id>"
      "field":    str,           # "input" | "output"
      "model":    str,
      "dim":      int,
      "vector":   list[float],
      "computed_at": str,
    }

Separate files per (level, field, model) keep the dataset independently
expandable: new embedding models, new fields, new agents — without touching
existing files.

Config keys::

    model: str            embedding model  (default: "text-embedding-3-small")
    api_base: str         OpenAI-compatible endpoint
    api_key: SecretRef    secret for the API key; without one, {secret: openai} is used for
                          the OpenAI endpoint only, another api_base needs its own
    levels: list[str]     which levels to embed (default: ["session"])
    fields: list[str]     which fields to embed (default: ["input", "output"])
    max_items: int | null cap (default: null = all)
    overwrite: bool       re-embed already-present run_ids (default: false)
    batch_size: int       number of texts per API call (default: 64)

Note: This step is a **scaffold** — the embedding call is implemented but the
downstream semantic-grouping analysis (nearest-neighbour, cluster consistency,
evaluation reuse) lives in notebooks / analysis steps, not here.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mas.lab.benchmark.pipeline import PipelineStep, StepOutput
from mas.lab.benchmark.pipeline.executor import ExecutionContext

logger = logging.getLogger(__name__)


class EmbedStep(PipelineStep):
    """Compute embeddings for trajectory text fields and store as JSONL."""

    type = "embed_trajectories"

    async def execute(self, ctx: ExecutionContext) -> StepOutput:
        bench_dir = ctx.output_dir
        traj_path = bench_dir / "trajectories.jsonl"

        if not traj_path.exists():
            logger.error("trajectories.jsonl not found — run extract_trajectories first")
            return StepOutput(metadata={"embedded": 0})

        model = self.config.get("model", "text-embedding-3-small")
        api_base = self.config.get("api_base", "")
        from mas.library.standard.lib.openai_client import reveal_openai_key

        levels = self.config.get("levels", ["session"])
        fields = self.config.get("fields", ["input", "output"])
        max_items: int | None = self.config.get("max_items")
        overwrite = self.config.get("overwrite", False)
        batch_size = int(self.config.get("batch_size", 64))

        api_key = None

        embeddings_dir = bench_dir / "embeddings"
        embeddings_dir.mkdir(parents=True, exist_ok=True)

        # Load trajectories
        trajectories: list[dict[str, Any]] = []
        for line in traj_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    trajectories.append(json.loads(line))
                except json.JSONDecodeError:
                    logger.debug("suppressed", exc_info=True)

        if max_items is not None:
            trajectories = trajectories[:max_items]

        total_embedded = 0
        output_files: list[Path] = []

        for level in levels:
            for field in fields:
                file_key = f"{level}__{field}".replace(":", "__")
                out_path = embeddings_dir / f"{file_key}.jsonl"
                output_files.append(out_path)

                # Skip already-embedded run_ids
                existing_ids: set[str] = set()
                if out_path.exists() and not overwrite:
                    for line in out_path.read_text(encoding="utf-8").splitlines():
                        if line.strip():
                            try:
                                existing_ids.add(json.loads(line).get("run_id", ""))
                            except json.JSONDecodeError:
                                logger.debug("suppressed", exc_info=True)

                to_embed = [
                    t
                    for t in trajectories
                    if t.get("run_id") and t["run_id"] not in existing_ids and not t.get("_extraction_error")
                ]

                if not to_embed:
                    logger.info("EmbedStep: %s/%s — all %d already embedded", level, field, len(existing_ids))
                    continue

                texts = [_extract_text(t, level, field) for t in to_embed]
                run_ids = [t["run_id"] for t in to_embed]

                logger.info("EmbedStep: embedding %d texts for %s/%s", len(texts), level, field)
                if api_key is None:
                    api_key = reveal_openai_key(self.config.get("api_key"), api_base)
                vectors = _embed_batch(texts, model=model, api_base=api_base, api_key=api_key, batch_size=batch_size)

                ts = datetime.now(timezone.utc).isoformat()
                with open(out_path, "a", encoding="utf-8") as out_f:
                    for run_id, vector in zip(run_ids, vectors, strict=False):
                        if vector is None:
                            continue
                        rec = {
                            "run_id": run_id,
                            "level": level,
                            "field": field,
                            "model": model,
                            "dim": len(vector),
                            "vector": vector,
                            "computed_at": ts,
                        }
                        out_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                        total_embedded += 1

        logger.info("EmbedStep done: %d vectors written", total_embedded)
        return StepOutput(
            data={"embeddings_dir": str(embeddings_dir)},
            files=output_files,
            metadata={"embedded": total_embedded},
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_text(trajectory: dict[str, Any], level: str, field: str) -> str:
    if level == "session":
        session = trajectory.get("session", {})
        if field == "input":
            return session.get("input", trajectory.get("prompt", ""))
        if field == "output":
            return session.get("output", "")
        if field in ("trajectory", "trajectory_concat"):
            # T-concat: sequence of agent IDs + tool names from LLM call records.
            calls: list[dict[str, Any]] = trajectory.get("calls", [])
            tokens: list[str] = []
            for c in calls:
                agent = c.get("agent_id", "")
                tools: list[str] = c.get("tool_calls") or []
                tokens.append(agent)
                for tc in tools:
                    tokens.append(f"{agent}:{tc}")
            return " ".join(tokens)
        return session.get("output", "")

    if level.startswith("agent:"):
        agent_id = level.split(":", 1)[1]
        agent = trajectory.get("agents", {}).get(agent_id, {})
        if field == "input":
            return agent.get("first_user_message", "")
        return agent.get("final_output", "")

    return ""


def _embed_batch(
    texts: list[str],
    model: str,
    api_base: str,
    api_key: str,
    batch_size: int,
) -> list[list[float] | None]:
    """Call the embedding endpoint in batches.  Returns one vector per text."""
    from mas.library.standard.lib.openai_client import build_openai_client

    try:
        client = build_openai_client(api_key, api_base)
    except ImportError:
        logger.error("openai package required for EmbedStep")
        return [None] * len(texts)

    vectors: list[list[float] | None] = []
    for i in range(0, len(texts), batch_size):
        batch = [t or "" for t in texts[i : i + batch_size]]
        try:
            response = client.embeddings.create(model=model, input=batch)
            vectors.extend(item.embedding for item in response.data)
        except Exception as exc:
            from mas.runtime.reliability.classify import error_summary

            logger.warning("Embedding batch %d failed: %s", i // batch_size, error_summary(exc))
            vectors.extend([None] * len(batch))

    return vectors
