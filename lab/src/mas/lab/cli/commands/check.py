#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Click wrappers for ``mas-lab check`` and ``mas-lab check-config``."""
from __future__ import annotations

from pathlib import Path

import click


@click.command("check")
@click.argument("mas_spec", type=Path, metavar="MAS_SPEC")
@click.option("-v", "--verbose", count=True, default=0,
              help="Verbosity: -v all checks, -vv debug, -vvv full debug.")
@click.option("--base-dir", type=Path, default=None,
              help="Base directory for resolving relative file references.")
@click.option("--strict/--no-strict", default=True, show_default=True,
              help="Strict mode (default): warn on missing recommended fields "
                   "(spec.description, spec.design_pattern, metadata.description). "
                   "Use --no-strict to suppress those warnings.")
def check_cmd(mas_spec: Path, verbose: int, base_dir: Path | None,
              strict: bool) -> None:
    """Validate a MAS specification (mas.yaml).

    For ``kind: MAS`` files the full MASSpecValidator tree is run (agents,
    overlays, workflows, …).  For any other kind (Agent, Patch, Flavour,
    Tool, …) the manifest is validated directly against its JSON Schema.
    """
    from mas.ctl.logging_setup import setup_logging
    setup_logging(verbosity=verbose)

    import yaml as _yaml_mod

    try:
        _raw = _yaml_mod.safe_load(mas_spec.read_text()) or {}
    except Exception as exc:
        print(f"\n❌ Cannot read {mas_spec}: {exc}\n")
        raise SystemExit(1)

    _kind = _raw.get("kind", "")

    if _kind not in ("MAS", "App", ""):
        from mas.ctl.validate import validate_file

        result = validate_file(
            mas_spec,
            strict=strict,
            base_dir=base_dir or mas_spec.parent,
        )
        if result.ok:
            print(f"\n✅  {mas_spec.name}  [{_kind}]  schema OK\n")
            raise SystemExit(0)
        for issue in result.issues:
            if issue.level == "error" or strict:
                print(f"  ❌  {issue.message}")
        errors = [i for i in result.issues if i.level == "error"]
        print(f"\n❌ Validation failed for {mas_spec.name} "
              f"[{_kind}] — {len(errors)} error(s)\n")
        raise SystemExit(1)

    from mas.lab.mas_validator import MASSpecValidator

    validator = MASSpecValidator(mas_spec, base_dir, strict=strict)
    result = validator.validate()

    print(f"\n{result.summary()}\n")

    if verbose or result.has_errors():
        for error in result.errors:
            print(error)

    if verbose or result.has_warnings():
        for warning in result.warnings:
            print(warning)

    if verbose:
        for info in result.info:
            print(info)

    raise SystemExit(0 if result.valid else 1)


@click.command("check-config")
@click.argument("workspace", type=Path, default=None, required=False,
                metavar="[WORKSPACE_DIR]")
@click.option("--fix", is_flag=True, default=False,
              help="(reserved) Auto-fix trivial violations. Not yet implemented.")
def check_config_cmd(workspace: Path | None, fix: bool) -> None:  # noqa: FBT001
    """Check YAML/JSON manifests for config hygiene violations.

    Enforces the declarative-config separation rule:

    \b
    MODEL_IN_NON_AGENT    model: key in flavour/overlay/infra/mas.yaml
                          (model selection belongs in agent manifests)
    ACCESS_IN_NON_FLAVOUR api_base/api_key_env/provider in agent.yaml,
                          overlays, or mas.yaml
                          (access config belongs in flavour files)

    Exits 0 when no violations are found, 1 otherwise.
    """
    from mas.lab.config_hygiene import run_hygiene_check

    ws = Path(workspace).resolve() if workspace else Path.cwd()
    report = run_hygiene_check(ws)
    print(report.format_full())
    raise SystemExit(0 if report.ok else 1)
