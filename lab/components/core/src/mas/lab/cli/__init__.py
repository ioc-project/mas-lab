#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""mas-lab click CLI — ``app`` is the single entry point.

Import and use programmatically::

    from mas.lab.cli import app
    from click.testing import CliRunner

    result = CliRunner().invoke(app, ["benchmark", "list"])
"""
from __future__ import annotations

import importlib
import logging

import click
import mas.third_party_otel  # noqa: F401
from dotenv import find_dotenv, load_dotenv
from mas.lab.cli.commands import check
from mas.lab.cli.commands import config as _config_mod
from mas.lab.cli.commands import init as _init_mod
from mas.lab.cli.commands import validate as _validate_mod
from mas.lab.cli.extensions import register_extension_components


@click.group()
@click.option("-v", "--verbose", count=True, default=0,
              help="Verbosity: -v exchange log, -vv debug, -vvv full")
@click.pass_context
def app(ctx: click.Context, verbose: int) -> None:
    """MAS Lab — evaluation, experimentation, and benchmarking tools.

    \b
    Command groups:
      benchmark     Run experiments, list results, manage steps
      control       Start/stop the controller daemon (UI + workers)
      worker        List and manage background workers
      plot          Generate trajectory and communication diagrams
      run           Execute processors and pipelines
      pipe          Stream-oriented processing

    \b
    Utilities:
      check         Validate a MAS configuration
      config        Show resolved paths and environment configuration
      serve         Start the benchmark UI server
      telemetry     Convert, verify, push, and dump traces (library-telemetry)
      kg            Normalize, validate, push, and dump graphs (library-kg)
      eval          Score traces with MCE metrics (library-eval)
      validate      Validate YAML manifests

        \b
        Optional extensions are discovered via the ``mas.lab.cli.components``
        entry-point group and loaded automatically when their packages are installed.

    \b
    Global options:
      -v            Show exchange log
      -vv           Debug output
      -vvv          Full trace output
    """
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose

    try:
        from mas.ctl.logging_setup import setup_logging
        setup_logging(verbosity=verbose)
    except ImportError:
        level = (logging.DEBUG if verbose >= 2
                 else logging.INFO if verbose >= 1
                 else logging.WARNING)
        logging.basicConfig(level=level, format="%(message)s")

    # Workspace config — loaded once, shared via ctx.obj
    try:
        from mas.lab.workspace import WorkspaceConfig
        ctx.obj["workspace"] = WorkspaceConfig.load()
    except ImportError:
        ctx.obj["workspace"] = None

    load_dotenv(find_dotenv(usecwd=True))


def _add_if_importable(name: str, module: str, attr: str) -> None:
    """Attach a command that lives in an optional lab component."""
    try:
        mod = importlib.import_module(module)
    except ImportError:
        return
    app.add_command(getattr(mod, attr), name=name)


app.add_command(check.check_cmd, name="check")
app.add_command(check.check_config_cmd, name="check-config")
app.add_command(_config_mod.config_cmd, name="config")
app.add_command(_init_mod.init_cmd, name="init")
app.add_command(_validate_mod.validate_cmd, name="validate")
_add_if_importable("serve", "mas.lab.cli.commands.serve", "serve_cmd")
_add_if_importable("benchmark", "mas.lab.cli.commands.benchmark", "group")
_add_if_importable("plot", "mas.lab.cli.commands.plot", "plot_group")
_add_if_importable("telemetry", "mas.lab.cli.commands.telemetry", "telemetry_group")
_add_if_importable("run", "mas.lab.cli.commands.run", "run_group")
_add_if_importable("pipe", "mas.lab.cli.commands.pipe", "pipe_group")

# Register optional command groups from installed extension packages.
register_extension_components(app)


def main() -> None:
    app()


if __name__ == "__main__":
    app()
