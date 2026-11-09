#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The Flavour's ``bind`` for a one-agent command (``chat``, ``tui``): apply it, open the chain, close it.

Both commands bind the same way and link their agent the same way; a bad bind or a
chain that cannot start is one ``error:`` line and exit status 1.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import click
from mas.ctl.compose.models import ResolvedInfra
from mas.ctl.executor.mas_session import open_single_agent_chains
from mas.ctl.executor.stacks import ChainHandles, ChainStartError
from mas.ctl.infra.bind import BindError, bind_session
from mas.ctl.session.flavour_control import ControlError


def bind_or_exit(
    resolved: ResolvedInfra | None,
    flavour_spec: dict[str, Any] | None,
    *,
    agent_id: str,
) -> ResolvedInfra:
    """``resolved`` with the Flavour applied to the one agent; a bad ``bind`` exits with one line."""
    try:
        return bind_session(resolved, flavour_spec, agent_ids=[agent_id])
    except (BindError, ControlError) as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from None


@contextmanager
def single_agent_chains(
    instance: Any, agent_id: str, resolved: ResolvedInfra | None, *, display: Any = None, verbose: int = 0
) -> Iterator[ChainHandles]:
    """Link ``agent_id`` on the Flavour's chain for the block, and close the chain however the block ends."""
    try:
        chains = open_single_agent_chains(instance, agent_id, resolved, display=display, verbose=verbose)
    except ChainStartError as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from None
    try:
        yield chains
    finally:
        chains.close()
