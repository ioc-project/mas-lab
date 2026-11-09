#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Validate CLI session flags against manifest-derived session requirements."""

from __future__ import annotations

import sys
from typing import Any

import click


def validate_chat_session(
    *,
    interactive: bool,
    single_turn: bool,
    scripted_turns: list[str],
    manifest: dict[str, Any] | None,
    is_tty: bool | None = None,
    flavour_spec: dict[str, Any] | None = None,
    agent_id: str = "",
    entry_id: str = "",
    delegate_ids: list[str] | tuple[str, ...] = (),
) -> None:
    """Reject inconsistent flag combinations before blocking on stdin."""
    tty = sys.stdin.isatty() if is_tty is None else is_tty
    hitl_mode = _hitl_mode(
        manifest,
        flavour_spec=flavour_spec,
        agent_id=agent_id,
        entry_id=entry_id,
        delegate_ids=delegate_ids,
    )

    if interactive and not tty and not scripted_turns:
        raise click.UsageError("-i/--interactive requires a TTY, or provide input via -q/-p or stdin pipe.")

    if hitl_mode == "interactive" and not tty and not scripted_turns:
        raise click.UsageError(
            "spec.governance.hitl_mode is 'interactive' but stdin is not a TTY. "
            "Use hitl_mode: auto-approve in the Control for CI/batch runs, "
            "or pass -q with a scripted query."
        )

    if hitl_mode == "interactive" and single_turn and scripted_turns and not tty:
        raise click.UsageError(
            "Non-interactive single-turn run with hitl_mode: interactive will block "
            "without an operator. Set hitl_mode: auto-approve in the Control."
        )


def _hitl_mode(
    manifest: dict[str, Any] | None,
    *,
    flavour_spec: dict[str, Any] | None = None,
    agent_id: str = "",
    entry_id: str = "",
    delegate_ids: list[str] | tuple[str, ...] = (),
) -> str | None:
    from mas.ctl.manifest.spec_bindings import parse_governance
    from mas.ctl.session.manifest_config import agent_id_from_manifest, spec_with_control_lists

    who = agent_id or agent_id_from_manifest(manifest)
    spec = spec_with_control_lists(
        (manifest or {}).get("spec") or {},
        flavour_spec,
        agent_id=who,
        entry_id=entry_id or who,
        delegate_ids=delegate_ids,
    )
    gov = parse_governance(spec.get("governance"))
    if not gov.hitl_on_tool and not gov.hitl_on_tool_result:
        return None
    mode = gov.hitl_mode
    return str(mode).strip().lower() if mode else "interactive"
