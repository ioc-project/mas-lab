#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``mas-lab run`` command group — execute processors and pipelines directly.

Sub-commands
------------
``mas-lab run processor``          — run a single processor against one input
``mas-lab run pipeline step``      — run one or more pipeline steps given an output
                                     directory and any required inputs.
                                     This is NOT equivalent to ``benchmark run``:
                                     it does not manage experiments, scenarios,
                                     tests or agent runs — it only executes the
                                     declared pipeline steps against data that
                                     already exists on disk.

CLI syntax
----------
Arguments are expressed as ``name=value`` (artifact binding) or
``name.attribute=value`` (artifact attribute override)::

    mas-lab run processor trajectory_plotter \\
        trace=20260224-140201-baseline-e60feafd \\
        plot=output/traj.svg \\
        plot.format=svg

When the processor has a single input / output the names can be omitted::

    mas-lab run processor trajectory_plotter \\
        20260224-140201-baseline-e60feafd \\
        output/traj.svg \\
        plot.format=svg

Any ``name.attribute`` pairs not matching input/output slot names are forwarded
as ``**kwargs`` to ``Processor.process()``.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Tuple

import click

# ---------------------------------------------------------------------------
# Top-level group
# ---------------------------------------------------------------------------

@click.group("run")
def run_group() -> None:
    """Run processors and pipelines outside a full benchmark context."""


# ---------------------------------------------------------------------------
# ``mas-lab run processor``
# ---------------------------------------------------------------------------

@run_group.command(
    "processor",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
)
@click.argument("processor_name", metavar="PROCESSOR", required=False, default=None)
@click.argument("bindings", nargs=-1, type=str)
@click.option(
    "--list", "do_list",
    is_flag=True, default=False,
    help="List all registered processors and exit.",
)
@click.option(
    "--import", "extra_imports",
    multiple=True, metavar="MODULE",
    help="Import a Python module before resolving the processor.",
)
@click.option(
    "--verbose", "-v",
    is_flag=True, default=False,
    help="Show full tracebacks on errors.",
)
def processor_cmd(
    processor_name: str | None,
    bindings: Tuple[str, ...],
    do_list: bool,
    extra_imports: Tuple[str, ...],
    verbose: bool,
) -> None:
    """Run a single processor.

    PROCESSOR is the processor name (e.g. ``trajectory_plotter``).

    \b
    Arguments are NAME=VALUE pairs:
      trace=<run_id or path>      bind the input slot named 'trace'
      plot=<output path>          bind the output slot named 'plot'
      plot.format=svg             override the 'format' attribute of 'plot'

    \b
    Examples:
      mas-lab run processor --list
      mas-lab run processor trajectory_plotter \\
          trace=20260224-140201-baseline-e60feafd \\
          plot=output/traj.svg \\
          plot.format=svg
    """
    # ── extra imports ───────────────────────────────────────────────────────
    for mod in extra_imports:
        try:
            import importlib
            importlib.import_module(mod)
        except ImportError as exc:
            click.echo(f"❌  Cannot import '{mod}': {exc}", err=True)
            sys.exit(1)

    try:
        from mas.lab.processor import ProcessorManifest, get_processor, list_processors
    except ImportError as exc:
        click.echo(f"❌  Cannot import mas.lab.processor: {exc}", err=True)
        sys.exit(1)

    # ── --list ──────────────────────────────────────────────────────────────
    if do_list:
        procs = list_processors()
        if not procs:
            click.echo("(no processors registered)")
            return
        click.echo(f"{'NAME':<32} {'PRI':>3}  {'INPUT':<24} {'OUTPUT':<24}  DESCRIPTION")
        click.echo("─" * 110)
        for p in procs:
            prio = getattr(p, "priority", 10)
            click.echo(f"{p.name:<32} {prio:>3}  {p.input_kind:<24} {p.output_kind:<24}  {p.description}")
        return

    if not processor_name:
        click.echo(
            "❌  Provide a PROCESSOR name or use --list.",
            err=True,
        )
        sys.exit(1)

    # ── resolve processor + manifest ────────────────────────────────────────
    try:
        proc_cls = get_processor(processor_name)
    except KeyError as exc:
        click.echo(f"❌  {exc}", err=True)
        sys.exit(1)

    manifest = ProcessorManifest.from_processor_cls(proc_cls)

    # ── parse bindings ───────────────────────────────────────────────────────
    # Syntax A: name=value          → slot binding  or  attr (no dot, no slot match)
    # Syntax B: name.attribute=val  → attribute override for a named slot
    # Positional (no =):            → assigned left-to-right to input then output slots
    slot_values: Dict[str, str]        = {}   # slot_name → raw value (path/run_id)
    slot_attrs:  Dict[str, Dict[str, Any]] = {}   # slot_name → {attr: value}
    extra_kwargs: Dict[str, Any]       = {}   # forwarded to process()

    slot_names = {s.name for s in manifest.inputs + manifest.outputs}
    positional_slots = [s.name for s in manifest.inputs] + [s.name for s in manifest.outputs]
    positional_idx = 0

    for token in bindings:
        if "=" in token:
            key, _, val = token.partition("=")
            if "." in key:
                slot, _, attr = key.partition(".")
                slot_attrs.setdefault(slot, {})[attr] = _coerce(val)
            elif key in slot_names:
                slot_values[key] = val
            else:
                extra_kwargs[key] = _coerce(val)
        else:
            # positional: assign to next unbound slot
            if positional_idx < len(positional_slots):
                slot_values[positional_slots[positional_idx]] = token
                positional_idx += 1
            else:
                click.echo(f"⚠️  Ignoring extra positional argument: {token!r}", err=True)

    # ── resolve input artifact ───────────────────────────────────────────────
    input_slot = manifest.inputs[0] if manifest.inputs else None
    input_name = input_slot.name if input_slot else "input"
    input_source = slot_values.get(input_name)

    if not input_source:
        click.echo(
            f"❌  Input artifact '{input_name}' is required.  "
            f"Pass it as: {input_name}=<run_id or path>",
            err=True,
        )
        sys.exit(1)

    artifact = _load_input(proc_cls, input_source)

    # ── build kwargs from manifest defaults + slot.attr overrides ───────────
    kwargs: Dict[str, Any] = {}
    for out_slot in manifest.outputs:
        kwargs.update(out_slot.defaults)
        if out_slot.name in slot_attrs:
            kwargs.update(slot_attrs[out_slot.name])

    # Input attrs too (e.g. trace.run_id=... for future use)
    for in_slot in manifest.inputs:
        if in_slot.name in slot_attrs:
            kwargs.update(slot_attrs[in_slot.name])

    # Positional / named output path
    output_slot = manifest.outputs[0] if manifest.outputs else None
    output_name = output_slot.name if output_slot else "output"
    if output_name in slot_values:
        kwargs["output"] = Path(slot_values[output_name])

    # Forwarded extra kwargs (no slot match)
    kwargs.update(extra_kwargs)

    # ── execute ──────────────────────────────────────────────────────────────
    processor = proc_cls()
    try:
        result = processor.process(artifact, **kwargs)
    except Exception as exc:
        click.echo(f"❌  Processor failed: {exc}", err=True)
        if verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)

    # ── report ───────────────────────────────────────────────────────────────
    if result.path and result.path.exists():
        click.echo(f"✅  {result.kind}  →  {result.path}")
    elif result.data is not None and "output" not in kwargs:
        click.echo(str(result.data))
    else:
        click.echo(f"✅  {result.kind}  (in-memory)")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _coerce(val: str) -> Any:
    """Try to parse val as int/float/bool, else return string."""
    if val.lower() in ("true", "yes"):
        return True
    if val.lower() in ("false", "no"):
        return False
    try:
        return int(val)
    except ValueError:
        pass
    try:
        return float(val)
    except ValueError:
        pass
    return val


def _load_input(proc_cls, source: str):
    """Resolve *source* to an artifact appropriate for *proc_cls*.

    Logic:
    1. If the processor's ``input_kind`` is ``"path"``, return the raw string.
    2. If source ends with ``.json``, load as JSON.
    3. Otherwise, wrap in a minimal ``Trajectory`` (lazy-load on process()).
    """
    from mas.lab.artifacts import Trajectory

    if proc_cls.input_kind == "path":
        return source

    if source.endswith(".json"):
        import json
        return json.loads(Path(source).read_bytes())

    p = Path(source)
    return Trajectory(
        path=p if p.suffix == ".jsonl" else None,
        run_id=source if not p.suffix else p.stem,
        meta={"source": str(source)},
    )


# ---------------------------------------------------------------------------
# ``mas-lab run pipeline`` sub-group
# ---------------------------------------------------------------------------

@run_group.group("pipeline")
def pipeline_group() -> None:
    """Execute pipeline steps directly.

    This command operates on the pipeline layer only — it reads and writes
    files in an output directory but knows nothing about experiments, scenarios,
    tests or agent runs.  Use ``mas-lab benchmark run`` to orchestrate a full
    experiment (scenarios → tests → runs → pipeline).
    """


@pipeline_group.command("step")
@click.argument("experiment_yaml", metavar="EXPERIMENT", type=click.Path(exists=True, dir_okay=False))
@click.argument("step_names", metavar="STEP...", nargs=-1, required=True)
@click.option(
    "--output-dir", "-o", "output_dir",
    default=None, metavar="DIR",
    help="Override the output directory (default: last run for this experiment).",
)
@click.option(
    "--force", "-f",
    is_flag=True, default=False,
    help="Re-run even if step outputs are already cached.",
)
@click.option(
    "--set", "overrides",
    multiple=True, metavar="step.key=value",
    help="Override a step config value.  Format: step_name.key=value",
)
def pipeline_step_cmd(
    experiment_yaml: str,
    step_names: tuple[str, ...],
    output_dir: str | None,
    force: bool,
    overrides: tuple[str, ...],
) -> None:
    """Run one or more pipeline steps from EXPERIMENT.

    Reads the pipeline definition from EXPERIMENT (an experiment.yaml) and
    executes the requested steps against OUTPUT_DIR.  This does NOT run
    scenarios, tests or agent runs \u2014 it only executes the pipeline steps
    themselves (evaluation, aggregation, plot generation, etc.).

    STEP is one or more step names (from the experiment's pipeline:).
    Use '*' to run all steps.

    \b
    Examples:
      # Regenerate the pipeline SVG diagram
      mas-lab run pipeline step labs/design-space.lab/01-design-patterns/experiment.yaml pipeline-diagram

      # Re-run all post-processing steps
      mas-lab run pipeline step labs/design-space.lab/01-design-patterns/experiment.yaml '*'

      # Override a config key on the fly
      mas-lab run pipeline step labs/.../experiment.yaml figure-patterns-quality \\
          --set figure-patterns-quality.output=/tmp/preview.png
    """
    import asyncio as _asyncio
    import sys as _sys

    exp_path = Path(experiment_yaml).resolve()

    # ── load experiment config ───────────────────────────────────────────────
    try:
        from mas.ctl.env import env_model_override
        from mas.lab.lab.config import MASExperimentConfig
    except ImportError as exc:
        click.echo(f"❌  Cannot import MASExperimentConfig: {exc}", err=True)
        _sys.exit(1)

    try:
        exp = MASExperimentConfig.from_yaml(
            exp_path, model_override=env_model_override()
        )
    except Exception as exc:
        click.echo(f"❌  Cannot load {exp_path}: {exc}", err=True)
        _sys.exit(1)

    # ── resolve output_dir ──────────────────────────────────────────────────
    if output_dir:
        out = Path(output_dir).expanduser().resolve()
    else:
        # Use the experiment's auto-derived output dir (same logic as benchmark run)
        out = exp.output_dir
        if not out.exists():
            click.echo(
                f"❌  Output directory does not exist: {out}\n"
                f"    Run the benchmark first, or pass --output-dir DIR.",
                err=True,
            )
            _sys.exit(1)

    click.echo(f"Output dir : {out}")

    from mas.lab.lab.config import discover_lab_context, inject_lab_libraries

    lab_ctx = discover_lab_context(exp_path)
    inject_lab_libraries(lab_ctx)

    # ── collect pipeline steps ───────────────────────────────────────────────
    from mas.lab.benchmark.schedule.pipeline import (
        build_runtime_pipeline,
        execute_runtime_pipeline,
        materialize_selected_specs,
    )
    from mas.lab.benchmark.schedule.pipeline_resolve import resolve_pipeline_specs

    all_steps_specs = resolve_pipeline_specs(exp, exp_path)
    if not all_steps_specs:
        click.echo("❌  No pipeline steps found in this experiment.", err=True)
        _sys.exit(1)

    # Filter to requested step names ('*' = all)
    want = set(step_names)
    if "*" in want:
        selected = all_steps_specs
        force_names = {s.name for s in selected}
    else:
        # Expand to include transitive dependencies
        specs_by_name = {s.name: s for s in all_steps_specs}
        missing = want - specs_by_name.keys()
        if missing:
            available = ", ".join(s.name for s in all_steps_specs)
            click.echo(
                f"❌  Step(s) not found: {', '.join(sorted(missing))}\n"
                f"    Available: {available}",
                err=True,
            )
            _sys.exit(1)

        # BFS to collect transitive deps
        def _transitive_deps(names: set) -> list:
            visited, order = set(), []
            queue = list(names)
            while queue:
                n = queue.pop(0)
                if n in visited or n not in specs_by_name:
                    continue
                visited.add(n)
                for dep in specs_by_name[n].depends_on:
                    if dep not in visited:
                        queue.append(dep)
                order.append(n)
            # Return in original pipeline order
            name_order = {s.name: i for i, s in enumerate(all_steps_specs)}
            return sorted(visited, key=lambda n: name_order.get(n, 999))

        dep_names = _transitive_deps(want)
        selected = [specs_by_name[n] for n in dep_names]
        force_names = want  # only force-rerun explicitly requested steps
        if set(dep_names) != want:
            extra = set(dep_names) - want
            click.echo(f"Including  : {', '.join(sorted(extra))} (transitive dependencies)")

    # ── parse --set overrides ────────────────────────────────────────────────
    step_overrides: dict[str, dict] = {}
    for ov in overrides:
        if "." not in ov or "=" not in ov:
            click.echo(f"⚠️   Ignoring malformed --set value: {ov!r}  (expect step.key=value)", err=True)
            continue
        step_key, _, val = ov.partition("=")
        step_name, _, cfg_key = step_key.partition(".")
        step_overrides.setdefault(step_name, {})[cfg_key] = _coerce(val)

    # ── build and run the pipeline ───────────────────────────────────────────
    step_dicts = materialize_selected_specs(
        selected,
        experiment_yaml=exp_path,
        output_dir=out,
        name_overrides=step_overrides,
    )

    pipeline = build_runtime_pipeline(
        exp=exp,
        experiment_yaml=exp_path,
        step_dicts=step_dicts,
        pipeline_name=f"{exp.name}-standalone",
    )

    click.echo(f"Steps      : {', '.join(s.name for s in pipeline.steps)}")
    click.echo(f"Force rerun: {', '.join(sorted(force_names)) if force else 'off'}")

    async def _run() -> None:
        await execute_runtime_pipeline(
            pipeline,
            output_dir=out,
            force_rerun=list(force_names) if force else None,
        )

    _asyncio.run(_run())
