#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``mas-lab benchmark artifact`` commands."""
from __future__ import annotations

from types import SimpleNamespace

import click


@click.group("artifact")
def artifact_group() -> None:
    """List and show artifacts for an experiment."""


_LEVEL = click.Choice(["application", "scenario", "test", "run"], case_sensitive=False)


@artifact_group.command("list")
@click.argument("target")
@click.option("-t", "--type", "artifact_type", default=None, metavar="TYPE",
              help="Filter by artifact type.")
@click.option("-l", "--level", "level", type=_LEVEL, default=None,
              help="Filter to one hierarchy level.")
@click.option("-v", "--verbose", is_flag=True, default=False,
              help="Include the type description.")
def artifact_list_cmd(
    target: str,
    artifact_type: str | None,
    level: str | None,
    verbose: bool,
) -> None:
    """List artifacts declared in the experiment, and whether each was generated.

    TARGET is ``last``, a benchmark id, or an experiment YAML. The run directory
    is resolved from the last-run pointer or the experiment identity — not passed
    as an argument.
    """
    from mas.lab.benchmark.cli import artifact_list_command

    raise SystemExit(artifact_list_command(SimpleNamespace(
        target=target,
        artifact_type=artifact_type,
        level=level,
        verbose=verbose,
    )))


@artifact_group.command("show")
@click.argument("artifact_id")
def artifact_show_cmd(artifact_id: str) -> None:
    """Show one generated artifact by its 8-character id."""
    from mas.lab.benchmark.cli import show_artifact_by_id_command

    raise SystemExit(show_artifact_by_id_command(SimpleNamespace(
        artifact_id=artifact_id,
    )))
