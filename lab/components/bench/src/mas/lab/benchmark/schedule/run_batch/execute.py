#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0

"""Execute MAS benchmark runs according to the execution plan."""

from __future__ import annotations

import asyncio
import json as _json
import logging
import time as _time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from mas.lab.benchmark.cache.trace_store import (
    compute_run_hash,
    extract_flavour_info,
    get_trace_cache_dir,
    link_trace_to_cache_entry,
    write_cache_inputs,
    write_run_info,
    write_run_result,
)
from mas.lab.benchmark.execution import build_execution_plan, plan_kwargs
from mas.lab.benchmark.execution.completeness import result_file_is_complete, run_dir_is_complete
from mas.lab.benchmark.execution.retry import run_with_retrier
from mas.lab.benchmark.execution.roles import PlannedRun, resolve_executor, validated_plugin
from mas.lab.benchmark.execution.wire import select_runs
from mas.lab.benchmark.schedule.run_batch.load import LoadedExperiment
from mas.lab.benchmark.schedule.run_batch.prepare import PreparedBatch
from mas.lab.runners.context import RunContext
from mas.lab.runners.invoke import invoke_runner

logger = logging.getLogger(__name__)


def _infra_refs_for_scenario(scenario_id: str, prepared: PreparedBatch) -> list[str]:
    """Prefer per-scenario demo-ui cache refs when the worker set them."""
    refs = list(prepared.infra_refs)
    mapping = prepared.scenario_infra_refs
    if not isinstance(mapping, dict):
        return refs
    extra = mapping.get(scenario_id) or mapping.get(str(scenario_id).lower())
    if not extra:
        return refs
    chosen = [str(item) for item in extra if item]
    if chosen:
        return chosen
    return refs


def _run_result_row(
    *,
    scenario_id: str,
    item: dict,
    run_idx: int,
    experiment_id: str,
    prompt: str,
    status: str,
    output: str,
    error: str,
    elapsed_ms: float,
    attempts: int,
    trace_path: str = "",
    session_id: str = "",
    checkpoint_id: str = "",
    forked_from_checkpoint: str = "",
    output_length: int | None = None,
) -> dict:
    item_id = item.get("id", 0)
    text = str(output or "")
    return {
        "run_id": f"{scenario_id}__item{item_id}__r{run_idx + 1}",
        "scenario": scenario_id,
        "item_id": item_id,
        "run": run_idx + 1,
        "source_item_id": item.get("source_item_id", item_id),
        "experiment_id": experiment_id,
        "session_id": session_id,
        "checkpoint_id": checkpoint_id,
        "forked_from_checkpoint": forked_from_checkpoint,
        "group": item.get("group", ""),
        "target_agents": ",".join(item.get("target_agents", [])),
        "prompt": prompt,
        "status": status,
        "output": text[:500],
        "output_length": len(text) if output_length is None else output_length,
        "trace_path": trace_path,
        "elapsed_ms": round(float(elapsed_ms), 1),
        "error": error or "",
        "attempts": attempts,
    }


def _mirror_result(global_run_dir: Path, run_output_dir: Path) -> None:
    """Copy the cache entry's ``result.json`` into the run folder.

    The results tree then stands alone: a shard's folder carries its own outcome, with
    no need for the machine's trace cache (see ``execution/wire.py``).
    """
    source = global_run_dir / "result.json"
    if not source.is_file():
        return
    import shutil

    target = run_output_dir / "result.json"
    run_output_dir.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.tmp")
    shutil.copyfile(source, tmp)
    tmp.replace(target)


def _clear_failed_attempt_traces(global_run_dir: Path) -> None:
    """Drop a failed attempt's events so the next try starts a clean stream."""
    traces = global_run_dir / "traces"
    for name in ("events.jsonl", "otel_sdk_spans.jsonl", "observe_sdk_spans.jsonl"):
        path = traces / name
        if path.is_file() or path.is_symlink():
            path.unlink()
    result_path = global_run_dir / "result.json"
    if result_path.is_file():
        result_path.unlink()


@dataclass
class ExecutionResult:
    """Aggregated results from the run loop."""

    results_rows: list
    total_ok: int
    total_fail: int
    exit_stack: Any
    run_keys: list = field(default_factory=list)
    pipeline_drained: bool = False


def close_exit_stack(stack: Any) -> BaseException | None:
    """Stop the batch's services. A close error is logged and does not replace the original one.

    ``KeyboardInterrupt`` / ``CancelledError`` from a closer is returned so the caller can
    re-raise it after metadata; ``GeneratorExit`` / ``SystemExit`` still propagate.
    """
    try:
        stack.close()
    except Exception:
        logger.error("stopping services failed", exc_info=True)
    except BaseException as exc:
        if isinstance(exc, (GeneratorExit, SystemExit)):
            raise
        logger.error("stopping services failed", exc_info=True)
        if isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError)):
            return exc
    return None


def _run_ref_is_stale(run_output_dir: Path, run_hash: str) -> bool:
    """True when the run folder points at a different content hash than the run we are about to do."""
    ref = run_output_dir / ".run_ref"
    try:
        recorded = ref.read_text(encoding="utf-8").strip() if ref.is_file() else ""
    except OSError:
        return False
    return bool(recorded) and recorded != run_hash


async def execute_batch(
    loaded: LoadedExperiment,
    prepared: PreparedBatch,
    *,
    progress: bool = True,
    force: bool = False,
    scheduler: Optional[str] = None,
    data_cache_dir: Optional[Path] = None,
) -> ExecutionResult:
    """Run all planned (scenario × item × run) executions.

    Services started for the batch are stopped when the batch fails or is interrupted
    (``Exception``, ``KeyboardInterrupt``, cancellation); on success the stack is handed to
    the caller in :class:`ExecutionResult` and closed by ``finalize_batch``.
    """
    import contextlib

    exit_stack = contextlib.ExitStack()
    try:
        return await _execute_batch(
            loaded,
            prepared,
            exit_stack,
            progress=progress,
            force=force,
            scheduler=scheduler,
            data_cache_dir=data_cache_dir,
        )
    except BaseException:
        close_exit_stack(exit_stack)
        raise


async def _execute_batch(
    loaded: LoadedExperiment,
    prepared: PreparedBatch,
    _exit_stack: Any,
    *,
    progress: bool = True,
    force: bool = False,
    scheduler: Optional[str] = None,
    data_cache_dir: Optional[Path] = None,
) -> ExecutionResult:
    from mas.ctl.preload import preload_run_path
    from mas.lab.benchmark.service_manager import ServiceManager as _ServiceManager
    from mas.lab.emulation_resolver import resolve_emulation_plugins

    exp = loaded.exp

    imported = await asyncio.to_thread(preload_run_path)
    if imported:
        logger.info("preloaded %d plugin modules before the first run", len(imported))
    experiment_yaml = loaded.experiment_yaml
    from mas.lab.benchmark.host_overrides import capture as _host_capture

    _flavour = loaded.flavour
    _flavour_name = loaded.flavour_name
    trace_cache_dir = loaded.trace_cache_dir
    output_dir = prepared.output_dir
    from mas.lab.benchmark.schedule.whatif import (
        apply_session_checkpoint_triggers,
        validate_scenario_graph,
    )

    validate_scenario_graph(exp.scenarios, getattr(exp, "session_checkpoints", None) or [])
    apply_session_checkpoint_triggers(
        exp.scenarios,
        getattr(exp, "session_checkpoints", None) or [],
        base_dir=experiment_yaml.parent,
    )

    _mas_manifest_path = exp.mas.manifest if (exp.mas and exp.mas.manifest) else experiment_yaml

    _svc_mgr = _ServiceManager.for_benchmark(
        flavour=_flavour,
        experiment_dir=experiment_yaml.parent,
        output_dir=output_dir,
    )
    _exit_stack.enter_context(_svc_mgr)

    from mas.library_roots import clear_library_discovery_cache
    from mas.runtime.spec.batch_cache import BatchCache, bind_batch_cache, reset_batch_cache

    clear_library_discovery_cache()
    _batch_cache = BatchCache()
    _batch_token = bind_batch_cache(_batch_cache)
    _batch_released: list[bool] = []

    def _release_batch_cache() -> None:
        if _batch_released:
            return
        _batch_released.append(True)
        reset_batch_cache(_batch_token)
        _batch_cache.clear()
        clear_library_discovery_cache()

    _exit_stack.callback(_release_batch_cache)

    results_rows: list = []
    total_ok = 0
    total_fail = 0

    _schedule = exp.schedule
    _executor = exp.executor
    _executor_plugin = resolve_executor(_executor.plugin)
    _retrier = validated_plugin("retrier", exp.retry.plugin, dict(exp.retry.params))
    _plan_kw = plan_kwargs(exp, plugin_override=scheduler)
    _effective_scheduler = _plan_kw["schedule"].get("plugin")
    _execution_plan = build_execution_plan(
        prepared.loaded_ids,
        prepared.dataset_items,
        loaded.n_runs,
        declared_scenario_ids=list(loaded.declared_scenario_ids) or None,
        **_plan_kw,
    )
    _attempts_before: dict[str, int] = {}
    if loaded.runs_plan is not None:
        _execution_plan, _attempts_before = select_runs(_execution_plan, loaded.runs_plan)

    from mas.lab.benchmark.environment.attach import validate_environments

    validate_environments(exp=exp, plan=_execution_plan)
    _worker_processes = _executor.worker_processes
    if _worker_processes > 1:
        from mas.lab.benchmark.environment.attach import refuse_environment_in_workers

        refuse_environment_in_workers(exp=exp, plan=_execution_plan)

    _parallel = max(1, _schedule.parallel_scenarios or 1)
    _pause = max(0.0, _schedule.pause_between_runs or 0.0)
    _worker_max_runs = _executor.worker_max_runs
    _sem = asyncio.Semaphore(_parallel)

    _io_lock = asyncio.Lock()
    _run_pool = None
    _done: dict[tuple, asyncio.Event] = {}
    _dep_status: dict[tuple, str] = {}
    for _plan_sid, _plan_item, _plan_ridx in _execution_plan:
        _done[(_plan_sid, _plan_item.get("id", 0), _plan_ridx)] = asyncio.Event()

    async def _wait_published_checkpoint(
        scenario_id: str,
        dep: str,
        name: str,
        index: int,
        item: dict,
        run_idx: int,
    ) -> None:
        from mas.lab.benchmark.schedule.whatif import checkpoint_key, published_checkpoint_path

        path = published_checkpoint_path(output_dir, name, index=int(index), key=checkpoint_key(item, run_idx))
        item_id = item.get("source_item_id", item.get("id", 0))
        keys = (
            (dep, item_id, run_idx),
            (dep, item.get("id", 0), run_idx),
        )
        started = _time.monotonic()
        next_report = 30.0
        while True:
            if path.is_file():
                return
            waited = _time.monotonic() - started
            if waited >= next_report:
                logger.warning(
                    "scenario %s: still waiting %.0fs for checkpoint %s from %r",
                    scenario_id,
                    waited,
                    path,
                    dep,
                )
                next_report += 30.0
            status = next((s for k in keys if (s := _dep_status.get(k))), None)
            ev = next((e for k in keys if (e := _done.get(k))), None)
            finished = ev is not None and ev.is_set()
            if status and status != "ok":
                raise RuntimeError(f"scenario {scenario_id}: producer {dep!r} ended with status {status}")
            if finished and not path.is_file():
                raise FileNotFoundError(
                    f"scenario {scenario_id}: resume_from {name!r} index {index} not published at {path}"
                )
            await asyncio.sleep(0.05)

    async def _wait_dependencies(scenario_id: str, item: dict, run_idx: int) -> None:
        spec = exp.get_scenario(scenario_id)
        if spec is None:
            return
        from mas.lab.benchmark.schedule.whatif import consumer_gate

        item_id = item.get("source_item_id", item.get("id", 0))
        gate, resume_id, resume_index = consumer_gate(spec)
        for dep in getattr(spec, "depends_on", None) or []:
            key = (dep, item_id, run_idx)
            event = _done.get(key) or _done.get((dep, item.get("id", 0), run_idx))
            if event is None:
                raise ValueError(f"scenario {scenario_id}: depends_on {dep!r} is not in the execution plan")
            dep_spec = exp.get_scenario(dep)
            produced = list(getattr(dep_spec, "produce_checkpoints", None) or [])
            if gate == "checkpoint" and resume_id and resume_id in produced:
                await _wait_published_checkpoint(scenario_id, dep, resume_id, resume_index, item, run_idx)
                continue
            await event.wait()
            status = _dep_status.get(key) or _dep_status.get((dep, item.get("id", 0), run_idx))
            if status and status != "ok":
                raise RuntimeError(f"scenario {scenario_id}: producer {dep!r} ended with status {status}")

    from mas.lab.benchmark.pipeline.channel import get_channel, install_channel
    from mas.lab.benchmark.pipeline.config import pipeline_settings
    from mas.lab.benchmark.pipeline.keys import run_key_from_parts
    from mas.lab.benchmark.pipeline.lifecycle import attempt_reset, begin_run, terminal_run
    from mas.runtime.boundary.obs.event_stream import set_channel_emitter

    _v2_settings = pipeline_settings(exp)
    _v2_keys: list = []
    install_channel(_v2_settings, cache_root=get_trace_cache_dir(explicit=trace_cache_dir))

    _v2_plan_keys: list = []
    for _sid, _itm, _ridx in _execution_plan:
        _item_id = _itm.get("id", 0)
        _v2_plan_keys.append(
            run_key_from_parts(
                experiment=getattr(exp, "name", "") or "experiment",
                scenario=_sid,
                item=f"item{_item_id}",
                run=f"r{_ridx + 1}",
            )
        )
    _live_post = None
    if loaded.pipeline_specs:
        try:
            from mas.lab.benchmark.schedule.pipeline import start_pipeline_scheduler

            _live_post = start_pipeline_scheduler(
                exp=exp,
                experiment_yaml=experiment_yaml,
                output_dir=output_dir,
                specs=loaded.pipeline_specs,
                keys=_v2_plan_keys,
                scenario_ids=list(prepared.loaded_ids),
                infra_name=loaded.infra_name,
                step_overrides=loaded.step_overrides_dict,
                progress=progress,
                data_cache_dir=data_cache_dir,
            )
        except Exception:
            logger.debug("live run-post not started", exc_info=True)
            _live_post = None

    from mas.lab.benchmark.pipeline.ui_bridge import LiveRunSink

    _live_sink = LiveRunSink()
    set_channel_emitter(_live_sink.emit)

    if _worker_processes > 1:
        from mas.lab.benchmark.schedule.run_batch.workers import require_cores, require_worker_safe

        require_cores(_worker_processes)
        require_worker_safe(loaded.pipeline_specs, base_dir=experiment_yaml.parent)

    _emulation = exp.bench_emulation
    _cache_policy = _emulation.runtime.cache

    if progress:
        print()
        print(f"Running MAS benchmark '{exp.name}'")
        print(f"  schedule   : {_effective_scheduler or 'default'}")
        print(f"  executor   : {_executor.plugin}")
        n_sc, n_items = len(prepared.loaded_ids), len(prepared.dataset_items)
        print(f"  {n_sc} scenarios × {n_items} items × {loaded.n_runs} run(s) = {len(_execution_plan)} executions")
        from mas.lab.benchmark.execution.plan import skipped_by_scenario

        skipped = skipped_by_scenario(prepared.loaded_ids, prepared.dataset_items, design=_plan_kw["design"])
        print(f"  {skipped} items skipped by scenario")
        print(f"  parallel   : {_parallel}   pause: {_pause}s   retry: {exp.retry.plugin} {dict(exp.retry.params)}")
        if _worker_processes > 1:
            print(f"  workers    : {_worker_processes} processes")
        if _emulation:
            _inf = _emulation.infra
            _state_parts = [f"llm={_inf.llm}", f"tools={_inf.tools}", f"memory={_inf.memory}"]
            if _inf.embeddings != "live":
                _state_parts.append(f"embeddings={_inf.embeddings}")
            if _inf.state != "live":
                _state_parts.append(f"state={_inf.state}")
            _state_parts.append(f"cache={_cache_policy}")
            print(f"  emulation  : {'  '.join(_state_parts)}")
        print()

    _faults_config = _emulation.faults
    _faults_dict = _faults_config if isinstance(_faults_config, dict) else None
    _emulation_plugins = resolve_emulation_plugins(
        _emulation,
        faults_config=_faults_dict,
    )

    from mas.ctl.compose.runner import infra_cache_identity, resolve_workspace_infra
    from mas.ctl.workspace.config import WorkspaceConfig

    # Runs resolve infra from the experiment workspace; the cache key must too.
    # ``trace_cache_portable`` drops the infra from the key so a published
    # cache hits on any LLM endpoint (see ExperimentConfig.trace_cache_portable).
    if getattr(exp, "trace_cache_portable", False):
        _infra_info = None
    else:
        _infra_info = infra_cache_identity(
            resolve_workspace_infra(
                WorkspaceConfig.load(experiment_yaml.parent),
                anchor=experiment_yaml.parent,
                cli_refs=list(prepared.infra_refs),
            )
        )

    from mas.lab.benchmark.schedule.run_batch.pre_hooks import LevelPreHooks

    _pre_hooks = LevelPreHooks(
        exp=exp,
        experiment_yaml=experiment_yaml,
        output_dir=output_dir,
        specs=loaded.pipeline_specs,
        scenario_ids=prepared.loaded_ids,
        infra_name=loaded.infra_name,
        step_overrides=loaded.step_overrides_dict,
        data_cache_dir=data_cache_dir,
        progress=progress,
    )

    async def _run_one(scenario_id: str, item: dict, run_idx: int) -> None:
        nonlocal total_ok, total_fail
        await _wait_dependencies(scenario_id, item, run_idx)
        _sc_whatif = exp.get_scenario(scenario_id)
        if _sc_whatif is not None and getattr(_sc_whatif, "resume_from", None):
            from mas.lab.benchmark.schedule.whatif import checkpoint_key, published_checkpoint_path

            ck_path = published_checkpoint_path(
                output_dir,
                _sc_whatif.resume_from,
                index=int(getattr(_sc_whatif, "resume_index", 1) or 1),
                key=checkpoint_key(item, run_idx),
            )
            if not ck_path.is_file():
                raise FileNotFoundError(
                    f"scenario {scenario_id}: resume_from {_sc_whatif.resume_from!r} "
                    f"index {getattr(_sc_whatif, 'resume_index', 1)} "
                    f"not published at {ck_path}"
                )
            inputs = dict(item.get("inputs") or {})
            checkpoint = dict(inputs.get("checkpoint") or {})
            checkpoint["load"] = str(ck_path)
            inputs["checkpoint"] = checkpoint
            item = dict(item)
            item["inputs"] = inputs
            item["checkpoint_id"] = _sc_whatif.resume_from
            item["forked_from_checkpoint"] = str(ck_path)
        if _sc_whatif is not None and getattr(_sc_whatif, "produce_checkpoints", None):
            inputs = dict(item.get("inputs") or {})
            checkpoint = dict(inputs.get("checkpoint") or {})
            checkpoint["save"] = True
            inputs["checkpoint"] = checkpoint
            item = dict(item)
            item["inputs"] = inputs
        config, spec_path = prepared.scenario_configs[scenario_id]
        item_id = item.get("id", 0)

        from mas.lab.inputs import load_run_input, run_input_to_dict

        _sc_spec_for_input = exp.get_scenario(scenario_id)
        _scenario_raw = None
        if _sc_spec_for_input is not None:
            _scenario_raw = {
                "inputs": getattr(_sc_spec_for_input, "inputs", {}) or {},
                "expectations": getattr(_sc_spec_for_input, "expectations", {}) or {},
            }
        _run_input = load_run_input(
            item,
            scenario=_scenario_raw,
            base_path=exp.base_dir if hasattr(exp, "base_dir") else output_dir.parent,
        )
        prompt = _run_input.primary_prompt
        _run_input_dict = run_input_to_dict(_run_input)
        checkpoint_id = str(item.get("checkpoint_id") or "")
        forked_from_checkpoint = str(item.get("forked_from_checkpoint") or "")
        if not forked_from_checkpoint and _run_input.checkpoint_load:
            forked_from_checkpoint = str(_run_input.checkpoint_load)
            if not checkpoint_id:
                checkpoint_id = Path(forked_from_checkpoint).stem
        if not prompt:
            raise ValueError(f"dataset item {item_id!r} has an empty inputs.user prompt")
        t0 = _time.monotonic()
        error: Optional[str] = None
        output = ""
        status = "ok"

        run_id = f"{scenario_id}__item{item_id}__r{run_idx + 1}"

        _sc_flavour = _flavour
        _sc_spec_flavour = exp.get_scenario(scenario_id)
        if _sc_spec_flavour and getattr(_sc_spec_flavour, "flavour", None):
            _sc_flavour = prepared.scenario_flavours.get(scenario_id, _flavour)

        if loaded.controls:
            from mas.ctl.session.flavour_control import control_search_dirs, session_flavour_with_controls

            _sc_flavour = session_flavour_with_controls(
                _sc_flavour,
                controls=list(loaded.controls),
                search_dirs=control_search_dirs(experiment_yaml.parent, Path.cwd()),
            )

        _flavour_info = extract_flavour_info(_sc_flavour)
        from mas.lab.benchmark.environment.attach import (
            bind_run_environment,
            blocks_for_run,
            control_refs_for_run,
            digest_for_run,
            render_infra_refs,
            replay_cached_environment,
            start_run_fixtures,
            stop_run_fixtures,
            write_fixture_llm_infra,
        )
        from mas.lab.benchmark.environment.engine import compile_environment, write_environment_control
        from mas.lab.benchmark.environment.model import RunCoordinates

        _env_block = blocks_for_run(exp=exp, scenario=exp.get_scenario(scenario_id), item=item)
        _env_digest = digest_for_run(
            exp=exp,
            scenario=exp.get_scenario(scenario_id),
            item=item,
            work_dir=experiment_yaml.parent,
        )
        _run_hash = compute_run_hash(
            config,
            _run_input_dict,
            item_id,
            run_idx,
            _flavour_info,
            base_path=spec_path,
            infra_info=_infra_info,
            environment_digest=_env_digest,
        )
        _trace_cache = get_trace_cache_dir(explicit=trace_cache_dir)
        _global_run_dir = _trace_cache / _run_hash
        _cached_events = _global_run_dir / "traces" / "events.jsonl"

        _sc_spec = exp.get_scenario(scenario_id)
        _sc_dir_name = _sc_spec.output_dir_name if _sc_spec else scenario_id
        run_output_dir = output_dir / _sc_dir_name / f"item{item_id}" / f"r{run_idx + 1}"
        run_output_dir.mkdir(parents=True, exist_ok=True)
        _events_path = run_output_dir / "traces" / "events.jsonl"
        _v2_key = run_key_from_parts(
            experiment=getattr(exp, "name", "") or "experiment",
            scenario=scenario_id,
            item=f"item{item_id}",
            run=f"r{run_idx + 1}",
        )
        _v2_keys.append(_v2_key)
        _use_cache = _cache_policy != "disabled"

        # A global cache hit requires the trace to exist AND the prior run to
        # have completed successfully.  Absent result.json means the run crashed
        # before writing its outcome (also a miss).  A run with status=error
        # may have written a partial events.jsonl — reusing it silently returns
        # wrong/empty results and corrupts downstream evaluation metrics.
        _global_result_path = _global_run_dir / "result.json"
        _global_result_ok = result_file_is_complete(_global_result_path)
        if _global_result_path.exists() and not _global_result_ok:
            logger.debug("Cache miss (prior run incomplete): hash=%s", _run_hash[:12])

        _global_hit = (
            _use_cache
            and not force
            and _cached_events.exists()
            and _cached_events.stat().st_size > 0
            and _global_result_ok
        )
        _local_hit = (
            _use_cache
            and not force
            and not _global_hit
            and not _events_path.is_symlink()
            # A traces/ directory that links into the trace cache names ONE content hash. We are
            # past the global lookup for the current hash, so that link is a different, stale run:
            # a changed config, overlay, fixture script or environment must run again.
            and not _events_path.parent.is_symlink()
            and not _run_ref_is_stale(run_output_dir, _run_hash)
            and _events_path.exists()
            and _events_path.stat().st_size > 0
            and run_dir_is_complete(run_output_dir)
        )
        if _cache_policy == "forced" and not _global_hit and not _local_hit:
            error = f"emulation.runtime.cache=forced but no cache entry for hash {_run_hash[:12]}"
            status = "error"
            async with _io_lock:
                total_fail += 1
                results_rows.append(
                    _run_result_row(
                        scenario_id=scenario_id,
                        item=item,
                        run_idx=run_idx,
                        experiment_id=exp.name,
                        prompt=prompt,
                        status=status,
                        output="",
                        error=error,
                        elapsed_ms=0.0,
                        attempts=0,
                        checkpoint_id=checkpoint_id,
                        forked_from_checkpoint=forked_from_checkpoint,
                    )
                )
            if progress:
                print(f"  ❌ [{scenario_id}] item={item_id} run={run_idx + 1} (cache=forced, miss)")
            if _live_post is not None:
                await _live_post.on_terminal(_v2_key, "skipped")
            return
        if _global_hit or _local_hit:
            _src = "cached:" + _run_hash[:8] if _global_hit else "local"
            if _global_hit:
                link_trace_to_cache_entry(run_output_dir, _global_run_dir, _run_hash)
            # write_run_info is idempotent (no-ops once cached, repoints a stale
            # symlink) so it's safe — and necessary — to call on every hit.
            write_run_info(
                _global_run_dir,
                run_output_dir,
                _run_hash,
                exp.name,
                scenario_id,
                item_id,
                run_idx,
                app=prepared.mas_app,
                app_version=prepared.mas_app_version,
                mas_ref=prepared.mas_ref,
                overlay_ref=prepared.scenario_overlay_refs.get(scenario_id, ""),
            )
            _mirror_result(_global_run_dir, run_output_dir)
            if progress:
                print(f"  ⏩  [{scenario_id}] item={item_id} run={run_idx + 1} ({_src}, skipping)")
            if _sc_whatif is not None and getattr(_sc_whatif, "produce_checkpoints", None):
                from mas.lab.benchmark.schedule.whatif import checkpoint_key, publish_named_checkpoints

                publish_named_checkpoints(
                    run_checkpoint_dir=_global_run_dir / "checkpoints",
                    output_dir=output_dir,
                    names=_sc_whatif.produce_checkpoints,
                    key=checkpoint_key(item, run_idx),
                )
            resolved_events = _cached_events if _global_hit else _events_path
            async with _io_lock:
                results_rows.append(
                    _run_result_row(
                        scenario_id=scenario_id,
                        item=item,
                        run_idx=run_idx,
                        experiment_id=exp.name,
                        prompt=prompt,
                        status="ok",
                        output="(cached)",
                        error="",
                        elapsed_ms=0.0,
                        attempts=0,
                        trace_path=str(resolved_events),
                        checkpoint_id=checkpoint_id,
                        forked_from_checkpoint=forked_from_checkpoint,
                        output_length=0,
                    )
                )
                total_ok += 1
            begin_run(_v2_key)
            try:
                if resolved_events.is_file():
                    for line in resolved_events.read_text(encoding="utf-8").splitlines():
                        if not line.strip():
                            continue
                        try:
                            rec = _json.loads(line)
                        except _json.JSONDecodeError:
                            continue
                        if isinstance(rec, dict):
                            _live_sink.emit(rec)
                replay_cached_environment(_global_run_dir)
            except Exception:
                logger.debug("cache-hit stream replay skipped", exc_info=True)
            terminal_run(_v2_key, "cached")
            if _live_post is not None:
                await _live_post.on_terminal(_v2_key, "cached")
            return

        if _events_path.is_symlink():
            _events_path.unlink()
        elif _events_path.exists():
            _events_path.unlink()
        for _span_name in ("otel_sdk_spans.jsonl", "observe_sdk_spans.jsonl"):
            for _traces_root in (run_output_dir / "traces", _global_run_dir / "traces"):
                _span_path = _traces_root / _span_name
                if _span_path.is_file():
                    _span_path.unlink()
        # Remove the traces dir/symlink from run_output_dir so that
        # link_trace_to_cache_entry (called in the finally block) can create a
        # clean symlink run_output_dir/traces → cache/<hash>/traces.  Without
        # this, a pre-existing real directory would block symlink creation and
        # cause a .run_ref-only fallback.
        _run_traces_dir = run_output_dir / "traces"
        if _run_traces_dir.is_symlink():
            _run_traces_dir.unlink()
        elif _run_traces_dir.is_dir():
            # Safety: only rmtree a directory that is strictly under run_output_dir.
            # Guards against the (unlikely) case where run_output_dir itself is a
            # symlink whose resolved path puts traces/ outside the experiment tree.
            if _run_traces_dir.resolve().is_relative_to(run_output_dir.resolve()):
                import shutil as _shutil_exec

                _shutil_exec.rmtree(_run_traces_dir)
            else:
                logger.warning(
                    "Skipping rmtree: %s resolves outside run_output_dir %s",
                    _run_traces_dir,
                    run_output_dir,
                )
        _cached_events_backup: Optional[Path] = None
        if force and _cached_events.exists():
            _cached_events_backup = _cached_events.with_suffix(".jsonl.bak")
            _cached_events.rename(_cached_events_backup)

        async with _sem:
            if _pause > 0:
                await asyncio.sleep(_pause)

            _global_run_dir.mkdir(parents=True, exist_ok=True)
            result_dict: dict = {}
            _attempts_used = 0
            _watch: asyncio.Task[None] | None = None
            if _sc_whatif is not None and getattr(_sc_whatif, "produce_checkpoints", None):
                from mas.lab.benchmark.schedule.whatif import checkpoint_key, publish_named_checkpoints

                _ck_key = checkpoint_key(item, run_idx)

                async def _publish_ready() -> None:
                    reported: set[str] = set()
                    while True:
                        try:
                            publish_named_checkpoints(
                                run_checkpoint_dir=_global_run_dir / "checkpoints",
                                output_dir=output_dir,
                                names=_sc_whatif.produce_checkpoints,
                                missing_ok=True,
                                key=_ck_key,
                            )
                        except Exception as exc:
                            if str(exc) not in reported:
                                reported.add(str(exc))
                                logger.warning("checkpoint publish for %s: %s", scenario_id, exc)
                        await asyncio.sleep(0.05)

                _watch = asyncio.create_task(_publish_ready())
            try:

                def _do_mas_run() -> dict:
                    from mas.lab.benchmark.environment.attach import run_with_cleanups

                    return run_with_cleanups(_do_mas_run_body)

                def _do_mas_run_body(_pending: list) -> dict:
                    from mas.lab.benchmark.pipeline.ui_bridge import forward_native_to_channel
                    from mas.lab.runners.infer import infer_runner_id
                    from mas.runtime.boundary.obs.event_stream import (
                        EventStream,
                        reset_event_stream,
                        set_event_stream,
                    )

                    _runner_id = infer_runner_id(
                        execution_runner=_executor.runner,
                        mas_manifest=(Path(_mas_manifest_path) if _mas_manifest_path is not None else None),
                        agent_config=config,
                        flavour=_sc_flavour if isinstance(_sc_flavour, dict) else None,
                    )
                    _overlay_refs = list(prepared.scenario_overlay_stacks.get(scenario_id, []))
                    _infra_refs = _infra_refs_for_scenario(scenario_id, prepared)
                    if _run_pool is not None:
                        from dataclasses import asdict, is_dataclass

                        from mas.lab.benchmark.pipeline.channel import reset_stream_id
                        from mas.lab.benchmark.schedule.run_batch.workers import plain, run_input_payload

                        emulation_payload = None
                        if _emulation is not None and is_dataclass(_emulation) and not isinstance(_emulation, type):
                            emulation_payload = plain(asdict(_emulation), "emulation")
                        _sid_token = begin_run(_v2_key)
                        try:
                            return _run_pool.run_job(
                                {
                                    "job_id": f"{run_id}-{_time.time_ns()}",
                                    "scenario_id": scenario_id,
                                    "item_id": item_id,
                                    "run_idx": run_idx,
                                    "stream_id": _v2_key.stream_id(),
                                    "prompt": prompt,
                                    "config": plain(config, "config"),
                                    "spec_path": str(spec_path),
                                    "output_dir": str(_global_run_dir),
                                    "runner_id": _runner_id,
                                    "run_input": run_input_payload(_run_input),
                                    "flavour": None if _sc_flavour is None else plain(_sc_flavour, "flavour"),
                                    "run_seed": run_idx,
                                    "overlay_refs": plain(_overlay_refs, "overlay_refs"),
                                    "overlays_dir": str(prepared.overlays_dir) if prepared.overlays_dir else None,
                                    "overlay_base_dir": str(prepared.overlay_base_dir)
                                    if prepared.overlay_base_dir
                                    else None,
                                    "infra_refs": list(_infra_refs),
                                    "session_id": _run_input.session_id,
                                    "host": _host_capture(),
                                    "emulation": emulation_payload,
                                    "faults": plain(_faults_dict, "faults") if _faults_dict is not None else None,
                                    "extra": plain(
                                        {
                                            "experiment_default_model": getattr(exp, "model", None),
                                            "experiment_model_slots": getattr(exp, "model_slots", None),
                                            "workspace_root": str(experiment_yaml.parent),
                                            "controls": list(loaded.controls or ()),
                                            "overrides": list(loaded.run_overrides or ()),
                                        },
                                        "extra",
                                    ),
                                }
                            )
                        finally:
                            if _sid_token is not None:
                                reset_stream_id(_sid_token)

                    class _ScenarioStream(EventStream):
                        def publish(self, event: dict) -> None:
                            if scenario_id and isinstance(event, dict) and not event.get("scenario"):
                                event = {**event, "scenario": scenario_id}
                            super().publish(event)

                    _stream = _ScenarioStream()
                    _stream.subscribe(forward_native_to_channel)
                    _token = set_event_stream(_stream)
                    _pending.append(lambda tok=_token: reset_event_stream(tok))
                    _sid_token = begin_run(_v2_key)
                    _env_spec = compile_environment(_env_block)
                    _bound = None
                    _run_flavour = _sc_flavour
                    _ctl_path = None
                    if _env_spec.triggers or _env_spec.fixtures or _env_spec.script:
                        if _env_spec.triggers or _env_spec.script:
                            _ctl_path = write_environment_control(
                                run_output_dir / ".environment" / "control.yaml",
                                script=_env_spec.script,
                            )
                            from mas.ctl.session.flavour_control import (
                                control_search_dirs,
                                session_flavour_with_controls,
                            )

                            _run_flavour = session_flavour_with_controls(
                                _run_flavour,
                                controls=[str(_ctl_path)],
                                search_dirs=control_search_dirs(experiment_yaml.parent, Path.cwd()),
                            )
                        _bound = bind_run_environment(
                            spec=_env_spec,
                            run=RunCoordinates(
                                experiment=str(getattr(exp, "name", "") or ""),
                                scenario=scenario_id,
                                item=f"item{item_id}",
                                run=f"r{run_idx + 1}",
                            ),
                            run_dir=_global_run_dir,
                            work_dir=experiment_yaml.parent,
                            output_dir=output_dir,
                            fixtures=prepared.fixture_registry,
                            key=_v2_key,
                            session_id=str(_run_input.session_id or ""),
                        )
                        _pending.append(_bound.close)
                    try:
                        from mas.lab.benchmark.pipeline.channel import set_stream_id
                    except Exception:
                        set_stream_id = None  # type: ignore[assignment]
                    if set_stream_id is not None and _sid_token is None:
                        _sid_token = set_stream_id(_v2_key.stream_id())
                    if _bound is not None:
                        _fixture_keys = {
                            "run_key": f"{scenario_id}/item{item_id}/r{run_idx + 1}",
                            "item_key": f"{scenario_id}/item{item_id}",
                            "scenario_key": str(scenario_id),
                        }
                        # Registered before the start: a failure halfway still releases the first ones.
                        _pending.append(
                            lambda: stop_run_fixtures(_env_spec, prepared.fixture_registry, **_fixture_keys)
                        )
                        start_run_fixtures(
                            _env_spec,
                            prepared.fixture_registry,
                            env=_bound.env,
                            **_fixture_keys,
                        )

                    if _bound is not None:
                        # The run's fixtures are up: infra files may name them (`${fixture.<name>.url}`).
                        _infra_refs = render_infra_refs(
                            _infra_refs, _bound.env.vars, run_output_dir / ".environment" / "infra"
                        )
                        # The control this environment generated is part of the flavour the runner sees.
                        _runner_id = infer_runner_id(
                            execution_runner=_executor.runner,
                            mas_manifest=(Path(_mas_manifest_path) if _mas_manifest_path is not None else None),
                            agent_config=config,
                            flavour=_run_flavour if isinstance(_run_flavour, dict) else None,
                        )
                        try:
                            _model_url = _bound.env.fixture_url("scripted-model")
                        except KeyError:
                            _model_url = ""
                        if _model_url:
                            _infra_refs = [
                                *_infra_refs,
                                str(
                                    write_fixture_llm_infra(
                                        run_output_dir / ".environment" / "openai-direct.yaml",
                                        _model_url,
                                    )
                                ),
                            ]
                    ctx = RunContext(
                        prompt=prompt,
                        config=config,
                        spec_path=spec_path,
                        output_dir=_global_run_dir,
                        runner_id=_runner_id,
                        run_input=_run_input,
                        flavour=_run_flavour,
                        run_seed=run_idx,
                        overlay_refs=_overlay_refs,
                        overlays_dir=prepared.overlays_dir,
                        overlay_base_dir=prepared.overlay_base_dir,
                        infra_refs=_infra_refs,
                        session_id=_run_input.session_id,
                        emulation_plugins=_emulation_plugins,
                        batch_cache=_batch_cache,
                        extra={
                            "experiment_default_model": getattr(exp, "model", None),
                            "experiment_model_slots": getattr(exp, "model_slots", None),
                            "workspace_root": experiment_yaml.parent,
                            "controls": control_refs_for_run(
                                loaded=list(loaded.controls or ()),
                                control_path=_ctl_path,
                            ),
                            "overrides": list(loaded.run_overrides or ()),
                        },
                    )
                    try:
                        from mas.lab.benchmark.pipeline.ui_bridge import (
                            pop_demo_ui_scope,
                            push_demo_ui_scope,
                        )
                    except Exception:
                        push_demo_ui_scope = None  # type: ignore[assignment]
                        pop_demo_ui_scope = None  # type: ignore[assignment]
                    _scope = None
                    if push_demo_ui_scope is not None:
                        _ov_ref = str(prepared.scenario_overlay_refs.get(scenario_id) or "")
                        _ov_name = Path(_ov_ref).stem if _ov_ref else ""
                        _scope = push_demo_ui_scope(
                            {
                                "scenario": scenario_id,
                                "experiment": getattr(exp, "name", None),
                                "overlay": _ov_name or None,
                            }
                        )
                    _pending.clear()  # the try/finally below owns the cleanup from here
                    try:
                        result = invoke_runner(ctx)
                        return {
                            "content": result.content,
                            "status": result.status,
                            "usage": result.metadata.get("usage", {}),
                            "agent_id": result.metadata.get("agent_id", ""),
                            "session_id": result.metadata.get("session_id", ""),
                        }
                    finally:
                        if _scope is not None and pop_demo_ui_scope is not None:
                            pop_demo_ui_scope(_scope)
                        try:
                            from mas.lab.benchmark.pipeline.channel import reset_stream_id

                            if _sid_token is not None:
                                reset_stream_id(_sid_token)
                        except Exception:
                            pass
                        reset_event_stream(_token)
                        if _bound is not None:
                            try:
                                stop_run_fixtures(
                                    _env_spec,
                                    prepared.fixture_registry,
                                    run_key=f"{scenario_id}/item{item_id}/r{run_idx + 1}",
                                    item_key=f"{scenario_id}/item{item_id}",
                                    scenario_key=str(scenario_id),
                                )
                            except Exception:
                                logger.debug("run fixture stop failed", exc_info=True)
                            _bound.close()

                def _on_retry(attempt_no: int, err: str) -> None:
                    attempt_reset(_v2_key)
                    _clear_failed_attempt_traces(_global_run_dir)
                    logger.warning(
                        "lab run %s failed (%s); retrying after attempt %s",
                        run_id,
                        (err or "")[:160],
                        attempt_no,
                    )
                    if progress:
                        print(f"  ⚠️  [{scenario_id}] item={item_id} run={run_idx + 1} retry after attempt {attempt_no}")

                _outcome = await run_with_retrier(
                    _executor_plugin,
                    _retrier,
                    _do_mas_run,
                    attempts_before=_attempts_before.get((PlannedRun(scenario_id, item, run_idx)).id, 0),
                    on_retry=_on_retry,
                    before_attempt=(
                        (
                            lambda: _pre_hooks.before_attempt(
                                scenario=_sc_dir_name,
                                test=f"item{item_id}",
                                run=f"r{run_idx + 1}",
                            )
                        )
                        if _pre_hooks.active
                        else None
                    ),
                )
                result_dict, status, error = _outcome.result, _outcome.status, _outcome.error
                output, _attempts_used = _outcome.output, _outcome.attempts
                write_cache_inputs(
                    _global_run_dir,
                    _run_hash,
                    _run_input_dict,
                    item_id,
                    run_idx,
                    _flavour_info,
                )
            except Exception as exc:
                error = str(exc)
                status = "error"
                output = ""
                result_dict = {}
                async with _io_lock:
                    total_fail += 1
            else:
                async with _io_lock:
                    if status == "error":
                        total_fail += 1
                    else:
                        total_ok += 1
            finally:
                if _watch is not None:
                    _watch.cancel()
                link_trace_to_cache_entry(run_output_dir, _global_run_dir, _run_hash)
                if _cached_events_backup is not None and _cached_events_backup.exists():
                    if _cached_events.exists() and _cached_events.stat().st_size > 0:
                        _cached_events_backup.unlink()
                    else:
                        _cached_events_backup.rename(_cached_events)
                if status == "ok" and _cached_events.is_file() and _cached_events.stat().st_size > 0:
                    from mas.ctl.benchmark.runner import (
                        bench_obs_config,
                        ensure_live_otel_span_files,
                    )

                    _obs_events, _obs_cfg = bench_obs_config(
                        _global_run_dir,
                        config,
                        spec_path,
                        flavour=_sc_flavour if isinstance(_sc_flavour, dict) else None,
                    )
                    ensure_live_otel_span_files(_obs_events, _obs_cfg)

        trace_path = str(_cached_events)
        elapsed_ms = (_time.monotonic() - t0) * 1000
        _term = "ok" if status == "ok" else "failed"
        # The result is on disk before the run is announced as terminal, so whoever reacts to the event
        # (a live stage, a post step) finds result.json.
        write_run_result(_global_run_dir, status, elapsed_ms, error or "", attempts=_attempts_used)
        _mirror_result(_global_run_dir, run_output_dir)
        terminal_run(_v2_key, _term)
        if _live_post is not None:
            await _live_post.on_terminal(_v2_key, _term)
        write_run_info(
            _global_run_dir,
            run_output_dir,
            _run_hash,
            exp.name,
            scenario_id,
            item_id,
            run_idx,
            app=prepared.mas_app,
            app_version=prepared.mas_app_version,
            mas_ref=prepared.mas_ref,
            overlay_ref=prepared.scenario_overlay_refs.get(scenario_id, ""),
        )
        async with _io_lock:
            results_rows.append(
                _run_result_row(
                    scenario_id=scenario_id,
                    item=item,
                    run_idx=run_idx,
                    experiment_id=exp.name,
                    prompt=prompt,
                    status=status,
                    output=str(output or ""),
                    error=error or "",
                    elapsed_ms=elapsed_ms,
                    attempts=_attempts_used,
                    trace_path=trace_path,
                    session_id=result_dict.get("session_id", ""),
                    checkpoint_id=checkpoint_id,
                    forked_from_checkpoint=forked_from_checkpoint,
                )
            )

        if progress:
            tag = "✅" if status == "ok" else "❌"
            print(f"  {tag} [{scenario_id}] item={item_id} run={run_idx + 1} ({elapsed_ms:.0f}ms)")
        if _sc_whatif is not None and status == "ok" and _sc_whatif.produce_checkpoints:
            from mas.lab.benchmark.schedule.whatif import checkpoint_key, publish_named_checkpoints

            publish_named_checkpoints(
                run_checkpoint_dir=_global_run_dir / "checkpoints",
                output_dir=output_dir,
                names=_sc_whatif.produce_checkpoints,
                key=checkpoint_key(item, run_idx),
            )

    async def _dispatch_all() -> None:
        async def _guarded(sid: str, itm: dict, ridx: int) -> None:
            nonlocal total_fail
            key = (sid, itm.get("id", 0), ridx)
            try:
                await _run_one(sid, itm, ridx)
            except Exception as exc:
                _dep_status[key] = "error"
                item_id = itm.get("id", 0)
                async with _io_lock:
                    total_fail += 1
                    results_rows.append(
                        _run_result_row(
                            scenario_id=sid,
                            item=itm,
                            run_idx=ridx,
                            experiment_id=exp.name,
                            prompt="",
                            status="error",
                            output="",
                            error=str(exc),
                            elapsed_ms=0.0,
                            attempts=0,
                        )
                    )
                if progress:
                    print(f"  ❌ [{sid}] item={item_id} run={ridx + 1} ({exc})")
            finally:
                if key not in _dep_status:
                    from mas.lab.benchmark.schedule.whatif import row_status

                    found = row_status(results_rows, sid, itm.get("id", 0), ridx + 1)
                    _dep_status[key] = found
                _done[key].set()

        tasks = []
        for sid, itm, ridx in _execution_plan:
            tasks.append(asyncio.create_task(_guarded(sid, itm, ridx)))
        await asyncio.gather(*tasks)  # _guarded turns a run's failure into an error row

    try:
        if _worker_processes > 1:
            from mas.lab.benchmark.schedule.run_batch.workers import RunWorkerPool

            _run_pool = RunWorkerPool(
                _worker_processes,
                max_runs=int(_worker_max_runs or 0),
                on_event=_live_sink.emit,
                timeout_s=float(_executor.timeout or 300),
            )
        await _dispatch_all()

        _plan_order = {(sid, itm.get("id", 0), ridx + 1): idx for idx, (sid, itm, ridx) in enumerate(_execution_plan)}
        results_rows.sort(
            key=lambda row: _plan_order.get((row.get("scenario"), row.get("item_id"), row.get("run")), 10**9)
        )

        live_ok = False
        if _live_post is not None:
            try:
                await _live_post.wait()
                live_ok = bool(_live_post.done and _live_post.error is None)
            except Exception:
                logger.error("live run-post failed", exc_info=True)

        return ExecutionResult(
            results_rows=results_rows,
            total_ok=total_ok,
            total_fail=total_fail,
            exit_stack=_exit_stack,
            run_keys=_v2_keys or _v2_plan_keys,
            pipeline_drained=live_ok,
        )
    finally:
        if _run_pool is not None:
            _run_pool.close()
        channel = get_channel()
        if channel is not None:
            channel.close()
        _release_batch_cache()
