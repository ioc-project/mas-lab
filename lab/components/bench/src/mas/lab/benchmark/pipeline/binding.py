#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Map experiment / artefact data onto step config the way ggplot maps aesthetics.

A YAML value that is ``$path`` is a binding into the pipeline context.
Anything else is free text. Write ``$$foo`` for a literal ``$foo``.

This is the ggplot split between ``aes()`` (per observation) and ``labs()``
(once per figure), and the d3 split between an accessor and a constant.
The pipeline file does not have to name the experiment it will run against:
context is injected when the step executes.

Roots a step can see, from its place in the hierarchy:

+------------+-----------------------------------------------------+
| Root       | Bound to                                            |
+============+=====================================================+
| experiment | the experiment being run                            |
| scenario   | this scenario, or the series/row's scenario         |
| test       | this dataset item                                   |
| run        | this repeat                                         |
| artefact   | metadata on the current input row / artefact        |
| row        | the observation itself (tidy-table column lookup)   |
+------------+-----------------------------------------------------+

A run-scope step sees all four of experiment / scenario / test / run.
A scenario-scope step sees experiment / scenario (and whatever child
frames already copied onto the artefact). An experiment-scope plot that
walks series passes a per-series datum so ``$scenario.metadata.legend``
still resolves.

``scenarios`` / ``tests`` / ``runs`` on the bag are the catalogs, so a
standalone pipeline reused across experiments keeps working: each run
supplies a different catalog.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from mas.lab.benchmark.pipeline.resources import ScopeContext

logger = logging.getLogger(__name__)

_BINDING = re.compile(r"^\$([A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)*)$")
_ROOTS = ("experiment", "scenario", "test", "run", "artefact", "row")


def binding_path(value: Any) -> str | None:
    """Return the dotted path if *value* is a ``$path`` binding, else None."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.startswith("$$"):
        return None
    matched = _BINDING.fullmatch(text)
    return matched.group(1) if matched else None


def is_binding(value: Any) -> bool:
    return binding_path(value) is not None


def as_literal(value: Any) -> Any:
    """Unescape ``$$foo`` to ``$foo``; leave every other value as written."""
    if isinstance(value, str) and value.lstrip().startswith("$$"):
        text = value.lstrip()
        return text[1:]
    return value


def lookup(root: Any, path: str) -> Any:
    """Walk a dotted path through mappings."""
    cur: Any = root
    for part in str(path or "").split("."):
        if not part:
            continue
        if not isinstance(cur, Mapping) or part not in cur:
            return None
        cur = cur[part]
    return cur


def resolve(value: Any, bag: Mapping[str, Any] | None = None) -> Any:
    """If *value* is ``$path``, look it up; otherwise return it as free text."""
    path = binding_path(value)
    if path is None:
        return as_literal(value)
    root = path.split(".", 1)[0]
    if root not in _ROOTS and root not in (bag or {}):
        # `$experimnt.name` is a typo, not an empty value: say so instead of drawing "None".
        raise ValueError(f"unknown binding root {root!r} in {value!r}; roots are {', '.join(_ROOTS)}")
    got = lookup(bag or {}, path)
    if got is None and isinstance((bag or {}).get(root), Mapping) and bag[root]:
        _warn_missing(path)
    return got


_WARNED: set[str] = set()


def _warn_missing(path: str) -> None:
    """A binding into a populated root that finds nothing: a typo looks exactly like this."""
    if path not in _WARNED:
        _WARNED.add(path)
        logger.warning("binding $%s resolved to nothing; check the key spelling", path)


def resolve_tree(obj: Any, bag: Mapping[str, Any] | None = None) -> Any:
    """Resolve ``$path`` strings anywhere in a nested config structure."""
    if isinstance(obj, str):
        return resolve(obj, bag)
    if isinstance(obj, Mapping):
        return {key: resolve_tree(val, bag) for key, val in obj.items()}
    if isinstance(obj, list):
        return [resolve_tree(item, bag) for item in obj]
    return obj


def _as_obj(value: Any, ident: str = "") -> dict[str, Any]:
    if isinstance(value, Mapping):
        out = dict(value)
        if ident and "id" not in out:
            out["id"] = ident
        return out
    if value in (None, ""):
        return {"id": ident} if ident else {}
    return {"id": str(value)}


def _field(obj: Any, name: str, default: Any = None) -> Any:
    """Read a named field from a mapping or a typed object."""
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    try:
        return object.__getattribute__(obj, name)
    except AttributeError:
        return default


def overlay_datum(bag: Mapping[str, Any], datum: Mapping[str, Any] | None) -> dict[str, Any]:
    """Copy *bag* and overlay per-observation roots from *datum*."""
    out = dict(bag)
    if not datum:
        return out
    for key in _ROOTS:
        if key in datum and datum[key] is not None:
            val = datum[key]
            out[key] = dict(val) if isinstance(val, Mapping) else val
    return out


def _ctx_field(ctx: Any, name: str) -> Any:
    """Read a named field from a pipeline context object; missing is None."""
    if ctx is None:
        return None
    try:
        return object.__getattribute__(ctx, name)
    except AttributeError:
        return None


def context_bag(ctx: Any = None, datum: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Build the ``$experiment`` / ``$scenario`` / … bag for a step execution.

    Catalogs live on ``ctx.template_vars`` (injected for both embedded and
    standalone pipelines). ``ctx.scope_context`` selects the current
    experiment / scenario / test / run objects. *datum* overlays those
    roots for a single series or tidy-table row.
    """
    tv: dict[str, Any] = {}
    sc: ScopeContext | None = None
    raw_tv = _ctx_field(ctx, "template_vars")
    if isinstance(raw_tv, Mapping):
        tv = dict(raw_tv)
    raw_sc = _ctx_field(ctx, "scope_context")
    if isinstance(raw_sc, ScopeContext):
        sc = raw_sc
    scenarios = tv.get("scenarios") if isinstance(tv.get("scenarios"), Mapping) else {}
    tests = tv.get("tests") if isinstance(tv.get("tests"), Mapping) else {}
    runs = tv.get("runs") if isinstance(tv.get("runs"), Mapping) else {}
    eid = str(sc.experiment if sc is not None else "")
    sid = str(sc.scenario if sc is not None else "")
    tid = str(sc.test if sc is not None else "")
    rid = str(sc.run if sc is not None else "")
    bag: dict[str, Any] = {
        "experiment": _as_obj(tv.get("experiment"), eid),
        "scenario": _as_obj(scenarios.get(sid) if sid else None, sid),
        "test": _as_obj(tests.get(tid) if tid else None, tid),
        "run": _as_obj(runs.get(rid) if rid else None, rid),
        "artefact": {},
        "row": {},
        "scenarios": scenarios,
        "tests": tests,
        "runs": runs,
    }
    return overlay_datum(bag, datum)


def scope_ids(ctx: Any) -> dict[str, str]:
    """Non-empty experiment / scenario / test / run ids from *ctx*."""
    sc = _ctx_field(ctx, "scope_context")
    if not isinstance(sc, ScopeContext):
        return {}
    out: dict[str, str] = {}
    for key, val in (
        ("experiment", sc.experiment),
        ("scenario", sc.scenario),
        ("test", sc.test),
        ("run", sc.run),
    ):
        text = str(val or "")
        if text:
            out[key] = text
    return out


def fill_scope_columns(columns: Any, ctx: Any) -> dict[str, str]:
    """Scope ids to copy onto a frame that does not already have them.

    Coalesce steps (``gather_level``, dataframe joins) call this so a
    test-level artefact still carries the scenario that produced each row
    when a later experiment-level plot binds ``$scenario.*``.
    """
    return {key: val for key, val in scope_ids(ctx).items() if key not in columns}


def datum_from_row(row: Mapping[str, Any] | None, bag: Mapping[str, Any]) -> dict[str, Any]:
    """Per-row datum: look the row's scenario / test / run up in the catalog."""
    row = dict(row or {})
    scenarios = bag.get("scenarios") if isinstance(bag.get("scenarios"), Mapping) else {}
    tests = bag.get("tests") if isinstance(bag.get("tests"), Mapping) else {}
    runs = bag.get("runs") if isinstance(bag.get("runs"), Mapping) else {}
    sid = str(row.get("scenario") or row.get("scenario_id") or "")
    if isinstance(row.get("scenario"), Mapping):
        sid = str(row["scenario"].get("id") or sid)
    tid = str(row.get("test") or row.get("item_id") or row.get("item") or "")
    if tid.startswith("item") and tid[4:] in tests:
        tid = tid[4:]
    if isinstance(row.get("test"), Mapping):
        tid = str(row["test"].get("id") or tid)
    rid = str(row.get("run") or row.get("run_id") or "")
    artefact = row.get("artefact") or row.get("metadata") or row.get("meta") or {}
    if not isinstance(artefact, Mapping):
        artefact = {}
    scenario = row["scenario"] if isinstance(row.get("scenario"), Mapping) else scenarios.get(sid)
    test = row["test"] if isinstance(row.get("test"), Mapping) else tests.get(tid)
    run = row["run"] if isinstance(row.get("run"), Mapping) else runs.get(rid)
    return {
        "scenario": _as_obj(scenario, sid),
        "test": _as_obj(test, tid),
        "run": _as_obj(run, rid),
        "row": row,
        "artefact": dict(artefact),
    }


def series_parts(label: str) -> tuple[str, str]:
    scenario, _, rest = str(label).partition("/")
    item = rest[4:] if rest.startswith("item") and len(rest) > 4 else rest
    return scenario, item


def series_datum(
    label: str,
    bag: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Datum for one plotted series, keyed by ``scenario/item…`` labels."""
    scenario_id, item_id = series_parts(label)
    row: dict[str, Any] = {}
    if rows:
        first = rows[0]
        if isinstance(first, Mapping):
            meta = first.get("meta") or first.get("metadata") or {}
            if isinstance(meta, Mapping):
                row.update(meta)
            row.update(dict(first))
    row.setdefault("scenario", scenario_id)
    if item_id:
        row.setdefault("test", item_id)
        row.setdefault("item", item_id)
    return datum_from_row(row, bag)


def series_legend(
    label: str,
    *,
    path: str | None,
    catalog: Mapping[str, Any] | None = None,
    rows: Sequence[Mapping[str, Any]] | None = None,
    ctx: Any = None,
) -> str:
    """Resolve a legend binding (or free text) for one series.

    ``$scenario.metadata.legend`` reads that scenario's metadata. A free-text
    value (``skill vs empty``) is used as-is for every series. Missing
    lookups fall back to the scenario or test id.
    """
    bag = context_bag(ctx)
    if catalog:
        for key in ("experiment", "scenarios", "tests", "runs"):
            if key in catalog:
                bag[key] = catalog[key]
    datum = series_datum(label, bag, rows)
    got = resolve(path, overlay_datum(bag, datum)) if path else None
    if got not in (None, ""):
        return str(got)
    scenario_id, item_id = series_parts(label)
    return str(scenario_id or item_id or label)


def bind_mapping(
    mapping: Mapping[str, Any] | None,
    rows: Sequence[Mapping[str, Any]],
    bag: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """ggplot ``aes()``: ``$path`` mapping values become columns on each row.

    Column names stay column names. A plot can invent a new aesthetic
    (``ribbon: $test.metadata.ci``) and it is filled from the catalog the
    same way as ``fill: $scenario.metadata.legend``.
    """
    mapping = dict(mapping or {})
    annotated = [dict(row) for row in rows]
    out: dict[str, Any] = {}
    for aes, val in mapping.items():
        path = binding_path(val)
        if path is None:
            out[aes] = as_literal(val)
            continue
        col = path.replace(".", "_")
        for rec in annotated:
            rec[col] = resolve(val, overlay_datum(bag, datum_from_row(rec, bag)))
        out[aes] = col
    return out, annotated


def pipeline_catalog(exp: Any) -> dict[str, Any]:
    """Catalog injected into every step's ``ctx.template_vars``."""
    name = str(_field(exp, "name", "") or "")
    ident = str(_field(exp, "id", "") or name)
    experiment = {
        "id": ident,
        "name": name,
        "description": str(_field(exp, "description", "") or ""),
        "metadata": dict(_field(exp, "metadata", None) or {}),
    }
    scenarios: dict[str, Any] = {}
    for spec in _field(exp, "scenarios", None) or ():
        sid = str(_field(spec, "id", "") or "")
        if not sid:
            continue
        scenarios[sid] = {
            "id": sid,
            "description": str(_field(spec, "description", "") or ""),
            "metadata": dict(_field(spec, "metadata", None) or {}),
            "tags": list(_field(spec, "tags", None) or []),
        }
    tests: dict[str, Any] = {}
    dataset = _field(exp, "dataset", None)
    try:
        from mas.lab.benchmark.dataset import Dataset

        path = Path(dataset) if dataset else None
        if path is not None and path.is_file():
            for item in Dataset.from_yaml(path).items:
                tests[item.id] = {
                    "id": item.id,
                    "description": str((item.metadata or {}).get("description") or ""),
                    "metadata": dict(item.metadata or {}),
                }
    except (OSError, TypeError, ValueError) as exc:
        logger.warning("dataset %s could not be read for $test bindings: %s", dataset, exc)
        tests = {}
    return {"experiment": experiment, "scenarios": scenarios, "tests": tests}
