#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``mas-lab benchmark update`` command."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import click


@click.command("update")
@click.argument("benchmark_id")
@click.option("--name", default=None, help="Set benchmark name.")
@click.option("--tags", default=None, help="Set tags (comma-separated, replaces all).")
@click.option("--add-tag", default=None, help="Add a single tag.")
@click.option("--remove-tag", default=None, help="Remove a single tag.")
@click.option("-o", "--output-dir", type=Path, default=None)
def update_cmd(benchmark_id: str, name: str | None, tags: str | None,
               add_tag: str | None, remove_tag: str | None,
               output_dir: Path | None) -> None:
    """Update benchmark metadata."""
    from mas.lab.benchmark.cli import update_command

    args = SimpleNamespace(benchmark_id=benchmark_id, name=name, tags=tags,
                           add_tag=add_tag, remove_tag=remove_tag,
                           output_dir=output_dir)
    raise SystemExit(update_command(args))
