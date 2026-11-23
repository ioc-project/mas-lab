#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""mas-ctl secrets — init, set, list, import-env. Never prints a value."""

from __future__ import annotations

import sys
from pathlib import Path

import click
from mas.ctl.secrets.inspect import list_secret_refs
from mas.ctl.secrets.store import build_resolver, secret_store_kind, secrets_dir_from_workspace
from mas.ctl.secrets.write import import_env_file, init_secrets_dir, write_file_secret
from mas.ctl.workspace.config import WorkspaceConfig
from mas.runtime.secrets import SecretError, SecretRefError

_ENV_STORE_READ_ONLY = (
    "the env store reads MAS_SECRET_* variables; set the variable in the environment, or use --secret-store file"
)


def _store(ctx: click.Context) -> tuple[str, Path]:
    """The selected store kind and the file store directory."""
    obj, workspace = ctx.obj or {}, WorkspaceConfig.load()
    kind = secret_store_kind(workspace, override=obj.get("secret_store"))
    return kind, secrets_dir_from_workspace(workspace, override=obj.get("secrets_dir"))


def _writable_directory(ctx: click.Context) -> Path:
    """The file store directory a write subcommand writes to; the env store cannot be written."""
    kind, directory = _store(ctx)
    if kind != "file":
        raise click.ClickException(_ENV_STORE_READ_ONLY if kind == "env" else f"unknown secret store {kind!r}")
    return directory


@click.group("secrets")
def secrets_group() -> None:
    """File-store secrets. list prints names and presence only."""


@secrets_group.command("init")
@click.pass_context
def init_cmd(ctx: click.Context) -> None:
    """Create ``<workspace>/.mas/secrets`` with mode 0700 and a ``.gitignore``."""
    try:
        click.echo(str(init_secrets_dir(_writable_directory(ctx))))
    except PermissionError as exc:
        raise click.ClickException(str(exc)) from None


def _read_value() -> bytes:
    """A hidden prompt with confirmation on a terminal; stdin otherwise (up to the store's cap). Never argv."""
    from mas.library.standard.plugins.secrets.file_store import DEFAULT_MAX_BYTES

    if sys.stdin.isatty():
        typed = click.prompt("Secret value", hide_input=True, confirmation_prompt=True, show_default=False)
        return str(typed).encode("utf-8")
    # Two more bytes than the cap: a value at the cap may still end in a CR LF that is stripped.
    payload = sys.stdin.buffer.read(DEFAULT_MAX_BYTES + 3)
    if len(payload) > DEFAULT_MAX_BYTES + 2:
        raise click.ClickException(f"the value is larger than {DEFAULT_MAX_BYTES} bytes")
    return payload[:-2] if payload.endswith(b"\r\n") else payload.removesuffix(b"\n")


# Anything after NAME (a value typed on the command line, an unknown option) is taken here and refused with a
# fixed message: Click's own "unexpected extra argument (...)" or "no such option" would print it back.
_REFUSE_EXTRA = {"allow_extra_args": True, "ignore_unknown_options": True}


@secrets_group.command("set", context_settings=_REFUSE_EXTRA)
@click.argument("name")
@click.option("--key", "secret_key", default="value", help="Keyed secret (default value).")
@click.pass_context
def set_cmd(ctx: click.Context, name: str, secret_key: str) -> None:
    """Write a secret from a hidden prompt or stdin. Never pass the value on the command line."""
    if ctx.args:
        raise click.UsageError("the value is read from stdin or a prompt, never from the command line")
    directory = _writable_directory(ctx)
    payload = _read_value()
    if not payload:
        raise click.ClickException("the value is empty")
    try:
        click.echo(str(write_file_secret(directory, name, payload, key=secret_key)))
    except (SecretError, SecretRefError, PermissionError) as exc:
        raise click.ClickException(str(exc)) from None


@secrets_group.command("list")
@click.pass_context
def list_cmd(ctx: click.Context) -> None:
    """List secret names and keys in the file store, and whether each one reads. No values."""
    kind, directory = _store(ctx)
    if kind != "file":
        raise click.ClickException(f"the {kind} secret store cannot be listed")
    resolver = build_resolver(directory=directory, store=kind, workspace=WorkspaceConfig.load())
    refs = list_secret_refs(directory)
    if not refs:
        click.echo(f"no secrets in {directory}")
    for ref in refs:
        info = resolver.describe(ref)
        state = "present" if info.present else info.reason or "missing"
        click.echo(f"{ref.name}/{ref.key}  {state}")


@secrets_group.command("import-env")
@click.argument("env_file")
@click.pass_context
def import_env_cmd(ctx: click.Context, env_file: str) -> None:
    """Write one file-store secret per known variable of a dotenv file. Does not export to the process.

    Imports the provider key variables (for example OPENAI_API_KEY as secret openai) and
    MAS_SECRET_* names. Other variables are listed as skipped.
    """
    env_path = Path(env_file).expanduser()
    if not env_path.is_file():
        # Never repeat the argument: it may be a secret typed in the wrong place.
        raise click.ClickException("no such dotenv file")
    directory = _writable_directory(ctx)
    try:
        imported, skipped = import_env_file(env_path, directory)
    except (SecretError, SecretRefError, PermissionError) as exc:
        raise click.ClickException(str(exc)) from None
    for ref in imported or ["no secrets imported"]:
        click.echo(ref)
    for name in skipped:
        click.echo(f"skipped {name} (not a known secret variable)")
