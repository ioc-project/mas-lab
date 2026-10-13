#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations
"""
Pipeline executor with automatic dependency resolution and caching.
"""


import json
import logging
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from dataclasses import dataclass, field

try:
    import yaml as _yaml
except ImportError:  # pragma: no cover
    _yaml = None  # type: ignore[assignment]
from datetime import datetime

from mas.lab.benchmark.pipeline.core import Pipeline, PipelineStep
from mas.lab.benchmark.pipeline.models import StepOutput
from mas.lab.benchmark.pipeline.resolver import DependencyResolver
from mas.lab.benchmark.pipeline.cache import CacheManager
from mas.lab.benchmark.pipeline.resources import (
    ResourceRegistry,
    ScopeContext,
    resolve_resource_refs,
)
from mas.lab.benchmark.pipeline.schema_validation import validate_payload
from mas.lab import paths as _paths
from mas.library_roots import find_ancestor_with_file
from mas.runtime.constants import LAB_CONFIG_FILENAME


logger = logging.getLogger(__name__)


def is_fatal_pipeline_error(exc: BaseException) -> bool:
    """True when a step failure must abort the whole pipeline (auth / missing key)."""
    err = str(exc)
    return (
        "AuthenticationError" in type(exc).__name__
        or "Incorrect API key" in err
        or "invalid_api_key" in err
        or "is not set" in err
        or ("401" in err and "api" in err.lower())
    )


#: Above this many steps, progress/summary output collapses to counts.
COMPACT_STEP_LIST_THRESHOLD = 24


def compact_step_names(
    names: List[str],
    *,
    type_of: Optional[Callable[[str], str]] = None,
    limit: int = COMPACT_STEP_LIST_THRESHOLD,
) -> str:
    """Join step names, collapsing long lists to counts by step type.

    ``type_of`` resolves a step name to its declared type (e.g. from
    ``pipeline.get_step(name).type``); without it, falls back to the name's
    hyphen-prefix as an approximation.
    """
    if not names:
        return ""
    if len(names) <= limit:
        return ", ".join(names)
    key_of = type_of or (lambda name: name.split("-")[0] if "-" in name else name)
    counts: Dict[str, int] = {}
    for name in names:
        key = key_of(name)
        counts[key] = counts.get(key, 0) + 1
    return ", ".join(f"{k}×{n}" for k, n in counts.items())


def _format_step_duration(ms: float) -> str:
    if ms < 1000:
        return f"{ms:.0f}ms"
    return f"{ms / 1000:.1f}s"


def _artifact_counts(files: List[Path]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for path in files:
        name = Path(path).name
        if name:
            counts[name] = counts.get(name, 0) + 1
    return counts


def _format_artifact_counts(counts: Dict[str, int]) -> str:
    return ", ".join(f"{name} ×{n}" for name, n in counts.items())


class _LineProgress:
    """Rewrite one stdout line on a TTY; print only completed lines otherwise."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        self._tty = bool(enabled and hasattr(sys.stdout, "isatty") and sys.stdout.isatty())

    def update(self, text: str) -> None:
        if not self.enabled or not self._tty:
            return
        sys.stdout.write(f"\r\033[K  {text}")
        sys.stdout.flush()

    def finish(self, text: str) -> None:
        if not self.enabled:
            return
        if self._tty:
            sys.stdout.write(f"\r\033[K  {text}\n")
        else:
            sys.stdout.write(f"  {text}\n")
        sys.stdout.flush()


class _TypeProgress:
    """Group live progress by step type: rewrite ``eval_mce  200/500``, then log artefacts."""

    def __init__(self, enabled: bool, totals: Dict[str, int]) -> None:
        self.line = _LineProgress(enabled)
        self.totals = totals
        self.done: Dict[str, int] = {}
        self.current: Optional[str] = None
        self._t0 = 0.0
        self._files: List[Path] = []
        self.timings: List[Tuple[str, float, int]] = []
        self.artifacts: Dict[str, int] = {}

    def begin(self, step_type: str) -> None:
        if self.current == step_type:
            return
        self.close()
        self.current = step_type
        self._t0 = time.perf_counter()
        self._files = []

    def tick(
        self,
        step_type: str,
        *,
        n: int = 1,
        files: Optional[List[Path]] = None,
    ) -> None:
        self.begin(step_type)
        self.done[step_type] = self.done.get(step_type, 0) + n
        if files:
            self._files.extend(files)
        done = self.done[step_type]
        total = self.totals.get(step_type, done)
        self.line.update(f"{step_type}  {done}/{total}")

    def close(self) -> None:
        if not self.current:
            return
        ms = (time.perf_counter() - self._t0) * 1000
        n = self.done.get(self.current, 0)
        total = self.totals.get(self.current, n)
        counts = _artifact_counts(self._files)
        for name, count in counts.items():
            self.artifacts[name] = self.artifacts.get(name, 0) + count
        extra = _format_artifact_counts(counts)
        suffix = f"   {extra}" if extra else ""
        self.line.finish(
            f"{self.current}  {n}/{total}   {_format_step_duration(ms)}{suffix}"
        )
        self.timings.append((self.current, ms, n))
        self.current = None
        self._files = []


@dataclass
class ExecutionPlan:
    """Plan for pipeline execution."""
    
    execution_order: List[str]
    """Steps in execution order."""
    
    steps_to_rerun: List[str]
    """Steps that will be rerun (cache miss or force_rerun)."""
    
    steps_cached: List[str]
    """Steps that will be skipped (cache hit)."""
    
    execution_layers: List[List[str]]
    """Layers for parallel execution."""
    
    def summary(self) -> str:
        """Human-readable summary."""
        lines = [
            f"Execution Plan:",
            f"  Total steps: {len(self.execution_order)}",
            f"  To rerun: {len(self.steps_to_rerun)} ({compact_step_names(self.steps_to_rerun) or 'none'})",
            f"  Cached: {len(self.steps_cached)} ({compact_step_names(self.steps_cached) or 'none'})",
            f"  Layers: {len(self.execution_layers)} (max parallelism: {max(len(l) for l in self.execution_layers) if self.execution_layers else 0})",
        ]
        return "\n".join(lines)


@dataclass
class ExecutionContext:
    """Context passed to step execution."""
    
    pipeline: Pipeline
    """Pipeline being executed."""
    
    output_dir: Path
    """Base output directory."""
    
    cache_manager: CacheManager
    """Cache manager."""
    
    step_outputs: Dict[str, StepOutput] = field(default_factory=dict)
    """Outputs from completed steps."""
    
    dry_run: bool = False
    """If True, don't actually execute steps."""

    template_vars: Dict[str, Any] = field(default_factory=dict)
    """User-supplied template variables (e.g. events_jsonl, run_id, ...).

    Available for ``{key}`` substitution in step config strings.
    ``output_dir`` is always pre-populated from ``ctx.output_dir``.
    """

    progress_sink: Optional[Callable[[str], None]] = None
    """Optional callable receiving JSON-encoded progress events.

    Called by batch steps after each item with a JSON string of the form::

        {"type": "fanout_progress", "step": "...", "total": N, "done": k, ...}

    The SSE server uses this to stream live progress to the UI.
    """

    def emit_progress(self, data: Dict[str, Any]) -> None:
        """Emit a progress event to the registered sink (if any)."""
        if self.progress_sink is not None:
            try:
                self.progress_sink(json.dumps(data))
            except Exception as exc:
                logger.warning("progress_sink failed: %s", exc)

    resource_registry: Optional[ResourceRegistry] = None
    """Scoped resource manager (KG instances, accumulators, etc.)."""

    scope_context: ScopeContext = field(default_factory=ScopeContext)
    """Current position in the experiment hierarchy (for resource resolution)."""

    workspace_data: Optional[Path] = None
    """Workspace-wide data root (for SHARED-scope artifacts).

    Resolved from env var ``MAS_DATA_ROOT``, experiment config ``workspace_data``,
    or :func:`mas.lab.paths.data_root` as the default.  Steps must not hardcode
    paths for shared resources; use
    :class:`~mas.lab.benchmark.pipeline.resources.Artifact` with
    ``scope=Scope.SHARED`` and call ``artifact.resolve_path(ctx)`` instead.
    """

    lab_name: str = ""
    """Name of the lab, used to derive the SHARED-scope subdirectory."""

    lab_data_dir: Optional[Path] = None
    """Directory containing static input datasets bundled with the lab.

    Derived from the lab root's ``data/`` subdirectory when a ``lab-config.yaml``
    file is found in parent directories.  Steps should use this instead of
    relative paths like ``../../data/`` in their config.
    """

    current_step: str = ""
    """Name of the step currently executing (used by :meth:`publish`)."""

    published_streams: set[str] = field(default_factory=set)
    """Stream ids this context already published onto (seal, don't re-dump)."""

    def publish(self, record: Dict[str, Any], *, stream_id: Optional[str] = None) -> None:
        """Publish one record onto the current step's output stream during ``execute``."""
        from mas.lab.benchmark.pipeline.channel import get_channel

        channel = get_channel()
        if channel is None:
            return
        sid = stream_id or self.current_step
        if not sid:
            return
        try:
            channel.publish_sync(sid, record)
            self.published_streams.add(sid)
        except Exception:
            logger.debug("ctx.publish skipped for %s", sid, exc_info=True)

    def get_dependency_output(self, step_name: str) -> Dict[str, Any]:
        """Get output data from a dependency step."""
        if step_name not in self.step_outputs:
            raise ValueError(f"Step '{step_name}' has not been executed yet")
        
        return self.step_outputs[step_name].data

    def get_resource(self, name: str) -> Any:
        """Get a scoped resource by name.

        The resource is resolved at its declared scope using the current
        ``scope_context``.  Raises ``KeyError`` if the resource is not
        declared or ``RuntimeError`` if no registry is available.
        """
        if self.resource_registry is None:
            raise RuntimeError(
                f"No resource registry — cannot resolve resource '{name}'. "
                "Declare resources in the experiment pipeline."
            )
        return self.resource_registry.get(name, self.scope_context)

    def resolve_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve ``@resource:xxx`` references in a step config dict.

        Returns the config unchanged if no resource registry is set.
        """
        if self.resource_registry is None:
            return config
        return resolve_resource_refs(config, self.resource_registry, self.scope_context)
    
    def get_step_output_dir(self, step_name: str) -> Path:
        """Get output directory for a step.

        When ``scope_context`` identifies a run, write into the run folder
        (``{output_dir}/{scenario}/{test}/{run}/``) instead of ``data/{step}/``.
        """
        sc = self.scope_context
        if sc.scenario and sc.test and sc.run:
            return self.output_dir / sc.scenario / sc.test / sc.run
        return self.output_dir / "data" / step_name
    
    def get_step_log_dir(self, step_name: str) -> Path:
        """Get log directory for a step."""
        return self.output_dir / "logs" / step_name


@dataclass
class ExecutionResult:
    """Result of pipeline execution."""
    
    success: bool
    """Whether all steps succeeded."""
    
    executed_steps: List[str]
    """Steps that were executed."""
    
    failed_steps: List[str]
    """Steps that failed."""
    
    step_outputs: Dict[str, StepOutput]
    """Outputs from all steps."""
    
    duration_ms: float
    """Total execution time in milliseconds."""

    step_timings: List[Tuple[str, float, int]] = field(default_factory=list)
    """Per-type wall time: ``(step_type, duration_ms, instance_count)``."""

    artifacts: Dict[str, int] = field(default_factory=dict)
    """Produced artefact basenames → instance counts (``data.csv`` × 500)."""

    step_errors: Dict[str, str] = field(default_factory=dict)
    """Error message of each step that raised (steps skipped for a failed dependency have none)."""
    
    def summary(self) -> str:
        """Human-readable summary."""
        status = "✓ SUCCESS" if self.success else "✗ FAILED"
        lines = [
            f"Execution Result: {status}",
            f"  Executed: {len(self.executed_steps)} steps",
            f"  Failed: {len(self.failed_steps)} steps",
            f"  Duration: {self.duration_ms:.0f}ms ({self.duration_ms/1000:.1f}s)",
        ]
        
        if self.failed_steps:
            lines.append(f"  Failed steps: {', '.join(self.failed_steps)}")

        if self.step_timings:
            lines.append("  Time:")
            for step_type, ms, n in sorted(self.step_timings, key=lambda row: -row[1]):
                inst = f" ×{n}" if n > 1 else ""
                lines.append(f"    {step_type}{inst}  {_format_step_duration(ms)}")
        if self.artifacts:
            lines.append("  Artifacts:")
            lines.append(f"    {_format_artifact_counts(self.artifacts)}")
        
        return "\n".join(lines)


@dataclass
class BoundPipeline:
    """A scheduler already wired to this executor; ``finish`` drains it."""

    executor: "PipelineExecutor"
    sched: Any
    ctx: "ExecutionContext"
    type_progress: Any
    rerun: set
    start_time: datetime

    async def mark_run_terminal(self, key: Any, status: str = "ok") -> None:
        await self.sched.mark_run_terminal(key, status)

    async def finish(self) -> ExecutionResult:
        await self.sched.drain()
        self.type_progress.close()
        executed: List[str] = []
        failed: List[str] = []
        errors: Dict[str, str] = {}
        for name, node in self.sched.nodes.items():
            if node.status == "ok" and name in self.rerun:
                executed.append(name)
            elif node.status in {"failed", "skipped"}:
                failed.append(name)
                if node.status == "failed":
                    errors[name] = str(node.result or "failed")
        duration_ms = (datetime.now() - self.start_time).total_seconds() * 1000
        result = ExecutionResult(
            success=len(failed) == 0,
            executed_steps=executed,
            failed_steps=failed,
            step_outputs=self.ctx.step_outputs,
            duration_ms=duration_ms,
            step_timings=list(self.type_progress.timings),
            artifacts=dict(self.type_progress.artifacts),
            step_errors=errors,
        )
        logger.info(result.summary())
        return result


def _find_lab_meta(
    config_path: Optional[Path],
    override_name: str = "",
) -> tuple[str, Optional[Path]]:
    """Walk parent directories to find ``lab-config.yaml`` and extract lab metadata.

    Returns ``(lab_name, lab_data_dir)`` where *lab_data_dir* is the
    ``data/`` subdirectory of the lab root, or ``None`` if not found.
    """
    if config_path is None:
        return override_name, None

    candidate = find_ancestor_with_file(
        config_path.resolve().parent, LAB_CONFIG_FILENAME, stop_at_suffix=".lab"
    )
    if candidate is not None:
        lab_yaml = candidate / LAB_CONFIG_FILENAME
        name = override_name
        if not name and _yaml is not None:
            try:
                with open(lab_yaml, encoding="utf-8") as fh:
                    data = _yaml.safe_load(fh) or {}
                name = (data.get("lab") or {}).get("name", "")
            except Exception as exc:
                logger.debug("Could not read lab name from %s: %s", lab_yaml, exc)
        if not name:
            # Fallback: strip .lab suffix from directory name
            name = candidate.name.removesuffix(".lab")
        data_dir = candidate / "data"
        return name, (data_dir if data_dir.is_dir() else None)

    # Not found — best-effort from directory name
    fallback_name = override_name or config_path.parent.parent.name.removesuffix(".lab")
    return fallback_name, None


class PipelineExecutor:
    """Executes pipelines with dependency resolution and caching."""
    
    def __init__(
        self,
        pipeline: Pipeline,
        output_dir: Optional[Path] = None,
        resource_registry: Optional[ResourceRegistry] = None,
        scope_context: Optional[ScopeContext] = None,
        progress: bool = False,
        workspace_data: Optional[Path] = None,
        lab_name: str = "",
        data_cache_dir: Optional[Path] = None,
    ):
        self.pipeline = pipeline
        self.resource_registry = resource_registry
        self.progress = progress
        self.scope_context = scope_context or ScopeContext()
        self.workspace_data: Optional[Path] = (
            workspace_data if workspace_data is not None else _paths.data_root()
        )

        # Derive lab_name and lab_data_dir by finding lab-config.yaml in parent dirs
        self.lab_name, self.lab_data_dir = _find_lab_meta(
            pipeline.config_path, override_name=lab_name
        )
        
        # Determine output directory
        if output_dir is None:
            base_dir = pipeline.config.output.get("base_dir", "./output")
            expanded = Path(base_dir).expanduser()
            if expanded.is_absolute():
                output_dir = expanded
            else:
                # Relative base_dir always anchors at the canonical lab-output
                # root — never inside the workspace tree, regardless of whether
                # a config_path is available.
                stem = pipeline.config_path.stem if pipeline.config_path else "pipeline"
                output_dir = _paths.lab_output() / stem / Path(base_dir).name
        
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Setup cache
        cache_dir = data_cache_dir if data_cache_dir is not None else self.output_dir / ".cache"
        self.cache_manager = CacheManager(cache_dir)
        
        # Setup resolver
        self.resolver = DependencyResolver(pipeline)
    
    async def run(
        self,
        steps: Optional[List[str]] = None,
        force_rerun: Optional[List[str]] = None,
        dry_run: bool = False,
        parallel: bool = True,
        template_vars: Optional[Dict[str, Any]] = None,
        progress_sink: Optional[Callable[[str], None]] = None,
    ) -> ExecutionResult:
        """Execute pipeline through the stream-engine scheduler."""
        bound = self.bind(
            steps=steps,
            force_rerun=force_rerun,
            dry_run=dry_run,
            parallel=parallel,
            template_vars=template_vars,
            progress_sink=progress_sink,
        )
        return await bound.finish()

    def bind(
        self,
        steps: Optional[List[str]] = None,
        force_rerun: Optional[List[str]] = None,
        dry_run: bool = False,
        parallel: bool = True,
        template_vars: Optional[Dict[str, Any]] = None,
        progress_sink: Optional[Callable[[str], None]] = None,
    ) -> BoundPipeline:
        """Like ``run`` but returns a live scheduler. Call ``finish`` to drain."""
        return self._bind_sync(
            steps=steps,
            force_rerun=force_rerun,
            dry_run=dry_run,
            parallel=parallel,
            template_vars=template_vars,
            progress_sink=progress_sink,
        )

    def _bind_sync(
        self,
        steps: Optional[List[str]] = None,
        force_rerun: Optional[List[str]] = None,
        dry_run: bool = False,
        parallel: bool = True,
        template_vars: Optional[Dict[str, Any]] = None,
        progress_sink: Optional[Callable[[str], None]] = None,
    ) -> BoundPipeline:
        start_time = datetime.now()
        force_rerun = set(force_rerun or [])
        execution_order = self.resolver.resolve(steps)
        ctx = ExecutionContext(
            pipeline=self.pipeline,
            output_dir=self.output_dir,
            cache_manager=self.cache_manager,
            dry_run=dry_run,
            resource_registry=self.resource_registry,
            scope_context=self.scope_context,
            workspace_data=self.workspace_data,
            lab_name=self.lab_name,
            lab_data_dir=self.lab_data_dir,
            template_vars=dict(template_vars or {}),
            progress_sink=progress_sink,
        )
        steps_to_rerun = []
        steps_cached = []
        for step_name in execution_order:
            step = self.pipeline.get_step(step_name)
            dep_outputs = {
                dep: ctx.step_outputs[dep]
                for dep in step.depends_on
                if dep in ctx.step_outputs
            }
            should_rerun = (
                step_name in force_rerun
                or self.cache_manager.should_rerun(step, dep_outputs, self.output_dir)
            )
            if should_rerun:
                steps_to_rerun.append(step_name)
            else:
                steps_cached.append(step_name)
        plan = ExecutionPlan(
            execution_order=execution_order,
            steps_to_rerun=steps_to_rerun,
            steps_cached=steps_cached,
            execution_layers=self.resolver.get_execution_layers(steps),
        )
        logger.info(plan.summary())
        if self.progress:
            print(
                f"\nPipeline: {len(execution_order)} steps "
                f"({len(steps_to_rerun)} to run, {len(steps_cached)} cached)"
            )
            if steps_cached:
                print(f"  cached: {compact_step_names(steps_cached)}")
            if steps_to_rerun:
                print(f"  to run: {compact_step_names(steps_to_rerun)}")
            print()
        if dry_run:
            for step in self.pipeline.steps:
                step.config["dry_run"] = True
        # Restore cached steps from declared artifacts (or a persisted snapshot).
        # Steps with no output_artifacts cannot be resumed — they are side-effect
        # only (e.g. experiment.pre hooks) and must re-run on every invocation.
        for step_name in list(steps_cached):
            step = self.pipeline.get_step(step_name)
            if step is None:
                continue
            artifacts = step.output_artifacts
            if not artifacts:
                logger.info("Step '%s' declares no output_artifacts — will re-run", step_name)
                steps_cached.remove(step_name)
                steps_to_rerun.append(step_name)
                continue
            restored = self.cache_manager.load_output(step_name)
            if restored is not None:
                restored.metadata["cached"] = True
                ctx.step_outputs[step_name] = restored
                continue
            data: dict = {}
            files: list = []
            failed = False
            for data_key, artifact in artifacts:
                path = getattr(artifact, "_resolved_path", None)
                if path is None:
                    path = artifact.resolve_path(ctx)
                value = artifact.load(path)
                if value is None:
                    failed = True
                    break
                data[data_key] = value
                if path.exists():
                    files.append(path)
            if failed:
                steps_cached.remove(step_name)
                steps_to_rerun.append(step_name)
            else:
                from mas.lab.benchmark.pipeline import StepOutput as _SO
                ctx.step_outputs[step_name] = _SO(
                    data=data, files=files, metadata={"cached": True}
                )
        type_totals: Dict[str, int] = {}
        for step_name in steps_to_rerun:
            step = self.pipeline.get_step(step_name)
            if step is None:
                continue
            type_totals[step.type] = type_totals.get(step.type, 0) + 1
        type_progress = _TypeProgress(self.progress, type_totals)
        return self._bind_scheduled(
            execution_order=execution_order,
            steps_to_rerun=steps_to_rerun,
            steps_cached=steps_cached,
            ctx=ctx,
            type_progress=type_progress,
            parallel=parallel,
            start_time=start_time,
        )

    def _bind_scheduled(
        self,
        *,
        execution_order: List[str],
        steps_to_rerun: List[str],
        steps_cached: List[str],
        ctx: ExecutionContext,
        type_progress: _TypeProgress,
        parallel: bool,
        start_time: datetime,
    ) -> BoundPipeline:
        """Wire the stream-engine scheduler (caller drains via BoundPipeline)."""
        from mas.lab.benchmark.pipeline.adapter import LegacyStepAdapter
        from mas.lab.benchmark.pipeline.channel import get_channel
        from mas.lab.benchmark.pipeline.config import pipeline_settings
        from mas.lab.benchmark.pipeline.channel import install_channel
        from mas.lab.benchmark.pipeline.fanout import (
            folds_batches,
            publish_output_stream,
            pump_stream,
            stream_sealed,
        )
        from mas.lab.benchmark.pipeline.scheduler import _fold_stream_id, _should_pump
        from mas.lab.benchmark.pipeline.keys import run_key_from_parts
        from mas.lab.benchmark.pipeline.scheduler import (
            Node,
            ResourcePool,
            Scheduler,
            scope_parent_id,
        )

        try:
            install_channel(pipeline_settings())
        except Exception:
            logger.debug("stream channel not installed for pipeline", exc_info=True)

        rerun = set(steps_to_rerun)
        spec_cap = 8
        try:
            spec_cap = int(pipeline_settings().max_active or 8)
        except Exception:
            spec_cap = 8
        cfg_cap = int(self.pipeline.config.max_active or 0)
        max_active = 1 if not parallel else max(1, cfg_cap or spec_cap)
        cpu_limit = max_active
        nodes: list[Node] = []
        for name in execution_order:
            step = self.pipeline.get_step(name)
            if step is None:
                continue
            resource = str(step.resource or "cpu")
            cfg = step.config or {}
            scope = str(step.scope or "experiment")
            key = None
            if scope == "run" and cfg.get("scenario"):
                key = run_key_from_parts(
                    experiment=self.lab_name or ctx.lab_name or "experiment",
                    scenario=str(cfg.get("scenario") or ""),
                    item=str(cfg.get("test") or cfg.get("item") or ""),
                    run=str(cfg.get("run") or ""),
                )
            parent = scope_parent_id(scope, cfg, key)
            concurrency = int(step.concurrency or cfg.get("concurrency") or 1)
            nodes.append(
                Node(
                    name=name,
                    scope=scope,
                    phase=str(step.phase or "post"),
                    step=step,
                    adapter=LegacyStepAdapter(step),
                    depends_on=list(step.depends_on or []),
                    resource=resource,
                    key=key,
                    parent=parent,
                    concurrency=concurrency,
                    refresh=str(step.refresh or ""),
                    ctx=ctx,
                )
            )

        bound_sched: list = []

        async def _run(node) -> Any:
            if node.name not in rerun:
                return
            step = self.pipeline.get_step(node.name)
            if step is None:
                return
            node.ctx = ctx
            channel = get_channel()
            stream_id = _fold_stream_id(node)
            live = bool(bound_sched[0]._gates) if bound_sched else False
            if (
                folds_batches(step)
                and stream_id
                and channel is not None
                and _should_pump(channel, node, stream_id, live=live)
            ):
                if ctx.resource_registry is not None:
                    step.config = ctx.resolve_config(step.config or {})
                step.config = self._resolve_config_templates(step.config or {}, ctx)
                output = await pump_stream(
                    channel,
                    stream_id,
                    step,
                    ctx,
                    key=node.key,
                )
                if output is None:
                    from mas.lab.benchmark.pipeline.models import StepOutput as _SO

                    output = _SO(data={})
                ctx.step_outputs[node.name] = output
                dep_outputs = {
                    dep: ctx.step_outputs[dep]
                    for dep in step.depends_on
                    if dep in ctx.step_outputs
                }
                fingerprint = self.cache_manager.compute_fingerprint(step, dep_outputs)
                self.cache_manager.persist_output(step, fingerprint, output)
                if type_progress is not None:
                    type_progress.tick(
                        step.type, n=1, files=list(output.files or [])
                    )
                return output
            await self._execute_step(node.name, ctx, progress=type_progress)
            output = ctx.step_outputs.get(node.name)
            if (
                channel is not None
                and output is not None
                and not stream_sealed(channel, node.name)
            ):
                try:
                    if node.name in ctx.published_streams:
                        channel.seal_sync(node.name)
                    else:
                        publish_output_stream(channel, node.name, output)
                except Exception:
                    logger.debug(
                        "publish_output_stream skipped for %s", node.name, exc_info=True
                    )
            return output

        sched = Scheduler(
            nodes,
            resources=ResourcePool({"judge": 2, "cpu": cpu_limit, "io": 8, "net": 8}),
            run=_run,
            max_active=max_active,
            channel=get_channel(),
        )
        bound_sched.append(sched)
        for name in steps_cached:
            sched.mark_done(name, "ok")
        return BoundPipeline(
            executor=self,
            sched=sched,
            ctx=ctx,
            type_progress=type_progress,
            rerun=rerun,
            start_time=start_time,
        )

    async def _execute_step(
        self,
        step_name: str,
        ctx: ExecutionContext,
        progress: Optional[_TypeProgress] = None,
    ):
        """Execute a single step."""
        step = self.pipeline.get_step(step_name)
        logger.info(f"Executing step: {step_name} (type: {step.type})")
        if progress is not None:
            progress.begin(step.type)
        
        compact = len(self.pipeline.steps) > COMPACT_STEP_LIST_THRESHOLD
        if self.progress and not compact and progress is None:
            print(f"  ▶ {step_name} ({step.type}) ...", end="", flush=True)
        
        step_start = datetime.now()
        ctx.current_step = step_name
        
        if ctx.resource_registry is not None:
            step.config = ctx.resolve_config(step.config or {})
        step.config = self._resolve_config_templates(step.config or {}, ctx)

        cfg = step.config or {}
        if cfg.get("scenario") or cfg.get("test") or cfg.get("run"):
            ctx.scope_context = ScopeContext(
                experiment=ctx.scope_context.experiment,
                scenario=str(cfg.get("scenario", "")),
                test=str(cfg.get("test", "")),
                run=str(cfg.get("run", "")),
            )

        schema_base_dir = self.pipeline.config_path.parent if self.pipeline.config_path else Path.cwd()
        input_stream = {
            dep: ctx.step_outputs[dep].data
            for dep in step.depends_on
            if dep in ctx.step_outputs
        }
        from mas.lab.benchmark.pipeline.run_artifacts import run_input_stream

        run_payload = run_input_stream(ctx, step.config)
        if run_payload:
            input_stream["_run"] = run_payload
            step.config.setdefault("run_dir", run_payload.get("run_dir", ""))
            for key in ("scenario", "test", "run", "events_path", "trace_path"):
                if run_payload.get(key) and not step.config.get(key):
                    step.config[key] = run_payload[key]
        validate_payload(
            input_stream,
            step.config.get("input_schema"),
            label=f"Step '{step_name}' input stream",
            base_dir=schema_base_dir,
        )
        output = await step.execute(ctx)

        validate_payload(
            output.data,
            step.config.get("output_schema"),
            label=f"Step '{step_name}' output data",
            base_dir=schema_base_dir,
        )
        
        step_duration = (datetime.now() - step_start).total_seconds() * 1000
        output.metadata["duration_ms"] = step_duration
        
        # Store output
        ctx.step_outputs[step_name] = output
        
        # Update cache
        dep_outputs = {
            dep: ctx.step_outputs[dep]
            for dep in step.depends_on
            if dep in ctx.step_outputs
        }
        fingerprint = self.cache_manager.compute_fingerprint(step, dep_outputs)
        self.cache_manager.persist_output(step, fingerprint, output)

        if progress is not None:
            progress.tick(step.type, n=1, files=list(output.files or []))
        elif self.progress and not compact:
            arts = _format_artifact_counts(_artifact_counts(list(output.files or [])))
            extra = f"   {arts}" if arts else ""
            _data_summary = ""
            if "rows" in output.data:
                _data_summary = f" [{output.data['rows']} rows]"
            elif "metrics_computed" in output.data:
                _mc = output.data["metrics_computed"]
                _cc = output.data.get("metrics_cached", 0)
                _data_summary = f" [computed={_mc}, cached={_cc}]"
            print(f" ✓ {_format_step_duration(step_duration)}{_data_summary}{extra}")
        
        logger.debug("✓ Step '%s' completed in %.0fms", step_name, step_duration)

    @staticmethod
    def _resolve_config_templates(
        config: Dict[str, Any],
        ctx: "ExecutionContext",
    ) -> Dict[str, Any]:
        """Substitute ``{key}`` placeholders in string config values.

        The namespace is ``output_dir`` plus ``ctx.template_vars`` (CLI
        ``--var``); the process environment is deliberately not consulted.

        Non-string values are returned unchanged.  Missing keys are left as-is
        so that steps can carry ``{key}`` literals through without erroring.
        """
        namespace: Dict[str, str] = {"output_dir": str(ctx.output_dir)}
        for k, v in ctx.template_vars.items():
            namespace[k] = str(v)

        def _substitute(value: Any) -> Any:
            if isinstance(value, str):
                return re.sub(
                    r"\{([^}]+)\}",
                    lambda m: namespace.get(m.group(1), m.group(0)),
                    value,
                )
            if isinstance(value, dict):
                return {k: _substitute(v) for k, v in value.items()}
            if isinstance(value, list):
                return [_substitute(item) for item in value]
            return value

        return _substitute(config)  # type: ignore[return-value]

    def plan(
        self,
        steps: Optional[List[str]] = None,
        force_rerun: Optional[List[str]] = None,
    ) -> ExecutionPlan:
        """Get execution plan without running.
        
        Useful for:
        - Previewing what will run
        - Understanding dependencies
        - Debugging pipeline structure
        """
        # Build plan directly (avoid async issues)
        execution_order = self.resolver.resolve(steps)
        execution_layers = self.resolver.get_execution_layers(steps)
        
        force_rerun_set = set(force_rerun or [])
        steps_to_rerun = []
        steps_cached = []
        
        for step_name in execution_order:
            if step_name in force_rerun_set:
                steps_to_rerun.append(step_name)
            else:
                # Check cache (without dependency outputs, conservative)
                cached_fp = self.cache_manager.get_cached_fingerprint(step_name)
                if cached_fp is None:
                    steps_to_rerun.append(step_name)
                else:
                    steps_cached.append(step_name)
        
        return ExecutionPlan(
            execution_order=execution_order,
            steps_to_rerun=steps_to_rerun,
            steps_cached=steps_cached,
            execution_layers=execution_layers,
        )
