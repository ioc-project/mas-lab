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
    "--revision",
    default=None,
    help="Branch or immutable commit SHA to pin the import to.",
)
@click.option(
    "--sha256",
    default=None,
    help="Expected SHA-256 digest of the tarball; import fails if the downloaded file does not match.",
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
@click.option(
    "--token",
    default=None,
    help="Hugging Face access token. Overrides --token-file and HF_TOKEN.",
)
@click.option(
    "--token-file",
    type=Path,
    default=None,
    help="Read the Hugging Face access token from a file.",
)
def import_hf_cmd(
    path_in_repo: str,
    repo_id: str,
    revision: str | None,
    sha256: str | None,
    output_dir: Path | None,
    trace_cache_dir: Path | None,
    dry_run: bool,
    token: str | None,
    token_file: Path | None,
) -> None:
    """Download a benchmark archive from a Hugging Face dataset repo and import it.

    ctl resolves the token from ``--token``, ``--token-file``, or ``HF_TOKEN``.
    ``--dry-run`` still uses ``HF_TOKEN`` when it is set; it does not require
    a token when none is available.
    """
    from mas.ctl.env import huggingface_token
    from mas.lab.benchmark.cli.export_import import import_hf_command

    try:
        resolved: str | None = huggingface_token(token=token, token_file=token_file)
    except ValueError:
        if not dry_run:
            raise
        resolved = None
    args = SimpleNamespace(
        repo_id=repo_id,
        path_in_repo=path_in_repo,
        revision=revision,
        sha256=sha256,
        output_dir=output_dir,
        trace_cache_dir=trace_cache_dir,
        dry_run=dry_run,
        token=resolved,
    )
    raise SystemExit(import_hf_command(args))
