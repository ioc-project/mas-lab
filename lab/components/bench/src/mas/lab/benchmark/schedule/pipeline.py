#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Batch pipeline orchestration — resolve once, materialize, execute."""
from __future__ import annotations

import copy
import logging
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from mas.lab.benchmark.execution import apply_step_overrides
from mas.lab.benchmark.schedule.pipeline_resolve import resolve_pipeline_specs
from mas.lab.benchmark.schedule.run_discovery import (
    discover_benchmark_runs,
    discover_benchmark_scenarios,
    discover_benchmark_tests,
    list_child_artifact_paths,
)

logger = logging.getLogger(__name__)

_INFRA_STEP_TYPES = frozenset(
    {"service_start", "service_stop", "serialize", "deserialize"}
)

# The scheduler runs each instance as its own node (max_active + per-type concurrency).


def substitute_template_vars(obj: Any, template_vars: dict[str, str]) -> Any:
    """Replace ``{key}`` placeholders in nested config structures."""
    if isinstance(obj, str):
        out = obj
        for key, value in template_vars.items():
            out = out.replace(f"{{{key}}}", value)
        return out
    if isinstance(obj, dict):
        return {k: substitute_template_vars(v, template_vars) for k, v in obj.items()}
    if isinstance(obj, list):
        return [substitute_template_vars(v, template_vars) for v in obj]
    return obj


def _base_step_name(spec: Any) -> str:
    return spec.name or spec.type


def _effective_scope(spec: Any) -> str:
    """Resolve a spec's materialization scope.

    An explicit ``scope:`` (set by the level block a spec was declared
    under, or written directly) always wins. The ``per_run``/``per_scenario``
    booleans are a fallback for specs with no ``scope:`` at all.
    """
    scope = str(spec.scope or "").strip()
    if scope:
        return scope
    if spec.per_run:
        return "run"
    if spec.per_scenario:
        return "scenario"
    return "application"


@dataclass(frozen=True)
class _Node:
    """One folder in the application / scenario / test / run tree."""

    scope: str
    path: Path
    scenario: str = ""
    test: str = ""
    run: str = ""

    def suffix(self) -> str:
        if self.scope == "run":
            return f"{self.scenario}-{self.test}-{self.run}"
        if self.scope == "test":
            return f"{self.scenario}-{self.test}"
        if self.scope == "scenario":
            return self.scenario
        return ""

    def contains(self, other: "_Node") -> bool:
        if self.scope in ("application", "experiment"):
            return True
        if other.scenario != self.scenario:
            return False
        if self.scope == "scenario":
            return True
        if other.test != self.test:
            return False
        if self.scope == "test":
            return True
        return other.run == self.run


def _filter_scenario(refs: list, scenario_ids: list[str]) -> list:
    allowed = set(scenario_ids)
    if not allowed:
        return list(refs)
    return [r for r in refs if r.scenario in allowed]


def _nodes_for_scope(
    scope: str, output_dir: Path, scenario_ids: list[str]
) -> list[_Node]:
    """Discover the folders/instances for one scope, shared by every spec at
    that scope in this ``materialize_step_dicts`` call.

    ``scenario_ids`` always filters "run" discovery here — including for a
    legacy ``per_run: true``-without-``per_scenario`` spec, which previously
    swept every run on disk unfiltered. No current experiment.yaml uses that
    flag combination (all real configs use ``scope:``), and per-spec filtering
    would need per-spec node lists rather than one shared list per scope, so
    this asymmetry is left as a known, currently-dormant gap.
    """
    if scope == "run":
        return [
            _Node("run", r.path, r.scenario, r.test, r.run)
            for r in _filter_scenario(discover_benchmark_runs(output_dir), scenario_ids)
        ]
    if scope == "test":
        return [
            _Node("test", t.path, t.scenario, t.test)
            for t in _filter_scenario(discover_benchmark_tests(output_dir), scenario_ids)
        ]
    if scope == "scenario":
        found = _filter_scenario(discover_benchmark_scenarios(output_dir), scenario_ids)
        if found:
            return [_Node("scenario", s.path, s.scenario) for s in found]
        return [_Node("scenario", output_dir / sid, sid) for sid in scenario_ids]
    return [_Node("application", output_dir)]


def _expand_deps(
    depends_on: list[str],
    node: _Node,
    name_scope: dict[str, str],
    nodes: dict[str, list[_Node]],
    batched: set[str],
    collapsed: set[str] | None = None,
) -> list[str]:
    """Rewrite each dependency to the child instances under *node*.

    ``collapsed`` contains step base names that will be emitted as a single
    wrapper step (batched types + concurrent wrappers). Deps in this set
    are never suffixed — they reference the wrapper/batch name directly.
    """
    all_collapsed = batched | (collapsed or set())
    expanded: list[str] = []
    for dep in depends_on or []:
        if dep in all_collapsed:
            expanded.append(dep)
            continue
        child_scope = name_scope.get(dep)
        if not child_scope or child_scope in ("application", "experiment"):
            expanded.append(dep)
            continue
        for child in nodes.get(child_scope, []):
            if node.contains(child):
                suffix = child.suffix()
                expanded.append(f"{dep}-{suffix}" if suffix else dep)
    return expanded


#: The level one step down the hierarchy — whose artifacts a node fans in from.
_CHILD_LEVEL = {
    "application": "scenario",
    "experiment": "scenario",
    "scenario": "test",
    "item": "run",
    "test": "run",
}


def _child_artifact_filename(
    inputs: list[str], child_artifacts: dict[str, Any]
) -> str:
    """Resolve the fan-in filename for artifact ``inputs[0]``.

    Prefers the child level's own declared ``ArtifactSpec`` (honoring a
    custom ``path:``); falls back to the global artifact-type default when
    the child level didn't declare that artifact name explicitly (the
    conventional ``df`` alias for a ``dataframe`` artifact is not itself
    registered as a type, so this is the common case, not just a last
    resort). If the guessed filename is wrong, ``gather_level`` raises at
    execute() time when nothing was found to concatenate — see there.
    """
    from mas.lab.lab.config.artifact_types import type_info
    from mas.lab.lab.config.pipeline import ArtifactSpec

    name = inputs[0] if inputs else "df"
    declared = child_artifacts.get(name)
    if declared is not None:
        return str(declared.relative_path())
    info = type_info(name)
    type_name = name if info else "dataframe"
    if not info:
        info = type_info("dataframe")
    spec = ArtifactSpec(
        name=name, type=type_name, path=info.get("path") or ""
    )
    return str(spec.relative_path())


def _inject_fan_in(
    cfg: dict[str, Any],
    spec: Any,
    node: _Node,
    output_dir: Path,
    level_artifacts: dict[str, dict[str, Any]],
) -> None:
    inputs = list(getattr(spec, "inputs", None) or [])
    if not inputs or node.scope == "run":
        return
    child_level = _CHILD_LEVEL.get(node.scope, "")
    filename = _child_artifact_filename(inputs, level_artifacts.get(child_level, {}))
    cfg.setdefault(
        "artifact_paths",
        [
            str(path)
            for path in list_child_artifact_paths(
                output_dir=output_dir,
                scope=node.scope,
                scenario=node.scenario,
                test=node.test,
                filename=filename,
            )
        ],
    )


def _fill_cfg(
    spec: Any,
    node: _Node,
    output_dir: Path,
    level_artifacts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    cfg = copy.deepcopy(spec.config or {})
    cfg.setdefault("level_dir", str(node.path))
    if node.scenario:
        cfg.setdefault("scenario", node.scenario)
        cfg.setdefault("scenario_dir", str(output_dir / node.scenario))
    if node.test:
        cfg.setdefault("test", node.test)
    if node.run:
        cfg.setdefault("run", node.run)
        cfg.setdefault("run_dir", str(node.path))
    if node.scope == "scenario":
        cfg.setdefault("scenarios", [node.scenario])
    if node.scope != "application":
        cfg.setdefault("output_dir", str(node.path))
    _inject_fan_in(cfg, spec, node, output_dir, level_artifacts)
    return cfg


def _finalize_cfg(
    spec: Any,
    cfg: dict[str, Any],
    *,
    infra_name: Optional[str],
    step_overrides: Optional[dict],
    tmpl: dict[str, str],
) -> dict[str, Any]:
    if infra_name and spec.type in _INFRA_STEP_TYPES:
        cfg.setdefault("infra", infra_name)
    cfg = apply_step_overrides(cfg, spec.type, step_overrides or {})
    if tmpl:
        cfg = substitute_template_vars(cfg, tmpl)
    return cfg


def _step_dict(spec: Any, name: str, cfg: dict[str, Any], deps: list[str]) -> dict:
    return {
        "name": name,
        "type": spec.type,
        "phase": spec.phase,
        "config": cfg,
        "depends_on": deps,
        "scope": _effective_scope(spec),
        "resource": str(spec.resource or "cpu"),
        "refresh": str(spec.refresh or ""),
        "concurrency": int(spec.concurrency or 1),
    }


def _emit_instances(
    spec: Any,
    base_name: str,
    instances: list[tuple[str, dict[str, Any], list[str]]],
) -> list[dict]:
    """One scheduler node per instance. Concurrency is a scheduler cap, not a wrapper."""
    del base_name
    return [_step_dict(spec, name, cfg, deps) for name, cfg, deps in instances]


#: Scope vocabulary used by ``_Node`` / the node tables, for the public aliases.
_NODE_SCOPE_ALIASES = {"item": "test", "experiment": "application"}


def node_scope(scope: str) -> str:
    """Normalise a public scope name (``item``, ``experiment``) to the node key."""
    return _NODE_SCOPE_ALIASES.get(scope, scope)


def make_node(
    scope: str,
    path: Path,
    scenario: str = "",
    test: str = "",
    run: str = "",
) -> _Node:
    """Build the node a level hook runs for (no disk discovery involved)."""
    return _Node(node_scope(scope), Path(path), scenario, test, run)


#: Hierarchy rank of a scope (inner levels first); aliases share a rank.
_SCOPE_RANK = {"run": 0, "test": 1, "item": 1, "scenario": 2, "application": 3, "experiment": 3}

#: Artifacts the execution engine itself writes (no pipeline step declares ``out:`` for them).
_ENGINE_ARTIFACTS = frozenset({"trace", "events", "run_info"})


def _dataflow_deps(phase_specs: list) -> dict[str, list[str]]:
    """Implicit dependencies from ``in:`` / ``out:``.

    A step that reads artifact ``X`` runs after the steps that write it. Among the
    producers at or below the consumer's level, the nearest level wins (a scenario
    step reading ``df`` follows the scenario step that gathers ``df``, not every run
    step that wrote a per-run ``df``). Names are base step names; expansion to the
    instances under the consumer's node is done by ``_expand_deps``.
    """
    producers: dict[str, list[tuple[int, str]]] = {}
    for sp in phase_specs:
        for art in getattr(sp, "outputs", None) or []:
            producers.setdefault(art, []).append(
                (_SCOPE_RANK.get(_effective_scope(sp), 3), _base_step_name(sp))
            )
    derived: dict[str, list[str]] = {}
    for sp in phase_specs:
        me = _base_step_name(sp)
        rank = _SCOPE_RANK.get(_effective_scope(sp), 3)
        extra: list[str] = []
        for art in getattr(sp, "inputs", None) or []:
            cands = [(rk, nm) for rk, nm in producers.get(art, []) if rk <= rank and nm != me]
            if not cands:
                if art not in _ENGINE_ARTIFACTS:
                    logger.warning(
                        "step %r reads artifact %r (in:), but no step in this phase writes it (out:)",
                        me, art,
                    )
                continue
            nearest = max(rk for rk, _ in cands)
            for _rk, nm in cands:
                if _rk == nearest and nm not in extra:
                    extra.append(nm)
        derived[me] = extra
    return derived


def materialize_step_dicts(
    specs: list,
    *,
    phase: str | None = None,
    scenario_ids: list[str],
    infra_name: Optional[str],
    step_overrides: Optional[dict],
    template_vars: Optional[dict[str, str]] = None,
    level_artifacts: Optional[dict[str, dict[str, Any]]] = None,
    scopes: Optional[set[str]] = None,
    nodes: Optional[dict[str, list[_Node]]] = None,
) -> list[dict]:
    """Expand each spec once per folder at its level.

    ``scopes`` keeps only specs whose effective scope is in the set (public or
    node names). ``nodes`` replaces folder discovery with explicit nodes, so a
    hook can expand a spec for exactly the run/item/scenario it belongs to.

    Higher-level steps receive ``artifact_paths`` — the child files of the
    artifact named in ``in:``. ``level_artifacts`` (``{level: {name: ArtifactSpec}}``,
    from the experiment's declared ``artifacts:`` blocks) resolves the child's
    filename from its own declared path when the caller has it; without it,
    fan-in falls back to the artifact type's default path.
    """
    phase_specs = (
        [s for s in specs if getattr(s, "phase", "post") == phase]
        if phase is not None
        else list(specs)
    )
    if scopes is not None:
        wanted = {node_scope(sc) for sc in scopes}
        phase_specs = [s for s in phase_specs if node_scope(_effective_scope(s)) in wanted]
    if not phase_specs:
        return []

    tmpl = template_vars or {}
    output_dir = Path(tmpl.get("output_dir", "."))
    ids = list(scenario_ids or [])
    if nodes is None:
        nodes = {
            scope: _nodes_for_scope(scope, output_dir, ids)
            for scope in ("run", "test", "scenario", "application")
        }
    else:
        nodes = {node_scope(k): list(v) for k, v in nodes.items()}
        for scope in ("run", "test", "scenario", "application"):
            nodes.setdefault(scope, [])
    nodes["item"] = nodes["test"]
    nodes["experiment"] = nodes["application"]
    name_scope = {
        _base_step_name(spec): _effective_scope(spec) for spec in phase_specs
    }
    level_arts = level_artifacts or {}
    derived_deps = _dataflow_deps(phase_specs)

    step_dicts: list[dict] = []
    for spec in phase_specs:
        base_name = _base_step_name(spec)
        scope = _effective_scope(spec)
        instances: list[tuple[str, dict[str, Any], list[str]]] = []
        for node in nodes[scope]:
            cfg = _finalize_cfg(
                spec,
                _fill_cfg(spec, node, output_dir, level_arts),
                infra_name=infra_name,
                step_overrides=step_overrides,
                tmpl=tmpl,
            )
            suffix = node.suffix()
            name = f"{base_name}-{suffix}" if suffix else base_name
            declared = list(spec.depends_on or [])
            deps = _expand_deps(
                declared + [d for d in derived_deps.get(base_name, []) if d not in declared],
                node, name_scope, nodes, set(),
            )
            instances.append((name, cfg, deps))
        step_dicts.extend(_emit_instances(spec, base_name, instances))
    return step_dicts


def start_pipeline_scheduler(
    *,
    exp: Any,
    experiment_yaml: Path,
    output_dir: Path,
    specs: list | None,
    keys: list,
    scenario_ids: list[str],
    infra_name: str | None = None,
    step_overrides: dict | None = None,
    progress: bool = True,
    data_cache_dir: Path | None = None,
):
    """Start the post DAG so run-scope nodes overlap MAS execution."""
    if not specs or not keys:
        return None
    from mas.lab.benchmark.pipeline.executor import PipelineExecutor
    from mas.lab.benchmark.pipeline.expand import planned_nodes
    from mas.lab.benchmark.pipeline.scheduler import install_scope_gates

    nodes = planned_nodes(keys, output_dir)
    step_dicts = materialize_step_dicts(
        specs,
        phase="post",
        scenario_ids=list(scenario_ids or sorted({k.scenario for k in keys})),
        infra_name=infra_name,
        step_overrides=step_overrides,
        template_vars={"output_dir": str(output_dir)},
        level_artifacts=_level_artifacts(exp),
        nodes=nodes,
    )
    if not step_dicts:
        return None
    try:
        name = str(exp.name)
    except AttributeError:
        name = "pipeline"
    pipeline = build_runtime_pipeline(
        exp=exp,
        experiment_yaml=experiment_yaml,
        step_dicts=step_dicts,
        pipeline_name=f"{name}-post",
    )
    executor = PipelineExecutor(
        pipeline,
        output_dir=output_dir,
        progress=progress,
        data_cache_dir=data_cache_dir,
    )
    bound = executor.bind(
        template_vars={"output_dir": str(output_dir)},
        parallel=True,
    )
    install_scope_gates(bound.sched, keys)
    import asyncio

    task = asyncio.create_task(bound.finish())

    class _Live:
        def __init__(self) -> None:
            self.bound = bound
            self._task = task
            self.done = False
            self.error: BaseException | None = None

        async def on_terminal(self, key, status: str) -> None:
            try:
                await self.bound.mark_run_terminal(key, status)
            except Exception:
                logger.debug("pipeline terminal skipped", exc_info=True)

        async def wait(self) -> Any:
            try:
                result = await self._task
                self.done = True
                return result
            except BaseException as exc:
                self.error = exc
                raise

    return _Live()


def build_runtime_pipeline(
    *,
    exp: Any,
    experiment_yaml: Path,
    step_dicts: list[dict],
    pipeline_name: str | None = None,
):
    from mas.lab.benchmark.pipeline import (
        Pipeline,
        PipelineConfig,
        PipelineStep,
    )

    steps = [
        PipelineStep.from_dict(s, base_dir=experiment_yaml.parent)
        for s in step_dicts
    ]
    try:
        label = pipeline_name or str(exp.name)
    except AttributeError:
        label = pipeline_name or "pipeline"
    return Pipeline(
        config=PipelineConfig(name=label),
        steps=steps,
        config_path=experiment_yaml,
    )


class PipelineExecutionError(RuntimeError):
    """Raised when a benchmark post/pre pipeline step fails."""


def _publish_pipeline_phase(phase: str) -> None:
    """Publish a pipeline_phase record onto EventStream and the channel."""
    record = {"kind": "pipeline_phase", "phase": phase}
    try:
        from mas.runtime.boundary.obs.event_stream import emit_to_channel, publish_event

        publish_event(record)
        emit_to_channel(record)
    except Exception:
        logger.debug("pipeline_phase event skipped", exc_info=True)


def _print_pipeline_banner(pipeline, phase_label: str) -> None:
    from mas.lab.benchmark.pipeline.executor import COMPACT_STEP_LIST_THRESHOLD

    title = f"{phase_label}-phase" if phase_label else "pipeline"
    print(f"Running {title} ({len(pipeline.steps)} steps):")
    if len(pipeline.steps) > COMPACT_STEP_LIST_THRESHOLD:
        counts = Counter(ps.type for ps in pipeline.steps)
        for step_type, count in counts.items():
            label = f" × {count}" if count > 1 else ""
            print(f"  · {step_type}{label}")
        return
    for ps in pipeline.steps:
        print(f"  · {ps.name} ({ps.type})")


async def execute_runtime_pipeline(
    pipeline,
    *,
    output_dir: Path,
    progress: bool = True,
    data_cache_dir: Optional[Path] = None,
    template_vars: Optional[dict[str, str]] = None,
    force_rerun: list[str] | None = None,
    phase_label: str = "",
) -> bool:
    from mas.lab.benchmark.pipeline.executor import PipelineExecutor

    if phase_label == "post":
        extract_fp = output_dir / ".cache" / "extract.fingerprint"
        if extract_fp.exists():
            extract_fp.unlink()

    if pipeline.steps:
        print()
        _print_pipeline_banner(pipeline, phase_label)

    executor = PipelineExecutor(
        pipeline,
        output_dir=output_dir,
        progress=progress,
        data_cache_dir=data_cache_dir,
    )
    try:
        result = await executor.run(
            template_vars=dict(template_vars or {"output_dir": str(output_dir)}),
            force_rerun=force_rerun,
            parallel=True,
        )
        print(result.summary())
        if not result.success:
            detail = "; ".join(
                f"{name}: {msg}" for name, msg in list(result.step_errors.items())[:3]
            ) or ", ".join(result.failed_steps[:3])
            raise PipelineExecutionError(
                f"{phase_label or 'pipeline'} failed — {detail or 'see step errors above'}"
            )
        return True
    except PipelineExecutionError:
        raise
    except Exception as exc:
        logger.error("Pipeline execution failed: %s", exc)
        raise PipelineExecutionError(str(exc)) from exc


def _load_generated_dataset(output_dir: Path) -> list | None:
    gen_ds_path = output_dir / "generated_dataset.yaml"
    if not gen_ds_path.exists():
        return None
    try:
        from mas.runtime.spec.source import load_yaml_file

        gds_data = load_yaml_file(gen_ds_path)
        gen_items = (
            gds_data.get("items", [])
            if isinstance(gds_data, dict)
            else gds_data
        )
        if gen_items:
            logger.info(
                "Pre-phase: loaded %d generated dataset items from %s",
                len(gen_items),
                gen_ds_path,
            )
            return gen_items
    except Exception as gds_exc:
        logger.warning("Failed to load generated_dataset.yaml: %s", gds_exc)
    return None


def _level_artifacts(exp: Any) -> dict[str, dict[str, Any]]:
    """Return ``{level: {artifact_name: ArtifactSpec}}`` declared on *exp*."""
    levels = getattr(exp, "levels", None) or {}
    return {
        level_name: {a.name: a for a in getattr(level_spec, "artifacts", None) or []}
        for level_name, level_spec in levels.items()
    }


async def run_pipeline_phase(
    *,
    phase: str,
    exp: Any,
    experiment_yaml: Path,
    output_dir: Path,
    specs: list | None = None,
    scenario_ids: list[str] | None = None,
    infra_name: Optional[str] = None,
    step_overrides: Optional[dict] = None,
    progress: bool = True,
    data_cache_dir: Optional[Path] = None,
    scopes: Optional[set[str]] = None,
    nodes: Optional[dict[str, list[_Node]]] = None,
    label: str | None = None,
) -> list | None:
    """Run all steps for ``phase`` (``pre`` or ``post``).

    ``scopes`` / ``nodes`` restrict and anchor the expansion (see
    ``materialize_step_dicts``); ``label`` suffixes the pipeline name.

    Returns updated dataset items when ``phase=='pre'`` and a generator step
    wrote ``generated_dataset.yaml``; otherwise ``None``.
    """
    resolved = specs if specs is not None else resolve_pipeline_specs(exp, experiment_yaml)
    step_dicts = materialize_step_dicts(
        resolved,
        phase=phase,
        scenario_ids=list(scenario_ids or []),
        infra_name=infra_name,
        step_overrides=step_overrides,
        template_vars={"output_dir": str(output_dir)},
        level_artifacts=_level_artifacts(exp),
        scopes=scopes,
        nodes=nodes,
    )
    if not step_dicts:
        return None

    if phase == "post":
        _publish_pipeline_phase("post")

    pipeline = build_runtime_pipeline(
        exp=exp,
        experiment_yaml=experiment_yaml,
        step_dicts=step_dicts,
        pipeline_name=f"{exp.name}-{phase}" + (f"-{label}" if label else ""),
    )
    ok = await execute_runtime_pipeline(
        pipeline,
        output_dir=output_dir,
        progress=progress,
        data_cache_dir=data_cache_dir,
        phase_label=phase,
    )
    if phase == "pre" and ok:
        return _load_generated_dataset(output_dir)
    return None


def materialize_selected_specs(
    specs: list,
    *,
    experiment_yaml: Path,
    output_dir: Path,
    step_overrides: Optional[dict] = None,
    name_overrides: Optional[dict] = None,
) -> list[dict]:
    """Materialize an explicit subset of specs (standalone ``mas-lab run pipeline step``)."""
    step_dicts = materialize_step_dicts(
        specs,
        phase=None,
        scenario_ids=[],
        infra_name=None,
        step_overrides=step_overrides,
        template_vars={"output_dir": str(output_dir)},
    )
    for step_dict in step_dicts:
        extra = (name_overrides or {}).get(step_dict["name"], {})
        if extra:
            step_dict["config"].update(extra)
    return step_dicts
