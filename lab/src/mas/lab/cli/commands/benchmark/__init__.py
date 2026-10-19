#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Click wrappers for ``mas-lab benchmark`` subcommands."""

from __future__ import annotations

import click

from .analyze import analyze_cmd
from .artifacts import artifact_group
from .clean import clean_cmd
from .daemon import daemon_group
from .export_cmd import export_cmd
from .follow import follow_cmd
from .import_cmd import import_cmd
from .import_hf import import_hf_cmd
from .list_cmd import list_cmd
from .migrate import migrate_cmd
from .pipeline import pipeline_group
from .publish_hf import publish_hf_cmd
from .rename import rename_cmd
from .run import run_cmd
from .show import show_cmd
from .step import step_group
from .step_info import step_info_cmd
from .update import update_cmd


@click.group("benchmark")
def group() -> None:
    """Benchmark management — run, list, show, update, analyze, step."""


group.add_command(run_cmd)
group.add_command(list_cmd)
group.add_command(show_cmd)
group.add_command(artifact_group)
group.add_command(update_cmd)
group.add_command(rename_cmd)
group.add_command(migrate_cmd)
group.add_command(analyze_cmd)
group.add_command(export_cmd)
group.add_command(import_cmd)
group.add_command(publish_hf_cmd)
group.add_command(import_hf_cmd)
group.add_command(follow_cmd)
group.add_command(clean_cmd)
group.add_command(step_info_cmd)
group.add_command(step_group)
group.add_command(pipeline_group)
group.add_command(daemon_group)

__all__ = [
    "group",
    "run_cmd",
    "list_cmd",
    "show_cmd",
    "artifact_group",
    "update_cmd",
    "rename_cmd",
    "migrate_cmd",
    "analyze_cmd",
    "export_cmd",
    "import_cmd",
    "publish_hf_cmd",
    "import_hf_cmd",
    "follow_cmd",
    "clean_cmd",
    "step_info_cmd",
    "step_group",
    "pipeline_group",
    "daemon_group",
]
