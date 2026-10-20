#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations
"""CollectMetricsStep — gather per-run metrics into a tidy CSV.

Walks ``<output_dir>/<scenario>/<item_id>/r<n>/`` and reads two canonical
artefacts from every run directory:

  - ``metrics.json`` (schema v1): session metrics keyed by metric name
  - ``run_info.json`` (schema v1): execution metadata (latency, model, status)

Output columns (schema v1):

    scenario, item_id, run_idx, metric, value,
    cache_key, run_hash,
    latency_s, n_llm_calls, n_tool_calls,
    model, status,
    n_warnings, n_errors, run_status
    [+ dataset enrichment columns when dataset: is configured]

Configuration
-------------
output       str           Relative output path (default: "results.csv").
run_glob     str           Glob for run directories relative to the level folder.
                           Default: ``"*/*/r*"`` (scenario/item_id/run).
scenarios    list[str]     Optional scenario filter.
metrics_filename  str | list   Metrics file name, glob, or list of those
                           (default: ``metrics.json``). Split ``eval_mce``
                           steps with distinct ``metrics_filename`` values
                           are merged. Optional ``details`` on a score is
                           copied as a JSON column.

dataset      str | dict    Optional: path to a dataset YAML/JSON file (or
                           shorthand: just the path string).  When provided,
                           item metadata is joined into every row so that
                           downstream steps can group by any item field
                           without path heuristics.  Dict form::

                               dataset:
                                 path: datasets/mas-necessity.yaml
                                 join_on: item_id       # default
                                 columns: [group, complexity]   # default: all scalar fields

                           ``columns`` supports dotted paths into nested dicts,
                           e.g. ``tags.n_agents_min``, which will be flattened
                           into a column named ``n_agents_min``.

                           The step resolves the path relative to the pipeline
                           config file if it is a relative path.

identity     dict          Identity column mapping from run-dir path.
                           Keys = output column names, values = path specs.
                           Default::

                               scenario: "_parent.2"
                               item_id:  "_parent.1"          # exact dir name
                               run_idx:  "_parent.0|re:r(\\d+)"  # strip "r" prefix

                           NOTE: ``item_id`` is the **exact** directory name
                           (e.g. ``"mn1"``, not ``"1"``).  Do not use a regex
                           that strips the alphabetic prefix.

Example YAML::

    - name: collect-metrics
      type: collect_metrics
      config:
        dataset: datasets/mas-necessity.yaml   # lab-local forward ref
        # or library id:
        #   name: mas-necessity
        #   locator: samples
        output: results.csv

    # Downstream steps can now group by item metadata:
    - name: compute-ci
      type: compute_ci
      config:
        data: "@collect-metrics"
        groupby: [scenario, group, complexity, metric]
"""

import csv
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from mas.lab.benchmark.pipeline import PipelineStep, StepOutput
from mas.lab.benchmark.pipeline.artifact_paths import (
    resolve_step_artifact_path,
    resolve_step_root,
    step_artifact_relpath,
)
from mas.lab.benchmark.pipeline.resources import Artifact, Scope
from mas.library.lab.steps.data.to_dataframe import (
    _apply_transform,
    _parse_column_spec,
)

# Dataset path-ref resolution (pkg:// , lib: , relative). Imported at module top
# so any import problem surfaces when this step is resolved at startup — never
# mid-execution. (This is the one runtime-spec utility a bench step needs; it
# ideally belongs in a shared low-level package rather than mas.runtime.)
from mas.runtime.spec.source import resolve_path_ref

logger = logging.getLogger(__name__)

# Default identity: derives identity from the benchmark's directory convention.
# Benchmark always creates: <output_dir>/<scenario>/item<id>/r<n>/
# _parent.0 = run dir ("r1"), _parent.1 = item dir ("itemmn1"), _parent.2 = scenario dir.
# The "item" prefix is stripped from _parent.1 via regex — this is a structural
# property of the benchmark layout, not a heuristic.
_DEFAULT_IDENTITY: Dict[str, str] = {
    "scenario": "_parent.2",
    "item_id":  r"_parent.1|re:item(.+)",  # "itemmn1" → "mn1", "item3" → "3"
    "run_idx":  r"_parent.0|re:r(\d+)",    # "r1" → "1"
}


class CollectMetricsStep(PipelineStep):
    """Collect per-run metrics from metrics.json + run_info.json into a tidy CSV."""

    type = "collect_metrics"

    @property
    def output_artifacts(self) -> List[Tuple[str, Artifact]]:
        rel = step_artifact_relpath(self.name, "results.csv", self.config, name_keys=())
        artifact = Artifact(name=Path(rel).stem, format="csv", scope=Scope.EXPERIMENT)
        artifact._relative_path = rel  # type: ignore[attr-defined]
        return [("csv_path", artifact)]

    async def execute(self, ctx: "ExecutionContext") -> StepOutput:
        self._ctx = ctx
        config = self.config

        output_dir = resolve_step_root(ctx, config, step=self.name)
        output_path = resolve_step_artifact_path(
            ctx, None, self.name, "results.csv", config, name_keys=(), mkdir=True
        )

        run_glob = config.get("run_glob", "*/*/r*")
        id_spec = config.get("identity", _DEFAULT_IDENTITY)
        scenarios_filter: set = set(config.get("scenarios", []))
        dataset_columns: list = _dataset_columns_from_config(config.get("dataset"))
        metrics_filename = config.get("metrics_filename", "metrics.json")

        dataset_lookup = _load_dataset_lookup(
            config.get("dataset"),
            config_path=ctx.pipeline.config_path,
        )

        run_dirs = sorted(d for d in output_dir.glob(run_glob) if d.is_dir())
        if scenarios_filter:
            run_dirs = [d for d in run_dirs
                        if _parse_identity(d, output_dir, id_spec).get("scenario", "") in scenarios_filter]

        fieldnames = [
            "scenario", "item_id", "run_idx", "metric", "value",
            "cache_key", "run_hash",
            "latency_s", "n_llm_calls", "n_tool_calls",
            "model", "status",
            "n_warnings", "n_errors", "run_status",
            "details",
        ] + dataset_columns


        rows: list = []
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()

            for run_dir in run_dirs:
                identity = _parse_identity(run_dir, output_dir, id_spec)
                item_id = identity.get("item_id", "")

                # --- run_info.json: latency, model, status ---
                run_info: dict = {}
                ri_path = run_dir / "run_info.json"
                if ri_path.exists():
                    try:
                        run_info = json.loads(ri_path.read_text(encoding="utf-8"))
                    except Exception:
                        logger.debug('suppressed', exc_info=True)

                # --- .run_ref: trace hash for cache linkage ---
                run_hash: str = run_info.get("run_hash", "")
                n_llm_calls = 0
                n_tool_calls = 0
                run_ref_f = run_dir / ".run_ref"
                if run_ref_f.exists():
                    try:
                        from mas.lab.paths import trace_cache as _trace_cache
                        _hash = run_ref_f.read_text(encoding="utf-8").strip()
                        run_hash = run_hash or _hash
                        _cache_dir = _trace_cache() / _hash
                        _result_path = _cache_dir / "result.json"
                        if _result_path.exists():
                            _result = json.loads(_result_path.read_text(encoding="utf-8"))
                            run_info.setdefault("elapsed_ms", _result.get("elapsed_ms", 0))
                            run_info.setdefault("status", _result.get("status", ""))
                        _events_path = _cache_dir / "traces" / "events.jsonl"
                        if _events_path.exists():
                            for _line in _events_path.read_text(encoding="utf-8").splitlines():
                                _line = _line.strip()
                                if not _line:
                                    continue
                                try:
                                    _ev_kind = json.loads(_line).get("kind", "")
                                    if _ev_kind == "llm_call_start":
                                        n_llm_calls += 1
                                    elif _ev_kind == "tool_call_start":
                                        n_tool_calls += 1
                                except Exception:
                                    logger.debug('suppressed', exc_info=True)
                    except Exception:
                        logger.debug('suppressed', exc_info=True)

                latency_s = run_info.get("elapsed_ms", 0) / 1000.0
                model = run_info.get("model", "")
                status = run_info.get("status", "")

                # --- metrics.json (one file, a list, or a glob): session metrics ---
                from mas.library.lab.steps.eval.metrics_files import (
                    details_json,
                    load_merged_session,
                    resolve_metrics_paths,
                )

                metric_paths = resolve_metrics_paths(run_dir, metrics_filename)
                if not metric_paths:
                    continue
                session, rq, _paths = load_merged_session(run_dir, metrics_filename)
                n_warnings = len(rq.get("warnings", []))
                n_errors = len(rq.get("errors", []))
                run_status_val = rq.get("status", "ok")
                doc_cache_key = ""
                doc_run_hash = run_hash
                for m_path in metric_paths:
                    try:
                        doc = json.loads(m_path.read_text(encoding="utf-8"))
                    except Exception:
                        continue
                    doc_cache_key = doc.get("cache_key", "") or doc_cache_key
                    doc_run_hash = doc.get("run_hash", "") or doc_run_hash

                item_meta = dataset_lookup.get(item_id, {})

                for metric_id, entry in session.items():
                    if not isinstance(entry, dict):
                        continue
                    value = entry.get("value")
                    if value is None:
                        continue
                    row = {
                        **identity,
                        "metric": metric_id,
                        "value": float(value),
                        "cache_key": doc_cache_key or "",
                        "run_hash": doc_run_hash,
                        "latency_s": latency_s,
                        "n_llm_calls": n_llm_calls,
                        "n_tool_calls": n_tool_calls,
                        "model": model,
                        "status": status,
                        "n_warnings": n_warnings,
                        "n_errors": n_errors,
                        "run_status": run_status_val,
                        "details": details_json(entry),
                        **{col: None for col in dataset_columns},
                        **item_meta,
                    }
                    rows.append(row)
                    writer.writerow(row)

        if not rows:
            logger.warning("CollectMetricsStep: no metrics found")
            return StepOutput(data={"rows": 0, "df": None}, metadata={"error": "no data"})

        import pandas as pd
        df = pd.DataFrame(rows)
        logger.info("CollectMetricsStep: %d rows → %s", len(rows), output_path)

        try:
            from mas.lab.benchmark.schema.validation import validate_metrics_csv
            validate_metrics_csv(output_path)
            logger.debug("CollectMetricsStep: schema validation passed")
        except Exception as _ve:
            logger.warning("CollectMetricsStep: schema validation warning: %s", _ve)

        return StepOutput(
            data={"rows": len(rows), "csv_path": str(output_path), "df": df},
            files=[output_path],
            metadata={"output": str(output_path)},
        )


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _parse_identity(
    run_dir: Path, output_dir: Path, id_spec: Dict[str, str],
) -> dict:
    """Extract identity columns from a run directory path."""
    rel = run_dir.relative_to(output_dir)
    rel_parts = list(rel.parts)
    rel_parts.reverse()  # _parent.0 = immediate parent directory

    path_ctx: dict = {"_parent": run_dir.name}
    for i, name in enumerate(rel_parts):
        path_ctx[f"_parent.{i}"] = name

    identity: dict = {}
    for col_name, col_spec in id_spec.items():
        col_path, col_re = _parse_column_spec(col_spec)
        raw_val = path_ctx.get(col_path, "")
        identity[col_name] = _apply_transform(raw_val, col_re)
    return identity


# ------------------------------------------------------------------
# Dataset enrichment helpers
# ------------------------------------------------------------------

def _load_dataset_lookup(
    dataset_config: Any,
    *,
    config_path: Optional[Path] = None,
) -> Dict[str, Dict[str, Any]]:
    """Build an ``{item_id: {col: value, ...}}`` lookup from a dataset file.

    ``dataset_config`` can be:
    - ``None`` — no enrichment, returns ``{}``
    - ``str`` — path to dataset YAML/JSON; all scalar fields from each item added
    - ``dict`` with keys ``path``, ``join_on`` (default ``"id"``),
      ``columns`` (default: all scalar fields)

    Columns support dotted paths for nested fields, e.g. ``"tags.n_agents_min"``,
    which is flattened to column ``"n_agents_min"``.
    """
    import yaml

    if dataset_config is None:
        return {}

    if isinstance(dataset_config, str):
        ds_path_raw = dataset_config
        join_on = "id"
        requested_columns: Optional[list] = None
    elif isinstance(dataset_config, dict):
        ds_path_raw = dataset_config.get("path", "")
        ds_name = dataset_config.get("name", "")
        join_on = dataset_config.get("join_on", "id")
        requested_columns = dataset_config.get("columns")
        # Resolve by name if path not provided
        if not ds_path_raw and ds_name:
            from mas.lab.benchmark.experiment import _resolve_dataset_by_name
            try:
                resolved = _resolve_dataset_by_name(
                    config_path.parent if config_path else Path("."),
                    ds_name,
                    locator=dataset_config.get("locator"),
                )
                ds_path_raw = str(resolved)
            except Exception as exc:
                logger.warning("collect_metrics: cannot resolve dataset name '%s': %s", ds_name, exc)
                return {}
    else:
        logger.warning("collect_metrics: invalid dataset config type %s — skipping enrichment", type(dataset_config))
        return {}

    if not ds_path_raw:
        return {}

    anchor = config_path.parent if config_path else Path(".")
    ds_path = resolve_path_ref(str(ds_path_raw), anchor)

    if not ds_path.exists():
        logger.warning("collect_metrics: dataset file not found: %s — skipping enrichment", ds_path)
        return {}

    try:
        with ds_path.open(encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) if ds_path.suffix in (".yaml", ".yml") else json.load(fh)
    except Exception as exc:
        logger.warning("collect_metrics: cannot load dataset %s: %s", ds_path, exc)
        return {}

    # Normalise to list of items
    if isinstance(raw, dict) and raw.get("kind") == "Dataset":
        items = raw.get("spec", {}).get("items", [])
    elif isinstance(raw, dict) and "items" in raw:
        items = raw["items"]
    elif isinstance(raw, list):
        items = raw
    else:
        logger.warning("collect_metrics: unrecognised dataset format in %s", ds_path)
        return {}

    lookup: Dict[str, Dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        key = str(item.get(join_on, item.get("id", "")))
        if not key:
            continue
        meta = _extract_item_columns(item, requested_columns)
        lookup[key] = meta

    logger.debug("collect_metrics: loaded %d items from dataset %s", len(lookup), ds_path.name)
    return lookup


def _extract_item_columns(item: dict, columns: Optional[list]) -> Dict[str, Any]:
    """Extract flat columns from a dataset item dict.

    If ``columns`` is None, extracts all top-level scalar fields
    (excluding ``id``, ``prompt``, ``ground_truth``, ``rationale``).
    If ``columns`` is a list, extracts those fields (dotted paths supported).
    The output column name is the last segment of the dotted path.
    """
    _SKIP_DEFAULT = {"id", "prompt", "ground_truth", "rationale"}

    if columns is None:
        result = {}
        for k, v in item.items():
            if k in _SKIP_DEFAULT:
                continue
            if isinstance(v, (str, int, float, bool)) or v is None:
                result[k] = v
        return result

    result = {}
    for col_spec in columns:
        parts = col_spec.split(".")
        col_name = parts[-1]
        val: Any = item
        for part in parts:
            if isinstance(val, dict):
                val = val.get(part)
            else:
                val = None
                break
        if isinstance(val, (str, int, float, bool)) or val is None:
            result[col_name] = val
        elif isinstance(val, list):
            result[col_name] = ",".join(str(v) for v in val)
    return result


def _dataset_columns_from_config(dataset_config: Any) -> list[str]:
    """Return the ordered list of column names that dataset enrichment will add.

    Used to determine fieldnames before rows are collected so the CSV header
    can be written in one pass.
    """
    if dataset_config is None:
        return []
    if isinstance(dataset_config, str):
        return []  # all scalars — determined at collection time; empty default here
    if isinstance(dataset_config, dict):
        cols = dataset_config.get("columns")
        if cols is None:
            return []
        return [c.split(".")[-1] for c in cols]
    return []

