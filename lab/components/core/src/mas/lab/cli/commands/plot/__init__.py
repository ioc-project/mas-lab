#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Click commands for ``mas-lab plot``."""
from __future__ import annotations

import click

from .communication_flow import communication_flow_cmd
from .list_cmd import list_cmd
from .multilevel_trajectory import multilevel_trajectory_cmd
from .trajectory import trajectory_cmd


@click.group("plot")
def plot_group() -> None:
    """Generate trajectory visualisations from native ``events.jsonl`` traces.

    \b
    Commands:
      trajectory              Delegation-flow diagram (mermaid / table / html / svg)
      multilevel-trajectory   Multilevel swimlane diagram (html / svg)
      communication-flow      Agent-to-agent routing graph (html / mermaid)
      list                    Discover generated plot artefacts in a directory

    \b
    Source resolution:
      FILE PATH         Path to ``events.jsonl``
      LAB SHORTHAND     <lab>/<experiment>[/<scenario>/<item>/<run>]
                        resolved under labs_root()/; missing segments
                        auto-expand to the first child
      RUN ID            e.g. 20260224-062142-baseline-673d6359

    \b
    Examples:
      mas-lab plot trajectory path/to/events.jsonl --format svg -o traj.svg
      mas-lab plot multilevel-trajectory tutorials/t3-analysis --format html -o out.html
      mas-lab plot communication-flow tutorials/t3-analysis/baseline/item1/r1
    """


plot_group.add_command(trajectory_cmd)
plot_group.add_command(multilevel_trajectory_cmd)
plot_group.add_command(communication_flow_cmd)
plot_group.add_command(list_cmd)

__all__ = ["plot_group", "resolve_source"]

from ._common import resolve_source  # noqa: E402
