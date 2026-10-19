#!/usr/bin/env python3
#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Rewrite ``experiment.execution`` into design / schedule / bench_emulation.

Usage::

    python scripts/migrate_experiment_execution.py PATH [PATH ...]
    python scripts/migrate_experiment_execution.py --root .   # all experiment YAML

The mapper is :func:`mas.lab.lab.config.execution.split_legacy_execution`.
``run.n_runs`` is left in place; a nested ``execution.n_runs`` is dropped
(the dual-read loader still honours it until strict rejection).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

# Keep this import path working when the script is run from a source checkout
# without installing the package (tests add src onto sys.path first).
_BENCH_SRC = ROOT / "lab" / "components" / "bench" / "src"
_CORE_SRC = ROOT / "lab" / "components" / "core" / "src"
for _src in (_BENCH_SRC, _CORE_SRC):
    if str(_src) not in sys.path:
        sys.path.insert(0, str(_src))

from mas.lab.lab.config.execution import split_legacy_execution  # noqa: E402


def migrate_experiment_dict(data: dict) -> dict:
    """Return a new dict with ``execution:`` split; no-op when already migrated."""
    if not isinstance(data, dict):
        return data
    wrapped = "experiment" in data
    exp = data.get("experiment", data)
    if not isinstance(exp, dict) or "execution" not in exp:
        return data
    mapped = split_legacy_execution(exp["execution"] or {})
    new_exp = dict(exp)
    del new_exp["execution"]
    # New keys win if the file already mixed both shapes.
    for key, value in mapped.items():
        if key in new_exp and isinstance(new_exp[key], dict) and isinstance(value, dict):
            merged = dict(value)
            merged.update(new_exp[key])
            new_exp[key] = merged
        elif key not in new_exp:
            new_exp[key] = value
    if wrapped:
        return {**data, "experiment": new_exp}
    return new_exp


def _dump(data: dict) -> str:
    return yaml.safe_dump(
        data,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    )


def _block_indent(line: str) -> int | None:
    if not line.strip() or line.lstrip().startswith("#"):
        return None
    return len(line) - len(line.lstrip(" "))


def rewrite_execution_block(text: str) -> str:
    """Replace a top-level ``execution:`` mapping in YAML text, keep the rest."""
    lines = text.splitlines(keepends=True)
    start = None
    indent = None
    for i, line in enumerate(lines):
        if line.lstrip().startswith("#"):
            continue
        stripped = line.lstrip(" ")
        if stripped.startswith("execution:"):
            start = i
            indent = len(line) - len(stripped)
            break
    if start is None or indent is None:
        return text

    end = len(lines)
    for j in range(start + 1, len(lines)):
        kind = _block_indent(lines[j])
        if kind is None:
            # Blank lines belong to the block; comments after the mapping
            # (same or lesser indent as `execution:`) close it.
            stripped = lines[j].lstrip()
            if stripped.startswith("#"):
                comment_indent = len(lines[j]) - len(stripped)
                if comment_indent <= indent:
                    end = j
                    break
            continue
        if kind <= indent:
            end = j
            break

    block = "".join(lines[start:end])
    parsed = yaml.safe_load(block)
    if not isinstance(parsed, dict) or "execution" not in parsed:
        return text
    mapped = split_legacy_execution(parsed["execution"] or {})
    if not mapped:
        # Drop an empty execution: block.
        return "".join(lines[:start] + lines[end:])

    dumped = yaml.safe_dump(mapped, sort_keys=False, default_flow_style=False)
    dumped_lines = dumped.splitlines(True)
    # yaml.safe_dump emits keys at column 0; re-indent to match the original block.
    pad = " " * indent
    rewritten = [pad + ln if ln.strip() else ln for ln in dumped_lines]
    return "".join(lines[:start] + rewritten + lines[end:])


def migrate_file(path: Path, *, dry_run: bool = False) -> bool:
    """Rewrite *path* in place. Return True when the file changed."""
    original = path.read_text(encoding="utf-8")
    data = yaml.safe_load(original)
    if not isinstance(data, dict):
        return False
    if migrate_experiment_dict(data) == data:
        return False
    text = rewrite_execution_block(original)
    if text == original:
        # Fallback: full dump (comments lost) when the block walker misses.
        header = ""
        kept: list[str] = []
        for line in original.splitlines(keepends=True):
            stripped = line.strip()
            if stripped.startswith("#") or stripped == "":
                kept.append(line)
                continue
            break
        header = "".join(kept)
        text = header + _dump(migrate_experiment_dict(data))
    if not dry_run:
        path.write_text(text, encoding="utf-8")
    return text != original


def _discover(root: Path) -> list[Path]:
    paths: list[Path] = []
    for pattern in (
        "labs/**/experiment*.yaml",
        "library-samples/experiments/**/*.yaml",
        "docs/tutorials/**/experiment*.yaml",
        "tests/fixtures/**/*.yaml",
        "lab/components/controller/tests/fixtures/**/*.yaml",
        "lab/src/mas/lab/templates/**/*.yaml",
        "docs/schemas/examples/experiments/**/*.yaml",
    ):
        paths.extend(root.glob(pattern))
    return sorted({p.resolve() for p in paths if p.is_file()})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--root", type=Path, default=None, help="Discover experiment YAML under this tree")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    targets = [p.resolve() for p in args.paths]
    if args.root is not None:
        targets.extend(_discover(args.root.resolve()))
    if not targets:
        targets = _discover(ROOT)

    changed = 0
    for path in targets:
        if migrate_file(path, dry_run=args.dry_run):
            print(f"{'would rewrite' if args.dry_run else 'rewrote'}: {path}")
            changed += 1
    print(f"{changed} file(s) {'would change' if args.dry_run else 'changed'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
