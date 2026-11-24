#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``mas-ctl`` loads a command's module when the command runs; its ``--help`` listing must still match the commands."""

from __future__ import annotations

import inspect
import subprocess
import sys

import click
import pytest
from mas.ctl.cli.main import LAZY_COMMANDS, app


@pytest.mark.parametrize("name", sorted(LAZY_COMMANDS))
def test_listed_help_is_the_commands_own(name: str) -> None:
    command = app.get_command(click.Context(app), name)
    assert command is not None
    assert command.name is not None
    # Python before 3.13 keeps the docstring indentation; 3.13 strips it. Compare the cleaned text.
    assert inspect.cleandoc(command.help or "") == inspect.cleandoc(LAZY_COMMANDS[name][2])


def test_a_command_added_to_the_app_is_listed_and_run() -> None:
    @app.command("lazy-probe-command")
    def _probe() -> None:
        click.echo("ran")

    try:
        ctx = click.Context(app)
        assert "lazy-probe-command" in app.list_commands(ctx)
        assert app.get_command(ctx, "lazy-probe-command") is _probe
    finally:
        app.commands.pop("lazy-probe-command", None)


def test_help_starts_without_the_kernel() -> None:
    code = (
        "import sys\n"
        "from mas.ctl.cli.main import app\n"
        "try:\n"
        "    app(['--help'])\n"
        "except SystemExit:\n"
        "    pass\n"
        "heavy = [m for m in ('mas.runtime.kernel.orchestrator', 'mas.ctl.session.controller') if m in sys.modules]\n"
        "print('HEAVY', heavy)\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60, check=True)
    assert out.stdout.strip().splitlines()[-1] == "HEAVY []"
