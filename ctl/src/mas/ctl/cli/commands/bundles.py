#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""List manifest libraries and infra bundles from installed packages."""

from __future__ import annotations

import click
from mas.ctl.libraries.bundles import list_bundles, list_manifest_libraries


@click.command("list-bundles")
@click.option("-v", "--verbose", is_flag=True)
def list_bundles_cmd(verbose: bool) -> None:
    """List infra bundles from mas.runtime.manifest_libraries entry points."""
    libs = list_manifest_libraries()
    if not libs:
        click.echo("No manifest libraries registered.")
        click.echo("Install a library that registers mas.runtime.manifest_libraries (for example library-standard/).")
        return

    click.echo("Manifest libraries:")
    for name, pkg in sorted(libs.items()):
        click.echo(f"  {name}  ({pkg})")

    bundles = list_bundles(verbose=verbose)
    if not bundles:
        click.echo("\nNo bundle YAML files found under libs/<library>/.")
        return

    click.echo("\nBundles:")
    for b in bundles:
        meta = f"  kind={b.kind}" if b.kind else ""
        click.echo(f"  {b.ref}{meta}  — {b.name}")
