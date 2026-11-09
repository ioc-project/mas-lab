#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Resolve HITL adapters from declarative manifest governance (not CLI flags).

Architecture
------------
- **Kernel** emits ``EmitHitlRequest`` from ``M_gov`` egress gate only.
- **Interactive sessions** terminate HITL at the ctl surface (``OperatorConsole`` /
  curses TUI) — no in-process ``HitlTerminal`` on the controller.
- **Batch / pipe** sessions wire an in-process ``HitlResponder`` (auto-approve, etc.).
"""

from __future__ import annotations

from typing import Any

from mas.ctl.manifest.spec_bindings import parse_governance
from mas.runtime.boundary.hitl.responders import HitlResponder
from mas.runtime.registry import get_registry


def _load_hitl_responder(mode: str) -> HitlResponder | None:
    key = (mode or "").strip().lower()
    if not key or key == "interactive":
        return None
    variant = get_registry().resolve_by_type("hitl_responder", key)
    if variant is None:
        names = get_registry().list_names("hitl_responder")
        raise ValueError(
            f"unsupported spec.governance.hitl_mode: {mode!r}; expected one of {sorted([*names, 'interactive'])}"
        )
    return variant.load_class()()


def resolve_hitl_from_manifest(
    manifest: dict[str, Any] | None,
    *,
    session_interactive: bool = False,
    flavour_spec: dict[str, Any] | None = None,
    agent_id: str = "",
    entry_id: str = "",
    delegate_ids: list[str] | tuple[str, ...] = (),
) -> tuple[Any | None, Any | None]:
    """Return (hitl_responder, hitl_terminal) for SessionController / RuntimeBuilder.

    ``hitl_terminal`` is always ``None`` — interactive HITL is owned by ctl surfaces.
    """
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
        return None, None

    mode = (getattr(gov, "hitl_mode", None) or "interactive").strip().lower()
    if mode == "interactive" and not session_interactive:
        return _load_hitl_responder("auto-approve"), None
    return _load_hitl_responder(mode), None
