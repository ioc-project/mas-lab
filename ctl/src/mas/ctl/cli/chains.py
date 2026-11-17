#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The Flavour's ``bind`` for a one-agent command (``chat``, ``tui``): apply it, open the chain, close it.

Both commands bind the same way and link their agent the same way; a bad bind or a
chain that cannot start is one ``error:`` line and exit status 1.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import click
from mas.ctl.compose.models import ResolvedInfra
from mas.ctl.executor.mas_session import open_single_agent_chains, standalone_peer_ids
from mas.ctl.executor.stacks import ChainHandles, ChainStartError
from mas.ctl.infra.bind import BindError, bind_session
from mas.ctl.session.flavour_control import ControlError


def bind_or_exit(
    resolved: ResolvedInfra | None,
    flavour_spec: dict[str, Any] | None,
    *,
    agent_id: str,
    manifest: dict[str, Any] | None = None,
) -> ResolvedInfra:
    """``resolved`` with the Flavour applied to the agent and to the peers its own spec declares.

    A bad ``bind`` exits with one line.
    """
    try:
        peers = standalone_peer_ids(manifest, agent_id)
        return bind_session(resolved, flavour_spec, agent_ids=[agent_id, *peers])
    except (BindError, ControlError) as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from None


@contextmanager
def single_agent_chains(
    instance: Any,
    agent_id: str,
    resolved: ResolvedInfra | None,
    *,
    display: Any = None,
    verbose: int = 0,
    manifest: dict[str, Any] | None = None,
    manifest_dir: Path | None = None,
) -> Iterator[ChainHandles]:
    """Link ``agent_id`` and its declared peers on the Flavour's chains for the block, and close them after."""
    try:
        chains = open_single_agent_chains(
            instance, agent_id, resolved, display=display, verbose=verbose, manifest=manifest, manifest_dir=manifest_dir
        )
    except ChainStartError as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from None
    try:
        yield chains
    finally:
        chains.close()
