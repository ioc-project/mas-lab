#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``mas-lab benchmark publish-hf`` command."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import click


@click.command("publish-hf")
@click.argument("tarball", type=Path)
@click.option(
    "--repo-id",
    required=True,
    help="Hugging Face dataset repo id, e.g. <user-or-org>/<repo>.",
)
@click.option(
    "--path-in-repo",
    default=None,
    help=(
        "Destination path inside the HF dataset repo. "
        "Defaults to benchmarks/<experiment>/<short_id>/bundle.tar.gz "
        "based on the archive manifest."
    ),
)
def publish_hf_cmd(tarball: Path, repo_id: str, path_in_repo: str | None) -> None:
    """Upload a benchmark archive tarball to a Hugging Face dataset repo.

    Authentication uses the ``HF_TOKEN`` environment variable.
    """
    from mas.lab.benchmark.cli.export_import import publish_hf_command

    args = SimpleNamespace(
        tarball=tarball,
        repo_id=repo_id,
        path_in_repo=path_in_repo,
    )
    raise SystemExit(publish_hf_command(args))
