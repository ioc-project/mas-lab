#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations
"""
CLI for pipeline execution.

Usage:
    python -m mas.lab.benchmark.pipeline.cli run evaluation.yaml
    python -m mas.lab.benchmark.pipeline.cli plan evaluation.yaml
    python -m mas.lab.benchmark.pipeline.cli show evaluation.yaml
    python -m mas.lab.benchmark.pipeline.cli validate evaluation.yaml
    python -m mas.lab.benchmark.pipeline.cli clean evaluation.yaml
"""


import argparse
import asyncio
import importlib.util
import logging
import shutil
import sys
from pathlib import Path

from mas.lab.benchmark.pipeline import Pipeline, PipelineExecutor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def _load_lab_custom_steps(pipeline_config_path: Path) -> None:
    """Auto-load custom step types from the lab directory."""
    search_dir = pipeline_config_path.parent
    max_levels = 5

    for _ in range(max_levels):
        register_file = search_dir / "register_steps.py"
        if register_file.exists():
            try:
                spec = importlib.util.spec_from_file_location(
                    "register_steps_local", register_file
                )
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    sys.modules["register_steps_local"] = module
                    spec.loader.exec_module(module)
                    logger.debug("[Pipeline] Loaded custom steps from %s", register_file)
                    return
            except (ImportError, AttributeError, ModuleNotFoundError) as e:
                # A custom step module that fails to import (e.g. an old
                # `register_step_type` reference or a bad dependency) is a
                # real configuration error, not a "no custom steps here"
                # case. Surface it loudly instead of swallowing it as a
                # warning -- a silent skip here previously produced a
                # confusing, unrelated "Unknown step type" error much later.
                logger.error(
                    "[Pipeline] Failed to load custom steps from %s: %s: %s",
                    register_file,
                    type(e).__name__,
                    e,
                )
                raise RuntimeError(
                    f"Failed to load custom step registrations from {register_file}: {e}"
                ) from e
            except Exception as e:
                logger.warning("[Pipeline] Could not load %s: %s", register_file, e)
                return

        search_dir = search_dir.parent
        if search_dir == search_dir.parent:
            break


def cmd_run(args):
    config_path = Path(args.config).resolve()
    _load_lab_custom_steps(config_path)

    try:
        from mas.lab.benchmark.pipeline.config import PipelineEngineSpec
        from mas.lab.benchmark.pipeline.channel import install_channel

        install_channel(PipelineEngineSpec())
    except Exception:
        logger.debug("stream channel not installed for standalone pipeline", exc_info=True)

    pipeline = Pipeline.from_yaml(args.config)
    executor = PipelineExecutor(pipeline)

    template_vars = {}
    for raw in args.var or []:
        if "=" not in raw:
            raise SystemExit(f"Invalid --var format: {raw!r}. Expected KEY=VALUE")
        key, value = raw.split("=", 1)
        key = key.strip()
        if not key:
            raise SystemExit(f"Invalid --var key in: {raw!r}")
        template_vars[key] = value

    result = asyncio.run(
        executor.run(
            steps=args.only,
            force_rerun=args.force,
            dry_run=args.dry_run,
            parallel=args.parallel,
            template_vars=template_vars,
        )
    )

    print()
    print(result.summary())
    return 0 if result.success else 1


def cmd_plan(args):
    config_path = Path(args.config).resolve()
    _load_lab_custom_steps(config_path)

    pipeline = Pipeline.from_yaml(args.config)
    executor = PipelineExecutor(pipeline)
    plan = executor.plan(steps=args.only, force_rerun=args.force)

    print()
    print(plan.summary())
    print()

    if args.verbose:
        print("Detailed Execution Order:")
        for i, step_name in enumerate(plan.execution_order, 1):
            step = pipeline.get_step(step_name)
            status = "RERUN" if step_name in plan.steps_to_rerun else "CACHED"
            deps = ", ".join(step.depends_on) if step.depends_on else "none"
            print(f"  {i}. [{status}] {step_name} (depends_on: {deps})")

    return 0


def cmd_show(args):
    config_path = Path(args.config).resolve()
    _load_lab_custom_steps(config_path)

    pipeline = Pipeline.from_yaml(args.config)
    print(f"Pipeline: {pipeline.config.name} v{pipeline.config.version}")
    if pipeline.config.description:
        print(f"Description: {pipeline.config.description}")
    print(f"Output: {pipeline.config.output.get('base_dir', './output')}")
    print()

    print(f"Steps ({len(pipeline.steps)}):")
    for i, step in enumerate(pipeline.steps, 1):
        deps = ", ".join(step.depends_on) if step.depends_on else "none"
        print(f"  {i}. {step.name}")
        print(f"     Type: {step.type}")
        print(f"     Depends on: {deps}")
        if args.verbose:
            print(f"     Config: {step.config}")
        print()

    return 0


def cmd_validate(args):
    config_path = Path(args.config).resolve()
    _load_lab_custom_steps(config_path)

    try:
        pipeline = Pipeline.from_yaml(args.config)
        print(f"✓ Pipeline valid: {pipeline.config.name}")
        print(f"  Steps: {len(pipeline.steps)}")
        print("  Dependency graph: OK")
        return 0
    except Exception as e:
        print(f"✗ Validation failed: {e}")
        return 1


def cmd_clean(args):
    pipeline = Pipeline.from_yaml(args.config)
    executor = PipelineExecutor(pipeline)
    output_dir = executor.output_dir

    if not output_dir.exists():
        print(f"Output directory does not exist: {output_dir}")
        return 0

    print(f"Cleaning: {output_dir}")

    if args.cache_only:
        cache_dir = output_dir / ".cache"
        if cache_dir.exists():
            shutil.rmtree(cache_dir)
            print(f"✓ Cleaned cache: {cache_dir}")
    else:
        if not args.yes:
            response = input(f"Delete all outputs in {output_dir}? [y/N] ")
            if response.lower() != "y":
                print("Cancelled")
                return 0
        shutil.rmtree(output_dir)
        print(f"✓ Cleaned: {output_dir}")

    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Pipeline CLI for declarative evaluations"
    )
    subparsers = parser.add_subparsers(dest="command", help="Command")

    run_parser = subparsers.add_parser("run", help="Execute pipeline")
    run_parser.add_argument("config", type=Path, help="Pipeline config YAML")
    run_parser.add_argument("--only", nargs="+", help="Run only these steps")
    run_parser.add_argument("--force", nargs="+", help="Force rerun these steps (ignores cache)")
    run_parser.add_argument(
        "--var",
        action="append",
        help="Template variable KEY=VALUE (repeatable)",
    )
    run_parser.add_argument("--dry-run", action="store_true", help="Show plan without executing")
    run_parser.add_argument(
        "--no-parallel",
        dest="parallel",
        action="store_false",
        help="Serialize steps (default is concurrent, capped by max_active)",
    )
    run_parser.set_defaults(parallel=True)

    plan_parser = subparsers.add_parser("plan", help="Show execution plan")
    plan_parser.add_argument("config", type=Path, help="Pipeline config YAML")
    plan_parser.add_argument("--only", nargs="+", help="Plan for these steps")
    plan_parser.add_argument("--force", nargs="+", help="Force rerun these steps")
    plan_parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")

    show_parser = subparsers.add_parser("show", help="Show pipeline structure")
    show_parser.add_argument("config", type=Path, help="Pipeline config YAML")
    show_parser.add_argument("-v", "--verbose", action="store_true", help="Show config details")

    validate_parser = subparsers.add_parser("validate", help="Validate pipeline config")
    validate_parser.add_argument("config", type=Path, help="Pipeline config YAML")

    clean_parser = subparsers.add_parser("clean", help="Clean pipeline outputs")
    clean_parser.add_argument("config", type=Path, help="Pipeline config YAML")
    clean_parser.add_argument("--cache-only", action="store_true", help="Only clean cache")
    clean_parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 1

    commands = {
        "run": cmd_run,
        "plan": cmd_plan,
        "show": cmd_show,
        "validate": cmd_validate,
        "clean": cmd_clean,
    }
    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
