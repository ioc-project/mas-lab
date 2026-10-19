#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from pathlib import Path

from mas.lab.cli.manpage import CLI_MAN_PAGES, slug
from mas.lab.cli.pipeline_shortcuts import CLI_PIPELINE_SHORTCUTS


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "library-telemetry" / "library.yaml").is_file() and (
            parent / "lab" / "docs" / "cli" / "index.md"
        ).is_file():
            return parent
    raise AssertionError("cannot locate mas-lab repo root")


def test_every_man_page_file_has_name_and_synopsis() -> None:
    root = _repo_root()
    missing: list[str] = []
    for page in CLI_MAN_PAGES:
        dest = root / page.path
        if not dest.is_file():
            missing.append(f"missing {page.command} → {page.path}")
            continue
        text = dest.read_text(encoding="utf-8")
        title = slug(page.command)
        if f"# {title}(1)" not in text and f"# {title}" not in text:
            missing.append(f"{page.path}: missing title {title}(1)")
        if "## NAME" not in text or "## SYNOPSIS" not in text:
            missing.append(f"{page.path}: missing NAME/SYNOPSIS")
        if page.command.split()[-1] not in text and page.command not in text:
            missing.append(f"{page.path}: does not mention {page.command}")
    assert not missing, "man pages:\n" + "\n".join(missing)


def test_every_pipeline_shortcut_has_a_man_page() -> None:
    by_cmd = {p.command: p for p in CLI_MAN_PAGES}
    missing = [
        item.command
        for item in CLI_PIPELINE_SHORTCUTS
        if item.command not in by_cmd
    ]
    assert not missing, "pipeline shortcuts without man pages: " + ", ".join(missing)


def test_site_index_exists() -> None:
    dest = _repo_root() / "docs" / "cli" / "mas-lab.md"
    text = dest.read_text(encoding="utf-8")
    assert "library-telemetry/docs/cli" in text
    assert "library-kg/docs/cli" in text
    assert "lab/docs/cli" in text


def test_click_leaves_are_in_the_man_catalog() -> None:
    from mas.lab.cli import app
    from mas.lab.cli.manpage import CLI_MAN_PAGES, walk_click_commands

    catalog = {page.command for page in CLI_MAN_PAGES}
    missing = []
    for command, cmd in walk_click_commands(app):
        if getattr(cmd, "hidden", False):
            continue
        if command in catalog:
            continue
        # `mas-lab otel` is the same Click group as `mas-lab telemetry`.
        if command.startswith("mas-lab otel"):
            aliased = "mas-lab telemetry" + command[len("mas-lab otel") :]
            if aliased in catalog:
                continue
        missing.append(command)
    assert not missing, "Click commands without man pages:\n" + "\n".join(missing)


def test_pipeline_run_defaults_to_parallel() -> None:
    from mas.lab.cli.commands.benchmark.pipeline import pipeline_run_cmd

    opt = next(p for p in pipeline_run_cmd.params if "--parallel" in getattr(p, "opts", ()))
    assert opt.default is True
