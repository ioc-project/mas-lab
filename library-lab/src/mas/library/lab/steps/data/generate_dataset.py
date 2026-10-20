#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations
"""GenerateDatasetStep — pre-phase pipeline step that generates a benchmark
dataset from the MAS agent's stated intent by calling an LLM.

Execution note: this step runs before any runs start (its output feeds
subsequent steps via ``depends_on``).
The generated dataset is written **before** the benchmark loop runs so that
the CLI can use it as the experiment dataset.

Storage layout::

    output/benchmark/
      generated_dataset.yaml          ← written by this step

Output file format::

    items:
      - id: 1
        prompt: '...'
      - id: 2
        prompt: '...'

Config keys::

    intent_source: str        Path (relative to pipeline YAML) to an agent/MAS
                              config YAML.  The system prompt is extracted from
                              ``spec.agents[0].system_prompt`` or, as fallback,
                              ``spec.system_prompt`` / ``metadata.description``.
    n_items: int              Number of questions to generate (default: 5).
    topic_hint: str           Optional hint appended to the generation prompt
                              to steer the question domain.
    model: str                OpenAI-compatible model (default: gpt-4o-mini).
    api_base: str             Optional proxy URL (default: OPENAI_BASE_URL env).
    api_key_env: str          Env-var name for the API key
                              (default: OPENAI_API_KEY).
    output_filename: str      Output filename (default: generated_dataset.yaml).
    temperature: float        LLM temperature (default: 0.8).
"""

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

import yaml

from mas.lab.benchmark.pipeline.artifact_paths import resolve_step_artifact_path
from mas.lab.benchmark.pipeline import PipelineStep, StepOutput
from mas.lab.benchmark.pipeline.executor import ExecutionContext
from mas.runtime.engine.llm_output_limits import output_token_kwargs

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_GENERATION_SYSTEM = (
    "You are an evaluation dataset designer for AI systems. "
    "Given the purpose and description of an AI assistant, you generate "
    "realistic, diverse test questions that users might ask it. "
    "Questions must be clear, self-contained, and cover different aspects of the domain."
)

_GENERATION_USER_TMPL = """\
The AI assistant you are evaluating has the following purpose:

---
{system_prompt}
---

{topic_hint_block}
Generate exactly {n_items} distinct test questions that a user might send to this assistant.

Return a JSON array of strings — one question per element, no preamble, no trailing text.

Example output format:
["Question 1?", "Question 2?", "Question 3?"]
"""


def _extract_system_prompt(source_path: Path) -> str:
    """Extract the agent's system prompt from a YAML config."""
    with open(source_path) as fh:
        data = yaml.safe_load(fh)

    spec = data.get("spec") or {}
    context = spec.get("context") or {}
    if isinstance(context, dict):
        role = context.get("role")
        if isinstance(role, str) and role.strip():
            return role.strip()

    agents = spec.get("agents") or []
    if agents and isinstance(agents, list):
        first = agents[0] if isinstance(agents[0], dict) else {}
        first_ctx = first.get("context") or {}
        if isinstance(first_ctx, dict):
            role = first_ctx.get("role")
            if isinstance(role, str) and role.strip():
                return role.strip()

    desc = spec.get("description")
    if isinstance(desc, str) and desc.strip():
        return desc.strip()

    meta = data.get("metadata") or {}
    if isinstance(meta.get("description"), str) and meta["description"].strip():
        return meta["description"].strip()

    return data.get("name") or "A general-purpose AI assistant."


def _call_llm(
    prompt_text: str,
    model: str,
    api_base: Optional[str],
    api_key: str,
    temperature: float,
) -> str:
    """Call the LLM and return the raw content string."""
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("openai package is required for GenerateDatasetStep") from exc

    client_kwargs: dict[str, Any] = {"api_key": api_key}
    if api_base:
        client_kwargs["base_url"] = api_base

    client = OpenAI(**client_kwargs)
    response = client.chat.completions.create(
        model=model,
        temperature=temperature,
        messages=[
            {"role": "system", "content": _GENERATION_SYSTEM},
            {"role": "user", "content": prompt_text},
        ],
        **output_token_kwargs(model),
    )
    return response.choices[0].message.content or ""


def _parse_questions(raw: str, n_items: int) -> list[str]:
    """Parse the LLM response into a list of question strings."""
    raw = raw.strip()

    # Try JSON array first
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(q) for q in parsed[:n_items]]
    except json.JSONDecodeError:
        logger.debug('suppressed', exc_info=True)

    # Fallback: split on newlines, strip bullets/numbers
    lines = [ln.strip().lstrip("0123456789.-) ").strip() for ln in raw.splitlines()]
    questions = [ln for ln in lines if ln and not ln.startswith("#")]
    return questions[:n_items]


# ---------------------------------------------------------------------------
# Step
# ---------------------------------------------------------------------------


class GenerateDatasetStep(PipelineStep):
    """Generate a benchmark dataset from the MAS agent's stated intent.

    This step should run before any run-producing steps.
    Chain it with ``depends_on`` from the downstream step.
    The CLI will execute it before the benchmark loop and feed the generated
    dataset to the runner.
    """

    type = "generate_dataset"

    async def execute(self, ctx: ExecutionContext) -> StepOutput:  # noqa: C901
        cfg = self.config

        # ── Config ────────────────────────────────────────────────────────
        n_items: int = int(cfg.get("n_items", 5))
        model: str = cfg.get("model", "gpt-4o-mini")
        topic_hint: str = cfg.get("topic_hint", "")
        api_base: Optional[str] = cfg.get("api_base") or os.environ.get("OPENAI_BASE_URL") or os.environ.get("OPENAI_API_BASE")
        api_key_env: str = cfg.get("api_key_env", "OPENAI_API_KEY")
        api_key: str = os.environ.get(api_key_env, "")
        temperature: float = float(cfg.get("temperature", 0.8))
        output_filename: str = str(cfg.get("output_filename") or "generated_dataset.yaml")

        if not api_key:
            logger.warning(
                "GenerateDatasetStep: %s not set — LLM call will likely fail",
                api_key_env,
            )

        # ── Resolve intent_source ──────────────────────────────────────────
        intent_source_raw: Optional[str] = cfg.get("intent_source")
        system_prompt: str = ""

        if intent_source_raw:
            # Resolve relative to the pipeline config file if available
            base = (
                ctx.pipeline.config_path.parent
                if (ctx.pipeline.config_path and ctx.pipeline.config_path.exists())
                else Path.cwd()
            )
            intent_path = (base / intent_source_raw).resolve()

            if intent_path.exists():
                try:
                    system_prompt = _extract_system_prompt(intent_path)
                    logger.info(
                        "GenerateDatasetStep: extracted system prompt from %s (%d chars)",
                        intent_path,
                        len(system_prompt),
                    )
                except Exception as exc:
                    logger.warning(
                        "GenerateDatasetStep: failed to parse %s: %s", intent_path, exc
                    )
            else:
                logger.warning(
                    "GenerateDatasetStep: intent_source not found: %s", intent_path
                )

        if not system_prompt:
            system_prompt = "A general-purpose AI question-answering assistant."
            logger.warning(
                "GenerateDatasetStep: no system prompt resolved — using generic fallback"
            )

        # ── Build generation prompt ────────────────────────────────────────
        topic_hint_block = (
            f"Additional constraint: {topic_hint}\n" if topic_hint else ""
        )
        generation_prompt = _GENERATION_USER_TMPL.format(
            system_prompt=system_prompt,
            topic_hint_block=topic_hint_block,
            n_items=n_items,
        )

        # ── Call LLM ──────────────────────────────────────────────────────
        logger.info(
            "GenerateDatasetStep: generating %d questions with model=%s", n_items, model
        )
        try:
            raw_response = _call_llm(generation_prompt, model, api_base, api_key, temperature)
            questions = _parse_questions(raw_response, n_items)
        except Exception as exc:
            logger.error("GenerateDatasetStep: LLM generation failed: %s", exc)
            return StepOutput(
                data={"error": str(exc)},
                metadata={"n_items": n_items, "model": model, "success": False},
            )

        if not questions:
            logger.error("GenerateDatasetStep: no questions could be parsed from LLM response")
            return StepOutput(
                data={"error": "empty question list after parsing"},
                metadata={"success": False},
            )

        logger.info("GenerateDatasetStep: generated %d questions", len(questions))

        # ── Write output ───────────────────────────────────────────────────
        items = [
            {
                "id": i + 1,
                "inputs": {"user": q},
            }
            for i, q in enumerate(questions)
        ]
        dataset = {
            "apiVersion": "lab/v1",
            "kind": "Dataset",
            "metadata": {"name": Path(output_filename).stem, "version": "v1"},
            "spec": {"items": items},
        }

        out_path: Path = resolve_step_artifact_path(
            ctx, None, "generate_dataset", "generated_dataset.yaml", cfg,
            key="output_filename", name_keys=(), mkdir=True,
        )
        with open(out_path, "w") as fh:
            yaml.dump(dataset, fh, allow_unicode=True, default_flow_style=False, sort_keys=False, width=120)

        logger.info("GenerateDatasetStep: wrote %s", out_path)

        return StepOutput(
            data={"dataset_path": str(out_path), "items": items},
            files=[out_path],
            metadata={"n_items": len(items), "model": model, "success": True},
        )
