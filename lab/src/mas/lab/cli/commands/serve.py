#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``mas-lab serve`` — start the controller daemon HTTP API.

    mas-lab serve
    mas-lab serve --port 8090
"""
from __future__ import annotations

from pathlib import Path

import click


@click.command("serve")
@click.option(
    "--port",
    "-p",
    type=int,
    default=8090,
    show_default=True,
    help="TCP port for the controller HTTP API.",
)
@click.option(
    "--host",
    default="127.0.0.1",
    show_default=True,
    help="Bind address. Non-loopback binds require --cors-origin for browser UIs.",
)
@click.option(
    "--cors-origin",
    "cors_origins",
    multiple=True,
    help="Allowed CORS origin (repeatable). Default: permissive on loopback, none otherwise.",
)
@click.option(
    "--token-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="File whose contents are the bearer token. Unset = open API.",
)
@click.option(
    "--state-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Directory for the run store, job store, and uploads.",
)
@click.option(
    "--workspace",
    type=click.Path(file_okay=False, exists=True, path_type=Path),
    default=None,
    help=(
        "Project folder for config.yaml, labs, libraries, and the writable "
        "Workspace library. Sets MAS_WORKSPACE_ROOT."
    ),
)
@click.pass_context
def serve_cmd(ctx: click.Context, port: int, host: str, cors_origins, token_file, state_dir, workspace) -> None:
    """Launch the MAS Lab controller daemon.

    \b
    Examples
    --------
    mas-lab serve
    mas-lab serve -p 8090
    mas-lab serve --workspace ./labs/my-experiment.lab
    mas-lab serve --host 0.0.0.0 --token-file ./token --cors-origin http://localhost:8080
    """
    click.echo()
    click.echo("=" * 60)
    click.echo("  MAS Lab Serve — controller daemon")
    click.echo("=" * 60)
    click.echo(f"  HTTP       : http://{host}:{port}")
    click.echo()
    click.echo("  Press Ctrl+C to stop")
    click.echo()

    from mas.lab.controller.daemon import main as daemon_main

    argv = ["--host", host, "--port", str(port)]
    for origin in cors_origins:
        argv.extend(["--cors-origin", origin])
    if token_file is not None:
        argv.extend(["--token-file", str(token_file)])
    if state_dir is not None:
        argv.extend(["--state-dir", str(state_dir)])
    if workspace is not None:
        argv.extend(["--workspace", str(workspace)])
    raise SystemExit(daemon_main(argv))
