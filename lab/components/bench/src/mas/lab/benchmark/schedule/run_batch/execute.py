#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0

"""Execute MAS benchmark runs according to the execution plan."""

from __future__ import annotations

import asyncio
import json as _json
import logging
import os
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
from mas.lab.benchmark.execution import build_execution_plan
from mas.lab.benchmark.schedule.metadata import execution_as_dict
from mas.lab.benchmark.schedule.run_batch.load import LoadedExperiment
from mas.lab.benchmark.schedule.run_batch.prepare import PreparedBatch
from mas.lab.runners.context import RunContext
from mas.lab.runners.invoke import invoke_runner

logger = logging.getLogger(__name__)

DEFAULT_LAB_RUN_ATTEMPTS = 3
DEFAULT_LAB_RETRY_BACKOFF_S = 2.0
_FATAL_RUN_MARKERS = (
    "tls certificate verification failed",
    "http 401",
    "http 403",
    "authentication/authorization",
)


def _infra_refs_for_scenario(scenario_id: str, prepared: PreparedBatch) -> list[str]:
    """Prefer per-scenario demo-ui cache refs when the worker set them."""
    refs = list(prepared.infra_refs)
    raw = os.environ.get("MAS_DEMO_UI_SCENARIO_INFRA")
    if not raw:
        return refs
    try:
        mapping = _json.loads(raw)
    except ValueError:
        return refs
    if not isinstance(mapping, dict):
        return refs
    extra = mapping.get(scenario_id) or mapping.get(str(scenario_id).lower())
    if not extra:
        return refs
    chosen = [str(item) for item in extra if item]
    if chosen:
        return chosen
    return refs


def lab_run_retry_settings(schedule: Any | None) -> tuple[int, float]:
    """``schedule.max_attempts`` / ``retry_backoff_s``, with lab defaults."""
    raw_attempts = getattr(schedule, "max_attempts", None) if schedule is not None else None
    raw_backoff = getattr(schedule, "retry_backoff_s", None) if schedule is not None else None
    attempts = DEFAULT_LAB_RUN_ATTEMPTS if raw_attempts is None else int(raw_attempts)
    backoff = DEFAULT_LAB_RETRY_BACKOFF_S if raw_backoff is None else float(raw_backoff)
    return max(1, attempts), max(0.0, backoff)


def lab_run_error_is_retryable(error: str, *, status: str) -> bool:
    """True for transient execution failures; false for auth/TLS/config errors."""
    if status != "error":
        return False
    text = (error or "").lower()
    return not any(marker in text for marker in _FATAL_RUN_MARKERS)


def _status_from_runner(result_dict: dict) -> tuple[str, str, str]:
    output = result_dict.get("content", str(result_dict))
    if result_dict.get("status") == "error":
        return "error", output or "execution error", output
    if output and str(output).startswith("LLM request failed:"):
        return "error", str(output), output
    return "ok", "", output


async def invoke_lab_run_with_retry(
    do_run,
    *,
    max_attempts: int,
    backoff_s: float,
    on_retry=None,
    before_attempt=None,
) -> tuple[dict, str, str, str, int]:
    """Run ``do_run`` up to ``max_attempts``. Returns result, status, error, output, attempts used.

    ``before_attempt`` (async, no arguments) runs before every attempt (``run.pre``
    steps: reset per-run state). If it raises, the run fails at once with that
    error and is not retried — the MAS never starts on unprepared state.
    """
    result_dict: dict = {}
    status, error, output = "error", "not started", ""
    attempts = max(1, max_attempts)
    for attempt in range(attempts):
        if before_attempt is not None:
            try:
                await before_attempt()
            except Exception as exc:
                return {}, "error", str(exc), "", attempt + 1
        try:
            result_dict = await asyncio.to_thread(do_run)
            status, error, output = _status_from_runner(result_dict)
        except Exception as exc:
            result_dict = {}
            status, error, output = "error", str(exc), ""
        if status != "error":
            return result_dict, status, error, output, attempt + 1
        if attempt >= attempts - 1 or not lab_run_error_is_retryable(error, status=status):
            return result_dict, status, error, output, attempt + 1
        if on_retry is not None:
            on_retry(attempt + 1, attempts, error)
        delay = backoff_s * (2 ** attempt)
        if delay > 0:
            await asyncio.sleep(delay)
    return result_dict, status, error, output, attempts


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


async def execute_batch(
    loaded: LoadedExperiment,
    prepared: PreparedBatch,
    *,
    progress: bool = True,
    force: bool = False,
    strategy: Optional[str] = None,
    data_cache_dir: Optional[Path] = None,
) -> ExecutionResult:
    """Run all planned (scenario × item × run) executions."""
    import contextlib

    from mas.lab.benchmark.service_manager import ServiceManager as _ServiceManager
    from mas.lab.emulation_resolver import resolve_emulation_plugins

    exp = loaded.exp
    experiment_yaml = loaded.experiment_yaml
    if not os.environ.get("MAS_LLM_SCHEDULER_USER", "").strip():
        os.environ["MAS_LLM_SCHEDULER_USER"] = getattr(exp, "name", "") or "experiment"
    _flavour = loaded.flavour
    _flavour_name = loaded.flavour_name
    trace_cache_dir = loaded.trace_cache_dir
    output_dir = prepared.output_dir

    _mas_manifest_path = exp.mas.manifest if (exp.mas and exp.mas.manifest) else experiment_yaml

    _svc_mgr = _ServiceManager.for_benchmark(
        flavour=_flavour,
        experiment_dir=experiment_yaml.parent,
        output_dir=output_dir,
    )
    _exit_stack = contextlib.ExitStack()
    _exit_stack.enter_context(_svc_mgr)

    results_rows: list = []
    total_ok = 0
    total_fail = 0

    _schedule = getattr(exp, "schedule", None) or getattr(exp, "execution", None)
    _effective_strategy = (
        strategy
        or (getattr(_schedule, "ordering", None) if _schedule else None)
        or (getattr(_schedule, "strategy", None) if _schedule else None)
        or "coverage"
    )
    _design_obj = getattr(exp, "design", None)
    _design_dict = (
        _design_obj.as_plan_dict()
        if _design_obj is not None and hasattr(_design_obj, "as_plan_dict")
        else None
    )
    _execution_plan = build_execution_plan(
        prepared.loaded_ids,
        prepared.dataset_items,
        loaded.n_runs,
        strategy=_effective_strategy,
        execution=execution_as_dict(exp),
        design=_design_dict,
    )

    _parallel = max(1, (getattr(_schedule, "parallel_scenarios", 1) if _schedule else 1) or 1)
    _pause    = max(0.0, (getattr(_schedule, "pause_between_runs", 0.0) if _schedule else 0.0) or 0.0)
    _max_attempts, _retry_backoff = lab_run_retry_settings(_schedule)
    _sem      = asyncio.Semaphore(_parallel)
    _io_lock  = asyncio.Lock()

    from mas.lab.benchmark.pipeline.config import pipeline_settings
    from mas.lab.benchmark.pipeline.keys import run_key_from_parts
    from mas.lab.benchmark.pipeline.channel import install_channel
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

    _emulation = getattr(exp, "bench_emulation", None) or getattr(
        getattr(exp, "execution", None), "emulation", None
    )
    _cache_policy = (
        getattr(getattr(_emulation, "runtime", None), "cache", "content-addressed")
        if _emulation
        else "content-addressed"
    )

    if progress:
        print()
        print(f"Running MAS benchmark '{exp.name}'")
        print(f"  strategy   : {_effective_strategy}")
        n_sc, n_items = len(prepared.loaded_ids), len(prepared.dataset_items)
        print(f"  {n_sc} scenarios × {n_items} items × {loaded.n_runs} run(s) = {len(_execution_plan)} executions")
        print(f"  parallel   : {_parallel}   pause: {_pause}s   max_attempts: {_max_attempts}")
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

    _faults_config = getattr(_emulation, "faults", None) if _emulation else None
    if _faults_config is None:
        _faults_config = getattr(exp.execution, "faults", None) if exp.execution else None
    _faults_dict = _faults_config if isinstance(_faults_config, dict) else None
    _emulation_plugins = resolve_emulation_plugins(
        _emulation,
        faults_config=_faults_dict,
    )

    from mas.ctl.compose.runner import infra_cache_identity, resolve_workspace_infra
    from mas.ctl.workspace.config import WorkspaceConfig

    # Runs resolve infra from the experiment workspace; the cache key must too.
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
        config, spec_path = prepared.scenario_configs[scenario_id]
        item_id = item.get("id", 0)
        source_item_id = item.get("source_item_id", item_id)

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

        _flavour_info = extract_flavour_info(_sc_flavour)
        _run_hash = compute_run_hash(
            config,
            _run_input_dict,
            item_id,
            run_idx,
            _flavour_info,
            base_path=spec_path,
            infra_info=_infra_info,
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
        _global_result_ok = False
        _global_result_path = _global_run_dir / "result.json"
        if _global_result_path.exists():
            try:
                _prior = _json.loads(_global_result_path.read_text(encoding="utf-8"))
                if _prior.get("status") != "error":
                    _global_result_ok = True
                else:
                    logger.debug(
                        "Cache miss (prior run failed): hash=%s error=%r",
                        _run_hash[:12], _prior.get("error", "")[:80],
                    )
            except Exception:
                logger.debug(
                    "Cache miss (result.json unreadable): hash=%s", _run_hash[:12]
                )

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
            and _events_path.exists()
            and _events_path.stat().st_size > 0
        )
        if _cache_policy == "forced" and not _global_hit and not _local_hit:
            error = f"emulation.runtime.cache=forced but no cache entry for hash {_run_hash[:12]}"
            status = "error"
            async with _io_lock:
                total_fail += 1
                results_rows.append({
                    "run_id": run_id, "scenario": scenario_id,
                    "item_id": item_id, "run": run_idx + 1,
                    "source_item_id": source_item_id,
                    "experiment_id": exp.name,
                    "session_id": "",
                    "checkpoint_id": checkpoint_id,
                    "forked_from_checkpoint": forked_from_checkpoint,
                    "group": item.get("group", ""),
                    "target_agents": ",".join(item.get("target_agents", [])),
                    "prompt": prompt,
                    "status": status, "output": "", "output_length": 0,
                    "trace_path": "", "elapsed_ms": 0.0, "error": error,
                    "attempts": 0,
                })
            if progress:
                print(f"  ❌ [{scenario_id}] item={item_id} run={run_idx+1} (cache=forced, miss)")
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
                _global_run_dir, run_output_dir, _run_hash, exp.name, scenario_id,
                item_id, run_idx,
                app=prepared.mas_app,
                app_version=prepared.mas_app_version,
                mas_ref=prepared.mas_ref,
                overlay_ref=prepared.scenario_overlay_refs.get(scenario_id, ""),
            )
            if progress:
                print(f"  ⏩  [{scenario_id}] item={item_id} run={run_idx+1} ({_src}, skipping)")
            resolved_events = _cached_events if _global_hit else _events_path
            async with _io_lock:
                results_rows.append({
                    "run_id": run_id,
                    "scenario": scenario_id,
                    "item_id": item_id,
                    "run": run_idx + 1,
                    "source_item_id": source_item_id,
                    "experiment_id": exp.name,
                    "session_id": "",
                    "checkpoint_id": checkpoint_id,
                    "forked_from_checkpoint": forked_from_checkpoint,
                    "group": item.get("group", ""),
                    "target_agents": ",".join(item.get("target_agents", [])),
                    "prompt": prompt,
                    "status": "ok",
                    "output": "(cached)",
                    "output_length": 0,
                    "trace_path": str(resolved_events),
                    "elapsed_ms": 0.0,
                    "error": "",
                    "attempts": 0,
                })
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
                    _run_traces_dir, run_output_dir,
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
            try:
                def _do_mas_run() -> dict:
                    from mas.lab.runners.infer import infer_runner_id
                    from mas.lab.benchmark.pipeline.ui_bridge import forward_native_to_channel
                    from mas.runtime.boundary.obs.event_stream import (
                        EventStream,
                        reset_event_stream,
                        set_event_stream,
                    )

                    class _ScenarioStream(EventStream):
                        def publish(self, event: dict) -> None:
                            if scenario_id and isinstance(event, dict) and not event.get("scenario"):
                                event = {**event, "scenario": scenario_id}
                            super().publish(event)

                    _stream = _ScenarioStream()
                    _stream.subscribe(forward_native_to_channel)
                    _token = set_event_stream(_stream)
                    _sid_token = begin_run(_v2_key)
                    try:
                        from mas.lab.benchmark.pipeline.channel import set_stream_id
                    except Exception:
                        set_stream_id = None  # type: ignore[assignment]
                    if set_stream_id is not None and _sid_token is None:
                        _sid_token = set_stream_id(_v2_key.stream_id())

                    _runner_id = infer_runner_id(
                        execution_runner=(
                            getattr(_schedule, "runner", None)
                            if _schedule
                            else (
                                exp.execution.runner
                                if exp.execution and getattr(exp.execution, "runner", None)
                                else None
                            )
                        ),
                        mas_manifest=(
                            Path(_mas_manifest_path)
                            if _mas_manifest_path is not None
                            else None
                        ),
                        agent_config=config,
                        flavour=_sc_flavour if isinstance(_sc_flavour, dict) else None,
                    )
                    _overlay_refs = list(prepared.scenario_overlay_stacks.get(scenario_id, []))
                    _infra_refs = _infra_refs_for_scenario(scenario_id, prepared)
                    ctx = RunContext(
                        prompt=prompt,
                        config=config,
                        spec_path=spec_path,
                        output_dir=_global_run_dir,
                        runner_id=_runner_id,
                        run_input=_run_input,
                        flavour=_sc_flavour,
                        run_seed=run_idx,
                        overlay_refs=_overlay_refs,
                        overlays_dir=prepared.overlays_dir,
                        overlay_base_dir=prepared.overlay_base_dir,
                        infra_refs=_infra_refs,
                        session_id=_run_input.session_id,
                        emulation_plugins=_emulation_plugins,
                        extra={
                            "experiment_default_model": getattr(exp, "model", None),
                            "experiment_model_slots": getattr(exp, "model_slots", None),
                            "workspace_root": experiment_yaml.parent,
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

                def _on_retry(failed_attempt: int, attempts: int, err: str) -> None:
                    attempt_reset(_v2_key)
                    _clear_failed_attempt_traces(_global_run_dir)
                    logger.warning(
                        "lab run %s failed (%s); retrying %s/%s",
                        run_id,
                        (err or "")[:160],
                        failed_attempt + 1,
                        attempts,
                    )
                    if progress:
                        print(
                            f"  ⚠️  [{scenario_id}] item={item_id} run={run_idx+1} "
                            f"retry {failed_attempt + 1}/{attempts}"
                        )

                result_dict, status, error, output, _attempts_used = await invoke_lab_run_with_retry(
                    _do_mas_run,
                    max_attempts=_max_attempts,
                    backoff_s=_retry_backoff,
                    on_retry=_on_retry,
                    before_attempt=(
                        (lambda: _pre_hooks.before_attempt(
                            scenario=_sc_dir_name,
                            test=f"item{item_id}",
                            run=f"r{run_idx + 1}",
                        ))
                        if _pre_hooks.active
                        else None
                    ),
                )
                write_cache_inputs(
                    _global_run_dir, _run_hash, _run_input_dict, item_id,
                    run_idx, _flavour_info,
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
        terminal_run(_v2_key, _term)
        if _live_post is not None:
            await _live_post.on_terminal(_v2_key, _term)
        write_run_result(_global_run_dir, status, elapsed_ms, error or "")
        write_run_info(
            _global_run_dir, run_output_dir, _run_hash, exp.name, scenario_id,
            item_id, run_idx,
            app=prepared.mas_app,
            app_version=prepared.mas_app_version,
            mas_ref=prepared.mas_ref,
            overlay_ref=prepared.scenario_overlay_refs.get(scenario_id, ""),
        )
        async with _io_lock:
            results_rows.append({
                "run_id": run_id,
                "scenario": scenario_id,
                "item_id": item_id,
                "run": run_idx + 1,
                "source_item_id": source_item_id,
                "experiment_id": exp.name,
                "session_id": result_dict.get("session_id", ""),
                "checkpoint_id": checkpoint_id,
                "forked_from_checkpoint": forked_from_checkpoint,
                "group": item.get("group", ""),
                "target_agents": ",".join(item.get("target_agents", [])),
                "prompt": prompt,
                "status": status,
                "output": str(output or "")[:500],
                "output_length": len(str(output or "")),
                "trace_path": trace_path,
                "elapsed_ms": round(elapsed_ms, 1),
                "error": error or "",
                "attempts": _attempts_used,
            })

        if progress:
            tag = "✅" if status == "ok" else "❌"
            print(f"  {tag} [{scenario_id}] item={item_id} run={run_idx+1} ({elapsed_ms:.0f}ms)")

    async def _dispatch_all() -> None:
        tasks = []
        for sid, itm, ridx in _execution_plan:
            tasks.append(asyncio.create_task(_run_one(sid, itm, ridx)))
        await asyncio.gather(*tasks, return_exceptions=True)

    await _dispatch_all()

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
