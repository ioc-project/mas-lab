#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``mas-lab dataset`` — post-processing pipeline entry points.

Post-processing is now driven by **pipeline steps** in
``mas.library.lab.steps``:

  ExtractTrajectoriesStep  (type: extract_trajectories)
      Reads run CSV + events.jsonl traces → ``trajectories.jsonl``

  AnnotateMetricsStep      (type: annotate_metrics)
      Reads trajectories → ``metrics/answer_relevancy.jsonl``

  EmbedStep                (type: embed_trajectories)
      Reads trajectories → ``embeddings/session__input.jsonl`` etc.

Add these as steps in your experiment YAML pipeline block and run:

    mas-lab benchmark run <experiment.yaml>
"""
from __future__ import annotations

from pathlib import Path

import click


@click.group("dataset")
def dataset_cmd() -> None:
    """Post-process benchmark run traces (extract I/O, evaluate relevancy)."""


# ---------------------------------------------------------------------------
# dataset extract
# ---------------------------------------------------------------------------

@dataset_cmd.command("extract")
@click.argument("item_dir", type=Path, metavar="ITEM_DIR")
@click.option("-o", "--output", type=Path, default=None,
              help="Output JSON path (default: <item_dir>/extracted.json).")
def extract_cmd(item_dir: Path, output: Path | None) -> None:
    """Extract multi-level I/O from a benchmark run item directory.

    ITEM_DIR must contain a run folder produced by ``mas-lab benchmark run``
    (i.e. a sub-tree with traces/events.jsonl).
    Produces extracted.json with session / agent / call-level inputs and
    outputs, filtering out system messages.
    """
    from mas.lab.dataset.extractor import TrajectoryExtractor

    extractor = TrajectoryExtractor(item_dir)
    result = extractor.extract()
    dest = output or item_dir / "extracted.json"
    import json
    dest.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    click.echo(f"Extracted to {dest}")


# ---------------------------------------------------------------------------
# dataset eval
# ---------------------------------------------------------------------------

@dataset_cmd.command("eval")
@click.argument("item_dir", type=Path, metavar="ITEM_DIR")
@click.option("--api-base", required=True, envvar="LITELLM_BASE_URL",
              help="OpenAI-compatible endpoint (or $LITELLM_BASE_URL).")
@click.option("--api-key", default="$OPENAI_API_KEY",
              help="API key or env-var reference (default: $OPENAI_API_KEY).")
@click.option("--model", default="openai/gpt-4o-mini", show_default=True,
              help="Judge model identifier.")
@click.option("--no-write", is_flag=True, default=False,
              help="Print report to stdout instead of writing eval.json.")
def eval_cmd(
    item_dir: Path,
    api_base: str,
    api_key: str,
    model: str,
    no_write: bool,
) -> None:
    """Score answer relevancy for every I/O level in extracted.json.

    Requires extracted.json to exist (run ``mas-lab dataset extract`` first).
    Produces eval.json with a 0–1 relevancy score and reasoning per level.
    """
    import json

    from mas.lab.dataset.evaluator import AnswerRelevancyEvaluator

    evaluator = AnswerRelevancyEvaluator(
        api_base=api_base,
        api_key=api_key,
        model=model,
    )
    report = evaluator.evaluate(item_dir, write=not no_write)

    if no_write:
        click.echo(json.dumps(report, indent=2))
    else:
        click.echo(f"Evaluation written to {item_dir / 'eval.json'}")
