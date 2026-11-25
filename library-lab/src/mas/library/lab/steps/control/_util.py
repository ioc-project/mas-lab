#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Shared upstream lookups for control pipeline steps."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mas.lab.benchmark.execution.roles import PlannedRun
from mas.lab.benchmark.pipeline import PipelineStep
from mas.lab.benchmark.pipeline.executor import ExecutionContext


def under(base: Path | str | None, raw: str | Path) -> Path:
    """``raw`` as is when absolute, else relative to ``base``."""
    path = Path(str(raw))
    return path if path.is_absolute() or base is None else Path(base) / path


def resolve_rel(ctx: ExecutionContext, raw: str | Path) -> Path:
    """A path in the pipeline file, relative to that file."""
    config_path = ctx.pipeline.config_path
    return under(config_path.parent if config_path else None, raw)


def item_user_prompt(item: Any) -> str:
    """User prompt text on a dataset item (envelope or legacy flat keys)."""
    if not isinstance(item, dict):
        return ""
    inputs = item.get("inputs") if isinstance(item.get("inputs"), dict) else {}
    user = inputs.get("user")
    if isinstance(user, str) and user.strip():
        return user
    if isinstance(user, list) and user:
        first = user[0]
        if isinstance(first, str) and first.strip():
            return first
        if isinstance(first, dict):
            content = first.get("content")
            if isinstance(content, str) and content.strip():
                return content
    for key in ("prompt", "query", "user_prompt"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


@dataclass(frozen=True)
class Scores:
    """Scores by scenario id and by item id, kept apart: an item id may equal a scenario id."""

    scenarios: dict[str, float]
    items: dict[str, float]


def _mean(rows: dict[str, list[float]]) -> dict[str, float]:
    return {key: sum(values) / len(values) for key, values in rows.items()}


def load_scores(step: PipelineStep, ctx: ExecutionContext) -> Scores:
    """Scores from ``scores_path`` (a JSON object) or from upstream tables.

    A JSON object names scenarios and items by one id space, so it fills both. A table
    row scores its scenario and its item; several rows of one scenario or item average.
    """
    raw_path = step.config.get("scores_path")
    if raw_path:
        data = json.loads(resolve_rel(ctx, str(raw_path)).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"step {step.name!r}: scores_path must be a JSON object")
        flat = {str(k): float(v) for k, v in data.items()}
        return Scores(dict(flat), dict(flat))
    score_key = str(step.config.get("score_key") or "score")
    by_scenario: dict[str, list[float]] = {}
    by_item: dict[str, list[float]] = {}
    direct: dict[str, float] = {}
    for data in _dep_payloads(ctx, step):
        scores = data.get("scores")
        if isinstance(scores, dict):
            direct.update({str(k): float(v) for k, v in scores.items()})
            continue
        rows = data.get("rows") or data.get("records") or []
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict) or score_key not in row:
                continue
            value = float(row[score_key])
            iid = row.get("item_id") if row.get("item_id") is not None else row.get("item")
            sid = row.get("scenario") or row.get("scenario_id")
            if iid is not None:
                by_item.setdefault(str(iid), []).append(value)
            if sid is not None:
                by_scenario.setdefault(str(sid), []).append(value)
    scenarios, items = _mean(by_scenario), _mean(by_item)
    scenarios.update(direct)
    items.update(direct)
    return Scores(scenarios, items)


def upstream_runs(ctx: ExecutionContext, step: PipelineStep) -> list[PlannedRun]:
    """Runs an upstream step produced, as :class:`PlannedRun` or as their ``run_rows`` form."""
    for data in _dep_payloads(ctx, step):
        for key in ("runs", "run_rows"):
            rows = data.get(key)
            if isinstance(rows, list) and rows:
                runs = [
                    r if isinstance(r, PlannedRun) else PlannedRun.from_row(r)
                    for r in rows
                    if isinstance(r, (PlannedRun, dict))
                ]
                if runs:
                    return runs
    return []


def upstream(ctx: ExecutionContext, step: PipelineStep, *keys: str) -> Any:
    """The first non-empty value of any of ``keys`` in the outputs of the step's dependencies."""
    for data in _dep_payloads(ctx, step):
        for key in keys:
            if data.get(key):
                return data[key]
    return None


def setting(ctx: ExecutionContext, step: PipelineStep, key: str, *upstream_keys: str) -> str:
    """``step.config[key]``, else an upstream value (of ``upstream_keys``, default ``key``), else ``""``."""
    return str(step.config.get(key) or upstream(ctx, step, *(upstream_keys or (key,))) or "")


def upstream_prompts(ctx: ExecutionContext, step: PipelineStep) -> list[dict[str, Any]]:
    prompts = upstream(ctx, step, "prompts")
    return [dict(p) for p in prompts if isinstance(p, dict)] if isinstance(prompts, list) else []


def _dep_payloads(ctx: ExecutionContext, step: PipelineStep) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for dep in step.depends_on:
        payload = ctx.step_outputs.get(dep)
        if payload is None:
            continue
        data = payload.data or {}
        if isinstance(data, dict):
            out.append(data)
    return out


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
MAX_PROMPT_CHARS = 8000


def safe_item_id(raw: Any, fallback: str, taken: set[str]) -> str:
    """An id fit to be a folder name and unique among ``taken``.

    A judge or any model output may name an item. Anything that is not a short plain token
    (a path separator, ``..``, spaces) is replaced by ``fallback`` rather than trusted.
    """
    candidate = str(raw) if raw is not None else ""
    if not _SAFE_ID.match(candidate) or ".." in candidate:
        candidate = fallback
    unique, n = candidate, 2
    while unique in taken:
        unique = f"{candidate}-{n}"
        n += 1
    taken.add(unique)
    return unique
