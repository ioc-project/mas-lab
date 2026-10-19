#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``mas-lab benchmark import-hf`` command."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import click


@click.command("import-hf")
@click.argument("path_in_repo")
@click.option(
    "--repo-id",
    required=True,
    help="Hugging Face dataset repo id, e.g. <user-or-org>/<repo>.",
)
@click.option(
    "--output-dir",
    type=Path,
    default=None,
    help="Directory to restore the benchmark output into.",
)
@click.option(
    "--trace-cache",
    "trace_cache_dir",
    type=Path,
    default=None,
    help="Directory to restore trace-cache entries into.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Download metadata path and preview the local import without writing outputs.",
)
def import_hf_cmd(
    path_in_repo: str,
    repo_id: str,
    output_dir: Path | None,
    trace_cache_dir: Path | None,
    dry_run: bool,
) -> None:
    """Download a benchmark archive from a Hugging Face dataset repo and import it.

    Authentication uses the ``HF_TOKEN`` environment variable.
    """
    from mas.lab.benchmark.cli.export_import import import_hf_command

    args = SimpleNamespace(
        repo_id=repo_id,
        path_in_repo=path_in_repo,
        output_dir=output_dir,
        trace_cache_dir=trace_cache_dir,
        dry_run=dry_run,
    )
    raise SystemExit(import_hf_command(args))
