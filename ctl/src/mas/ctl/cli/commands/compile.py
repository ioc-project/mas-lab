#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""mas-ctl compile — emit resolved Agent/MAS YAML after overlays + defaults."""

from __future__ import annotations

from pathlib import Path

import click
from mas.ctl.cli.help_text import COMPILE_EPILOG
from mas.ctl.compile import (
    CompileError,
    compile_manifest,
    compiled_documents,
    dump_yaml,
    render_header,
    resolve_layout,
    write_compiled,
)
from mas.ctl.paths import manifest_cwd


@click.command("compile", epilog=COMPILE_EPILOG)
@click.argument("manifest")
@click.option("-o", "--overlay", "overlays", multiple=True, type=click.Path())
@click.option(
    "--override",
    "overrides",
    multiple=True,
    metavar="PATH=VALUE",
    help="Schema-validated overlay override (repeatable; applied last).",
)
@click.option(
    "--output",
    "-O",
    "output_path",
    default=None,
    type=click.Path(),
    help="Directory (tree layout) or YAML file (bundle / single agent).",
)
@click.option(
    "--layout",
    type=click.Choice(["auto", "tree", "bundle"], case_sensitive=False),
    default="auto",
    show_default=True,
    help="tree = folder of manifests; bundle = one YAML (MAS agents inlined).",
)
@click.option("--no-defaults", is_flag=True, help="Do not fill omitted runtime defaults")
@click.option(
    "--kind",
    "schema_kind",
    default=None,
    help="Explicit registered schema for documents without a kind (e.g. config).",
)
@click.option("--no-validate", is_flag=True, help="Skip schema validation")
@click.option("--no-header", is_flag=True, help="Omit the generated-by comment header")
def compile_cmd(
    manifest: str,
    overlays: tuple[str, ...],
    overrides: tuple[str, ...],
    output_path: str | None,
    layout: str,
    no_defaults: bool,
    no_validate: bool,
    no_header: bool,
    schema_kind: str | None = None,
) -> None:
    """Patch any registered manifest; resolve overlays and defaults for Agent/MAS.

    Unlike ``compose`` (EffectiveBind + placement), this dumps the YAML dict
    the runtime holds after overlay merge. Default ``--layout auto`` writes a
    single file (or stdout) for one agent / ``--layout bundle``, and a folder
    of ``mas.yaml`` + ``agents/`` when ``--output`` is a directory.
    """
    try:
        with manifest_cwd(manifest, overlay_paths=overlays) as session:
            compiled = compile_manifest(
                session.manifest,
                list(session.overlays),
                overrides=overrides,
                fill_defaults=not no_defaults,
                validate=not no_validate,
                schema_kind=schema_kind,
            )
            dest = Path(output_path).expanduser() if output_path else None
            if dest is not None and not dest.is_absolute():
                dest = (session.original_cwd / dest).resolve()
            resolved = resolve_layout(dest, layout)
            if dest is None:
                docs = compiled_documents(compiled, resolved)
                text = dump_yaml(next(iter(docs.values())))
                if not no_header:
                    text = render_header(compiled) + text
                click.echo(text, nl=not text.endswith("\n"))
                return
            written = write_compiled(
                compiled,
                output=dest,
                layout=resolved,
                header=not no_header,
            )
    except (CompileError, FileNotFoundError, ValueError) as exc:
        click.echo(str(exc), err=True)
        raise SystemExit(1) from exc

    for path in written:
        click.echo(f"Wrote {path}", err=True)
