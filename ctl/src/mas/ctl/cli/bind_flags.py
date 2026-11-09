# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
"""Shared ``--connect`` / ``--listen`` / ``--override`` wiring for live ctl commands."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

import click
from mas.ctl.overrides.bind import combine_overrides

BIND_ALIAS_WARNING = "warning: --bind is deprecated; use --connect"


def _warn_bind_alias(
    ctx: click.Context,
    param: click.Parameter,
    value: tuple[str, ...],
) -> tuple[str, ...]:
    if value:
        click.echo(BIND_ALIAS_WARNING, err=True)
    return value


connect_option = click.option(
    "--connect",
    "connects",
    multiple=True,
    metavar="NAME=URI",
    help=(
        "Connect to a remote peer or tool (infra usage: connect): "
        "agent=a2a://host:port/path or tool=mcp://host:port/mcp#name. "
        "Expands to --override infra:…; same merge path as --infra-ref YAML."
    ),
)

bind_option = click.option(
    "--bind",
    "binds",
    multiple=True,
    metavar="NAME=URI",
    hidden=True,
    callback=_warn_bind_alias,
    help="Old name for --connect.",
)

control_option = click.option(
    "--control",
    "controls",
    multiple=True,
    metavar="REF[@SELECTOR]",
    help=(
        "Attach a kind: Control file (observability set and governance set) to the session, the way a "
        "Flavour spec.control row does. REF is a path, pkg://… or a library ref. SELECTOR is $all (default), "
        "$entry, $not-entry, $delegates, agent ids, or the same agent path as --override "
        "(.mas.agent[id] / .mas.agent[*])."
    ),
)

events_sink_option = click.option(
    "--events-sink",
    "event_sinks",
    multiple=True,
    metavar="ROW",
    help=(
        "Send events to this infra Application row (adds it to the Flavour's bind.events). "
        "Adds that sink; native events.jsonl still records unless a Control named the "
        "observability list (including []) or leftover agent observability named plugins, "
        "or --no-events / --events-format replaced the set."
    ),
)

no_control_dir_option = click.option(
    "--no-control-dir",
    "--no-control",
    "no_control",
    is_flag=True,
    help=(
        "Do not advertise a control endpoint (mas-ctl control attach). "
        "This is the pair of --control-dir, not of --control."
    ),
)

middleware_option = click.option(
    "--middleware",
    "middleware_refs",
    multiple=True,
    help=("Infra middleware ref (repeatable). Merged after workspace middleware / infra_interceptors."),
)


listen_option = click.option(
    "--listen",
    "listens",
    multiple=True,
    metavar="NAME=URI",
    help=(
        "Listen on a well-known name (infra usage: listen): serve the agent "
        "NAME at a2a://host:port/path. The mirror of --connect (which connects to a "
        "remote peer). Expands to --override infra:…; same merge path as "
        "--infra-ref YAML."
    ),
)


def with_connects(
    connects: Sequence[str] | Iterable[str] = (),
    overrides: Sequence[str] | Iterable[str] = (),
    listens: Sequence[str] | Iterable[str] = (),
    binds: Sequence[str] | Iterable[str] = (),
) -> tuple[str, ...]:
    """Expand ``--connect`` and ``--listen`` then append explicit ``--override`` values.

    ``binds`` is the hidden ``--bind`` dest: those assignments follow ``connects``.
    Bad ``NAME=URI`` values become a Click usage error instead of an uncaught
    ``ValueError``.
    """
    routes = tuple(connects) + tuple(binds)
    try:
        return combine_overrides(
            connects=routes,
            overrides=overrides,
            listens=listens,
        )
    except ValueError as exc:
        if tuple(connects):
            hint = "--connect"
        elif tuple(binds):
            hint = "--bind"
        elif tuple(listens):
            hint = "--listen"
        else:
            hint = "--connect"
        raise click.BadParameter(str(exc), param_hint=hint) from exc
