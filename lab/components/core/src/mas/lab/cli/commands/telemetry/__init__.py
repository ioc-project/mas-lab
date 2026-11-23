#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``mas-lab telemetry`` CLI: native traces and ClickHouse queries (library-telemetry adds the OTel commands)."""

from __future__ import annotations

import click

from .apps import apps_group
from .dump import dump_cmd
from .sessions import sessions_group
from .show import show_cmd
from .traces import traces_group


@click.group("telemetry")
def telemetry_group() -> None:
    """Telemetry utilities: native traces and ClickHouse OTel queries."""


telemetry_group.add_command(show_cmd)
telemetry_group.add_command(dump_cmd)
telemetry_group.add_command(apps_group)
telemetry_group.add_command(sessions_group)
telemetry_group.add_command(traces_group)

__all__ = ["telemetry_group"]
