#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""mas-ctl CLI — compose, chat, TUI (runtime has no UI)."""

from __future__ import annotations

import mas.third_party_otel  # noqa: F401  # isort: skip -- must import before anything that imports a2a

from pathlib import Path

import click
from mas.ctl.cli.commands.bundles import list_bundles_cmd
from mas.ctl.cli.commands.chat import chat_cmd
from mas.ctl.cli.commands.checkpoint import checkpoint_group
from mas.ctl.cli.commands.compile import compile_cmd
from mas.ctl.cli.commands.compose import compose_cmd, plan_cmd
from mas.ctl.cli.commands.control import control_group
from mas.ctl.cli.commands.hardening import hardening_group
from mas.ctl.cli.commands.plugin import plugin_group
from mas.ctl.cli.commands.registry import registry_group
from mas.ctl.cli.commands.run_mas import run_mas_cmd
from mas.ctl.cli.commands.serve import serve_agent_cmd
from mas.ctl.cli.commands.tui import tui_cmd
from mas.ctl.cli.commands.validate import schemas_cmd, validate_cmd
from mas.ctl.cli.commands.workspace import flavour_group, infra_group
from mas.ctl.env import load_dotenv
from mas.ctl.logging_setup import setup_logging
from mas.ctl.run_progress import set_run_progress


@click.group()
@click.option("-v", "--verbose", count=True, default=0)
@click.option("--env", "env_file", default=None, type=click.Path(exists=True), help=".env file")
@click.pass_context
def app(ctx: click.Context, verbose: int, env_file: str | None) -> None:
    """MAS ctl — compose, placement, and conversation UI for the Mealy runtime."""
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose
    load_dotenv(explicit=Path(env_file) if env_file else None)
    setup_logging(verbosity=verbose)
    set_run_progress(verbose)


app.add_command(chat_cmd, name="chat")
app.add_command(tui_cmd, name="tui")
app.add_command(compile_cmd, name="compile")
app.add_command(compose_cmd, name="compose")
app.add_command(plan_cmd, name="plan")
app.add_command(run_mas_cmd, name="run-mas")
app.add_command(list_bundles_cmd, name="list-bundles")
app.add_command(checkpoint_group, name="checkpoint")
app.add_command(control_group, name="control")
app.add_command(hardening_group, name="hardening")
app.add_command(validate_cmd, name="validate")
app.add_command(schemas_cmd, name="schemas")
app.add_command(registry_group, name="registry")
app.add_command(serve_agent_cmd, name="serve")
app.add_command(plugin_group, name="plugin")
app.add_command(infra_group, name="infra")
app.add_command(flavour_group, name="flavour")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
