#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""mas-ctl CLI — compose, chat, TUI (runtime has no UI)."""

from __future__ import annotations

import mas.third_party_otel  # noqa: F401  # isort: skip -- must import before anything that imports a2a

import importlib
from pathlib import Path
from typing import Any

import click
from click.formatting import HelpFormatter, measure_table
from mas.ctl.env import load_dotenv
from mas.ctl.logging_setup import setup_logging
from mas.ctl.run_progress import set_run_progress
from mas.ctl.secrets import install_process_resolver

# name -> (module, attribute, help). A command's module loads when the command runs, not when ``mas-ctl`` starts:
# most of them pull in the kernel and the session machinery (about 0.4 s of imports), which ``--help``, ``validate``
# and ``secrets`` never use. The help text is the command's own and is listed here only so ``--help`` need not load
# every module; a test compares each entry with the command it names.
LAZY_COMMANDS: dict[str, tuple[str, str, str]] = {
    "chat": (
        "chat",
        "chat_cmd",
        "Run agent conversation on stdout (You:/Agent: labels).\n\n"
        "Use --help for session commands (/quit, /steer), HITL, and examples.\n",
    ),
    "tui": (
        "tui",
        "tui_cmd",
        "Curses chat UI — manifest, overlays, infra, and HITL parity with mas-ctl chat.",
    ),
    "compile": (
        "compile",
        "compile_cmd",
        "Patch any registered manifest; resolve overlays and defaults for Agent/MAS.\n\n"
        "Unlike ``compose`` (EffectiveBind + placement), this dumps the YAML dict\n"
        "the runtime holds after overlay merge. Default ``--layout auto`` writes a\n"
        "single file (or stdout) for one agent / ``--layout bundle``, and a folder\n"
        "of ``mas.yaml`` + ``agents/`` when ``--output`` is a directory.\n",
    ),
    "compose": (
        "compose",
        "compose_cmd",
        "Compose MAS + deployment into effective bind and placement plan.",
    ),
    "plan": ("compose", "plan_cmd", "Emit placement plan only (compose subset)."),
    "run-mas": (
        "run_mas",
        "run_mas_cmd",
        "Run a MAS manifest (compose → materialize → session on entry agent).",
    ),
    "list-bundles": (
        "bundles",
        "list_bundles_cmd",
        "List infra bundles from mas.runtime.manifest_libraries entry points.",
    ),
    "checkpoint": ("checkpoint", "checkpoint_group", "Session checkpoint files."),
    "control": (
        "control",
        "control_group",
        "Attach to ControlContract using the session id (A2A contextId).",
    ),
    "hardening": (
        "hardening",
        "hardening_group",
        "Named hardening checks and the production posture.",
    ),
    "identity": (
        "identity",
        "identity_group",
        "The identities a session would use: check them, list them, describe one.",
    ),
    "validate": (
        "validate",
        "validate_cmd",
        "Validate YAML manifests against docs/schemas (v1 + v2 kinds).",
    ),
    "schemas": ("validate", "schemas_cmd", "List registered schema kinds."),
    "registry": (
        "registry",
        "registry_group",
        "List/query runtime machines, ctl registries, and schema kinds.",
    ),
    "serve": (
        "serve",
        "serve_agent_cmd",
        "Serve an agent through the agent_expose plugin selected by its infra endpoint.",
    ),
    "plugin": (
        "plugin",
        "plugin_group",
        "Inspect and manage runtime plugin availability (library.yaml plugins:).",
    ),
    "infra": ("workspace", "infra_group", "Infrastructure bundle discovery."),
    "flavour": ("workspace", "flavour_group", "Flavour manifests."),
    "secrets": (
        "secrets",
        "secrets_group",
        "File-store secrets. list prints names and presence only.",
    ),
}


class _LazyGroup(click.Group):
    """The ``mas-ctl`` group: its commands are imported when one is looked up for a run, not at start."""

    def list_commands(self, ctx: click.Context) -> list[str]:
        return sorted({*LAZY_COMMANDS, *self.commands})

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        spec = LAZY_COMMANDS.get(cmd_name)
        if spec is None:
            return super().get_command(ctx, cmd_name)  # a command added with ``add_command``
        module, attribute, _help = spec
        command: Any = getattr(importlib.import_module(f"mas.ctl.cli.commands.{module}"), attribute)
        return command

    def format_commands(self, ctx: click.Context, formatter: HelpFormatter) -> None:
        names = self.list_commands(ctx)
        limit = formatter.width - 6 - measure_table([(name, "") for name in names])[0]
        rows = []
        for name in names:
            spec = LAZY_COMMANDS.get(name)
            command = click.Command(name, help=spec[2]) if spec else self.commands[name]
            if not command.hidden:
                rows.append((name, command.get_short_help_str(limit)))
        with formatter.section("Commands"):
            formatter.write_dl(rows)


@click.group(cls=_LazyGroup)
@click.option("-v", "--verbose", count=True, default=0)
@click.option(
    "--env",
    "env_file",
    default=None,
    type=click.Path(exists=True),
    help=".env file (import with secrets import-env; not exported)",
)
@click.option(
    "--secrets-dir",
    "secrets_dir",
    default=None,
    type=click.Path(file_okay=False),
    help="File-store directory (default <workspace>/.mas/secrets)",
)
@click.option("--secret-store", "secret_store", default=None, help="file (default) or env")
@click.pass_context
def app(
    ctx: click.Context,
    verbose: int,
    env_file: str | None,
    secrets_dir: str | None,
    secret_store: str | None,
) -> None:
    """MAS ctl — compose, placement, and conversation UI for the Mealy runtime."""
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose
    ctx.obj["secrets_dir"] = secrets_dir
    ctx.obj["secret_store"] = secret_store
    from mas.ctl.session.llm_scheduler_env import install_env_overrides

    # The host first (hostenv, then the scrub), then the .env file on top of it: its MAS_LLM_*
    # values reach the overrides instead of being replaced by a later read of the bare host.
    install_env_overrides()
    load_dotenv(explicit=Path(env_file) if env_file else None)
    install_process_resolver(directory=secrets_dir, store=secret_store)
    setup_logging(verbosity=verbose)
    set_run_progress(verbose)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
