#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Lab tree and pipeline DAG rendering for benchmark show commands."""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _render_exp_tree(
    exp_yaml: "Path",
    verbose: int,
    depth: int,
    scenario_filter: "str | None",
    item_filter: "str | None",
    run_filter: "int | None",
    prefix: str,
    child_prefix: str,
) -> None:
    """Render one experiment subtree."""
    import json as _json

    from mas.lab.lab.config import MASExperimentConfig

    # Load experiment config
    try:
        exp = MASExperimentConfig.from_yaml(exp_yaml)
    except Exception as e:
        print(f"{prefix}{exp_yaml.parent.name}/  [config error: {e}]")
        return

    exp_dir_name = exp_yaml.parent.name
    data_dir: "Path | None" = getattr(exp, "output_dir", None)

    def _has_results(d: "Path | None") -> bool:
        if d is None or not d.exists():
            return False
        csv = d / "results.csv"
        if not csv.exists():
            return False
        # A header-only CSV is ~100 bytes; require at least one data row.
        if csv.stat().st_size < 150:
            return False
        try:
            import pandas as _pd0
            return len(_pd0.read_csv(csv)) > 0
        except Exception:
            return csv.stat().st_size > 150

    # Resolve status / row count
    if data_dir and data_dir.exists():
        results_csv = data_dir / "results.csv"
        if results_csv.exists():
            try:
                import pandas as _pd
                n_rows = len(_pd.read_csv(results_csv))
                status_str = f"✓  {n_rows} rows"
            except Exception:
                status_str = "✓  (results.csv present)"
        else:
            status_str = "⚑  no results.csv"
    else:
        status_str = "○  not run"

    print(f"{prefix}{exp_dir_name}/  [{exp.name}]  {status_str}")

    if data_dir is None or not data_dir.exists():
        return

    # Reserved top-level dir names that are not scenarios
    _NON_SCENARIO_DIRS = {"results", "plots", "runs", "otel", "traces"}

    # Collect scenario directories (skip hidden + reserved names)
    scenarios = sorted(
        d for d in data_dir.iterdir()
        if d.is_dir() and not d.name.startswith(".") and d.name not in _NON_SCENARIO_DIRS
    )
    if scenario_filter:
        scenarios = [s for s in scenarios if scenario_filter in s.name]

    # Sections to render: scenarios + optional artifacts + optional pipeline DAG
    _has_pipeline = verbose >= 3 and bool(getattr(exp, "pipeline", []))

    if depth >= 2:
        for j, sc_dir in enumerate(scenarios):
            is_last_sc = (j == len(scenarios) - 1) and not _has_pipeline
            sc_prefix = child_prefix + ("└── " if is_last_sc else "├── ")
            sc_child = child_prefix + ("    " if is_last_sc else "│   ")

            # Collect item directories
            items = sorted(
                d for d in sc_dir.iterdir()
                if d.is_dir() and not d.name.startswith(".")
            )
            if item_filter:
                items = [it for it in items if item_filter in it.name]

            # Summarise runs for compact display
            item_summaries = []
            for it_dir in items:
                runs = sorted(
                    d for d in it_dir.iterdir()
                    if d.is_dir() and d.name.startswith("r")
                )
                if run_filter is not None:
                    runs = [r for r in runs if r.name == f"r{run_filter}"]

                statuses = []
                for r in runs:
                    metrics_path = r / "metrics.json"
                    if metrics_path.exists():
                        try:
                            info = _json.loads(metrics_path.read_text())
                            rq = info.get("run_quality", {})
                            s = rq.get("status", "?")
                            statuses.append("✓" if s == "ok" else "✗")
                        except Exception:
                            statuses.append("?")
                    elif (r / "run_info.json").exists() or (r / ".run_ref").exists():
                        statuses.append("✓")
                    else:
                        statuses.append("○")

                # Strip "item" prefix for display
                name = it_dir.name
                if name.startswith("item"):
                    name = name[4:]
                item_summaries.append((name, statuses))

            n_items = len(item_summaries)
            n_runs = max((len(s) for _, s in item_summaries), default=0)
            print(f"{sc_prefix}{sc_dir.name}/  ({n_items} items × {n_runs} runs)")

            # Expand items only when explicitly requested (filter or --depth item/run)
            if item_filter or run_filter is not None or depth >= 3:
                for k, (it_name, run_statuses) in enumerate(item_summaries):
                    is_last_it = k == len(item_summaries) - 1
                    it_prefix = sc_child + ("└── " if is_last_it else "├── ")
                    runs_str = "  ".join(
                        f"r{ri + 1}{s}" for ri, s in enumerate(run_statuses)
                    )
                    print(f"{it_prefix}{it_name}  {runs_str}")

    # Pipeline DAG section (verbose >= 3)
    if _has_pipeline:
        _render_pipeline_dag(
            steps=exp.pipeline,
            data_dir=data_dir,
            prefix=child_prefix + "└── ",
            child_prefix=child_prefix + "    ",
        )


# ---------------------------------------------------------------------------
# _render_pipeline_dag — show pipeline step DAG with artifact lineage (-vvv)
# ---------------------------------------------------------------------------

# Artifact descriptors:
#   inputs:  (label, materialized, glob_pattern_for_count | None)
#   outputs: (label, materialized, config_key | None, default_template | None, glob_pat | None)
_PIPELINE_ARTIFACT_SCHEMA: dict = {
    "eval_mce": {
        "inputs":  [("events.jsonl per run", False, None)],
        "outputs": [("metrics.json per run", True, None, None, "**/metrics.json")],
    },
    "eval_mce_batch": {
        "inputs":  [("events.jsonl per run", False, None)],
        "outputs": [("metrics.json per run", True, None, None, "**/metrics.json")],
    },
    "eval_batch": {
        "inputs":  [("events.jsonl per run", False, None)],
        "outputs": [("metrics.json per run", True, None, None, "**/metrics.json")],
    },
    "collect_metrics": {
        "inputs":  [("metrics.json per run", True, "**/metrics.json")],
        "outputs": [("results.csv", True, "output", "results.csv", None)],
    },
    "to_dataframe": {
        "inputs":  [("metrics.json per run", True, "**/metrics.json")],
        "outputs": [("data.csv", True, "output", "data.csv", None)],
    },
    "join_dataframe": {
        "inputs":  [("data.csv", True, None)],
        "outputs": [("joined.csv", True, "output", "joined.csv", None)],
    },
    "plotnine": {
        "inputs":  [("results.csv", True, None)],
        "outputs": [("figure", True, "output", None, None)],
    },
    "metrics_comparison_plot": {
        "inputs":  [("results.csv", True, None)],
        "outputs": [("comparison.png", True, "output", "topology-comparison.png", None)],
    },
}


def _render_pipeline_dag(
    steps: list,
    data_dir: "Path",
    prefix: str,
    child_prefix: str,
) -> None:
    """Render the pipeline DAG with artifact lineage at -vvv verbosity.

    Legend:
      ●  materialized artifact (written to disk / already exists)
      ○  virtual artifact (in-memory / not directly persisted by this step)
      ←  input consumed by step
      →  output produced by step
    """

    def _subst(s: str) -> str:
        # configured / default locations are relative to the level folder
        s = s.replace("{output_dir}/", "").replace("{output_dir}", ".")
        return str(Path(data_dir) / s)

    def _fmt_path(p: "Path") -> str:
        try:
            return "~/" + str(p.relative_to(Path.home()))
        except ValueError:
            return str(p)

    def _file_info(p: "Path") -> str:
        if not p.exists():
            return "missing"
        size_kb = p.stat().st_size / 1024
        if p.suffix == ".csv" and size_kb > 0:
            try:
                import pandas as _pd3
                n = len(_pd3.read_csv(p))
                return f"{n} rows, {size_kb:.1f} KB"
            except Exception:
                logger.debug('suppressed', exc_info=True)
        return f"{size_kb:.1f} KB"

    print(f"{prefix}[pipeline]  legend: ● materialized  ○ virtual  ← in  → out")

    for i, step in enumerate(steps):
        is_last = i == len(steps) - 1
        step_pfx   = child_prefix + ("└── " if is_last else "├── ")
        step_child = child_prefix + ("    " if is_last else "│   ")

        step_name  = step.name or step.type
        scope_note = f"  scope:{step.scope}" if getattr(step, "scope", "") else (
            "  per-scenario" if getattr(step, "per_scenario", False) else ""
        )
        deps_note  = f"  ← {', '.join(step.depends_on)}" if getattr(step, "depends_on", []) else ""
        print(f"{step_pfx}{step_name}  [{step.type}]{scope_note}{deps_note}")

        schema  = _PIPELINE_ARTIFACT_SCHEMA.get(step.type, {})
        cfg     = getattr(step, "config", {}) or {}
        lines: list[str] = []

        # Inputs
        for label, materialized, glob_pat in schema.get("inputs", []):
            sym  = "●" if materialized else "○"
            note = "" if materialized else "  (virtual)"
            if glob_pat and data_dir:
                count     = sum(1 for _ in data_dir.rglob(glob_pat))
                count_str = f"  ×{count}" if count else ""
            else:
                count_str = ""
            lines.append(f"← {sym}  {label}{count_str}{note}")

        # Outputs
        for label, materialized, config_key, default_tmpl, glob_pat in schema.get("outputs", []):
            sym = "●" if materialized else "○"

            # Resolve actual path: config key > default template > glob
            actual: "Path | None" = None
            if config_key and config_key in cfg:
                raw = str(cfg[config_key])
                if not raw.startswith("@"):      # skip @step-ref data sources
                    actual = Path(_subst(raw))
            elif default_tmpl:
                actual = Path(_subst(default_tmpl))

            if actual:
                info        = _file_info(actual)
                exist_mark  = " ✓" if actual.exists() else " ✗"
                lines.append(f"→ {sym}  {_fmt_path(actual)}  ({info}){exist_mark}")
            elif glob_pat and data_dir:
                count     = sum(1 for _ in data_dir.rglob(glob_pat))
                count_str = f"  ×{count}" if count else ""
                lines.append(f"→ {sym}  {label}{count_str}")
            else:
                lines.append(f"→ {sym}  {label}")

        for j, line in enumerate(lines):
            is_last_line = j == len(lines) - 1
            line_pfx = step_child + ("└── " if is_last_line else "├── ")
            print(f"{line_pfx}{line}")

