#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import re
from pathlib import Path

import yaml

from mas.lab.cli.pipeline_shortcuts import (
    CLI_PIPELINE_SHORTCUTS,
    library_root_for_shortcut,
    resolve_shortcut_pipeline,
    shortcut_pipeline_refs,
)
from mas.runtime.package_refs import resolve_path_ref


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "library-telemetry" / "library.yaml").is_file() and (
            parent / "library-kg" / "library.yaml"
        ).is_file():
            return parent
    raise AssertionError("cannot locate mas-lab repo root from test file")


def _source_file(item) -> Path:
    path = _repo_root() / item.source_path
    assert path.is_file(), f"{item.command}: pipeline file removed: {path}"
    return path


def test_every_shortcut_pipeline_file_exists() -> None:
    for item in CLI_PIPELINE_SHORTCUTS:
        _source_file(item)


def test_shortcut_pipeline_yaml_declares_step_types() -> None:
    for item in CLI_PIPELINE_SHORTCUTS:
        data = yaml.safe_load(_source_file(item).read_text(encoding="utf-8")) or {}
        steps = (data.get("spec") or {}).get("steps") or []
        types = {str(step.get("type") or "") for step in steps if isinstance(step, dict)}
        missing = [t for t in item.step_types if t not in types]
        assert not missing, (
            f"{item.command}: {item.source_path} missing step types {missing}; has {sorted(types)}"
        )


def test_every_shortcut_pipeline_resolves() -> None:
    """Refs must resolve with the same candidate rules as ``resolve_path_ref``."""
    root = _repo_root()
    for item in CLI_PIPELINE_SHORTCUTS:
        resolved = resolve_shortcut_pipeline(item, root)
        assert resolved.resolve() == _source_file(item).resolve(), (
            f"{item.command}: {item.pipeline_ref} resolved to {resolved}, "
            f"not {item.source_path}"
        )


def test_shortcut_refs_resolve_when_library_is_installed() -> None:
    """When the scheme is on the entry-point path, URN refs must resolve to the YAML."""
    unresolved: list[str] = []
    skipped = 0
    for ref in shortcut_pipeline_refs():
        try:
            path = resolve_path_ref(ref, _repo_root())
        except LookupError:
            skipped += 1
            continue
        if not path.is_file():
            unresolved.append(f"{ref} → {path}")
    if skipped == len(shortcut_pipeline_refs()):
        item = next(iter(CLI_PIPELINE_SHORTCUTS))
        assert _source_file(item).is_file()
        return
    assert not unresolved, "installed library scheme did not resolve:\n" + "\n".join(unresolved)


def test_library_root_matches_source_path() -> None:
    root = _repo_root()
    for item in CLI_PIPELINE_SHORTCUTS:
        lib = library_root_for_shortcut(item, root)
        assert lib.is_dir() and (lib / "library.yaml").is_file(), (
            f"{item.command}: {lib} is not a library root"
        )
        assert item.source_path.startswith(lib.name + "/"), (
            f"{item.command}: source_path {item.source_path} is not under {lib.name}"
        )


def _click_command_names(lab_cli: Path) -> set[str]:
    text = lab_cli.read_text(encoding="utf-8")
    names = set(re.findall(r'\.command\("([^"]+)"\)', text))
    names.update(re.findall(r'\.add_command\([^,]+,\s*name="([^"]+)"\)', text))
    return names


def test_shortcut_click_commands_are_registered() -> None:
    root = _repo_root()
    names = {
        "telemetry": _click_command_names(
            root / "library-telemetry" / "src" / "mas" / "library" / "telemetry" / "lab_cli.py"
        ),
        "kg": _click_command_names(
            root / "library-kg" / "src" / "mas" / "library" / "kg" / "lab_cli.py"
        ),
    }
    from mas.lab.cli.commands.plot import plot_group

    names["plot"] = set(plot_group.commands)
    missing: list[str] = []
    for item in CLI_PIPELINE_SHORTCUTS:
        have = names.get(item.cli_group)
        if have is None:
            missing.append(f"{item.command}: unknown cli_group {item.cli_group!r}")
            continue
        if item.cli_name not in have:
            missing.append(
                f"{item.command}: {item.cli_group}.{item.cli_name} not registered "
                f"(have {sorted(have)})"
            )
    assert not missing, "CLI shortcut commands missing:\n" + "\n".join(missing)
