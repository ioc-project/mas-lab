#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from click.testing import CliRunner

from mas.lab.cli import app
from mas.lab.cli.manpage import walk_click_commands


def test_every_click_command_help_exits_zero() -> None:
    runner = CliRunner()
    failed: list[str] = []
    for command, cmd in walk_click_commands(app):
        if getattr(cmd, "hidden", False):
            continue
        args = command.split()[1:] + ["--help"]
        result = runner.invoke(app, args)
        if result.exit_code != 0:
            failed.append(f"{command}: exit {result.exit_code}\n{result.output[:500]}")
    assert not failed, "CLI --help smoke failed:\n\n" + "\n\n".join(failed)
