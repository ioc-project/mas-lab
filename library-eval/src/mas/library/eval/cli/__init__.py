#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""mas-lab eval — library-eval CLI (same runner as the eval_mce pipeline step).

Usage::

    mas-lab eval path/to/events.jsonl --metric goal_success_rate --json
    mas-lab eval path/to/experiment/ --metric goal_success_rate --recursive
    mas-lab eval --list-metrics
"""
from __future__ import annotations

import json as _json
from pathlib import Path
from typing import Optional

import click

from mas.library.eval.mce.catalog import listed_ids, resolve_metric
from mas.library.eval.mce.runner import (
    compute_session_metrics,
    install_openai_llm_service,
)

_DEFAULT_METRICS = ["goal_success_rate"]


@click.command("eval")
@click.argument("source", type=str, metavar="SOURCE", required=False)
@click.option(
    "--fixture",
    type=Path,
    default=None,
    help="Path to a fixture YAML that provides ground truth.",
)
@click.option(
    "--metric",
    "metrics",
    multiple=True,
    help=(
        "MCE metric(s) to compute (repeatable). Snake_case ids only "
        f"(goal_success_rate). Defaults to {_DEFAULT_METRICS} when omitted. "
        "Use --list-metrics to show the available metrics."
    ),
)
@click.option(
    "--response-agent",
    default=None,
    show_default="auto-detect from trace",
    help="agent_id whose last execution_end is the final response. Auto-detected when omitted.",
)
@click.option(
    "--model",
    default=None,
    show_default="workspace infra default model, else gpt-4o",
    help="Judge model. Default: workspace infra default, else gpt-4o.",
)
@click.option(
    "--list-metrics",
    is_flag=True,
    default=False,
    help="List the available metrics and exit.",
)
@click.option(
    "--json",
    "output_json",
    is_flag=True,
    default=False,
    help="Output results as JSON instead of a human-readable table.",
)
@click.option("--overlay", default=None, help="Label for display (e.g. inject-C7-backend).")
@click.option(
    "--engine",
    type=click.Choice(["mce-v1"]),
    default="mce-v1",
    show_default=True,
    help="Metric computation engine: mce-v1.",
)
@click.option(
    "--api-key-env",
    default=None,
    help="Name of the environment variable holding the API key (default: OPENAI_API_KEY).",
)
@click.option(
    "--recursive",
    is_flag=True,
    default=False,
    help="Score every events.jsonl under SOURCE when SOURCE is a directory.",
)
def eval_cmd(
    source: Optional[str],
    fixture: Optional[Path],
    metrics: tuple[str, ...],
    response_agent: str,
    model: Optional[str],
    output_json: bool,
    overlay: Optional[str],
    engine: str,
    api_key_env: Optional[str],
    list_metrics: bool,
    recursive: bool,
) -> None:
    """Score traces with MCE LLM-as-judge metrics (same runner as eval_mce).

    SOURCE can be a file path (events.jsonl), a directory (with --recursive),
    a lab shorthand (e.g. tutorials/t3-analysis/baseline/item1/r1), or a run_id.

    \b
    Examples:
      mas-lab eval path/to/events.jsonl --metric goal_success_rate --json
      mas-lab eval tutorials/t3-analysis/baseline/item1/r1 --metric groundedness
      mas-lab eval --list-metrics
    """
    del engine  # reserved; only mce-v1 is wired
    if list_metrics:
        click.echo("Available metrics (snake_case):")
        for snake in listed_ids(session_only=True):
            click.echo(f"  {snake}")
        return
    if not source:
        raise click.UsageError("SOURCE argument required (or use --list-metrics)")

    try:
        metric_names = [
            resolve_metric(n, session_only=True).snake for n in (metrics or _DEFAULT_METRICS)
        ]
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc

    from mas.lab.cli.commands.plot import resolve_source

    resolved = Path(resolve_source(source)).expanduser()
    if not resolved.exists():
        click.echo(f"Source not found: {source}")
        raise SystemExit(1)

    traces: list[Path]
    if resolved.is_dir():
        if not recursive:
            raise click.UsageError("SOURCE is a directory; pass --recursive to score every events.jsonl")
        traces = sorted(resolved.rglob("events.jsonl"))
        if not traces:
            click.echo(f"No events.jsonl under {resolved}")
            raise SystemExit(1)
    else:
        traces = [resolved]

    install_openai_llm_service(model, api_key_env=api_key_env)

    if fixture:
        if not fixture.exists():
            click.echo(f"Fixture not found: {fixture}")
            raise SystemExit(1)
        click.echo("ℹ️  --fixture provided but not consumed by current MCE metrics.", err=True)

    any_results = False
    for trace_path in traces:
        results_dict = compute_session_metrics(
            trace_path,
            metric_names,
            response_agent_id=response_agent,
        )
        if not results_dict:
            click.echo(f"No results for {trace_path} — check trace content.", err=True)
            continue
        any_results = True
        label = overlay or (
            str(trace_path.relative_to(resolved)) if resolved.is_dir() else trace_path.stem
        )
        if output_json:
            payload = {k: v for k, v in results_dict.items() if not k.startswith("__")}
            if resolved.is_dir():
                click.echo(_json.dumps({"source": label, "metrics": payload}, indent=2))
            else:
                click.echo(_json.dumps(payload, indent=2))
        else:
            _print_table(results_dict, label)
    if not any_results:
        raise SystemExit(1)


def _print_table(results_dict: dict, label: str) -> None:
    click.echo(f"\n{'─' * 72}")
    click.echo(f"  eval — {label}")
    click.echo(f"{'─' * 72}")
    click.echo(f"  {'Metric':<30}  {'Score':>6}  Reasoning")
    click.echo(f"  {'─' * 30}  {'─' * 6}  {'─' * 30}")
    for name, result in results_dict.items():
        if name.startswith("__"):
            continue
        value = result.get("value")
        error = result.get("error")
        reasoning = (result.get("reasoning") or "").replace("\n", " ")[:60]
        score_str = f"{value:.3f}" if value is not None and not error else "ERR"
        click.echo(f"  {name:<30}  {score_str:>6}  {reasoning}")
    click.echo(f"{'─' * 72}\n")


class EvalCliComponent:
    """Register ``mas-lab eval`` via ``mas.lab.cli.components``."""

    def register(self, app: click.Group) -> str:
        app.add_command(eval_cmd, name="eval")
        return "eval"
