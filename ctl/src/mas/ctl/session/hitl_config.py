#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Resolve HITL adapters from declarative manifest governance (not CLI flags).

Architecture
------------
- **Kernel** emits ``EmitHitlRequest`` from ``M_gov`` egress gate only.
- **Interactive sessions** terminate HITL at the ctl surface (``OperatorConsole`` /
  curses TUI) — no in-process ``HitlTerminal`` on the controller.
- **Batch / pipe** sessions wire an in-process ``HitlResponder``: the one ``hitl_mode`` names,
  or auto-deny when it is unset (a hold is never approved without an explicit auto-approve).
  ``spec.governance.hitl_responder`` is the mapping handed to that responder.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from mas.ctl.manifest.spec_bindings import parse_governance
from mas.runtime.boundary.hitl.responders import HitlResponder
from mas.runtime.registry import get_registry
from mas.runtime.secrets import prepare_manifest
from mas.runtime.spec.gov import _constructor_kwargs


def _load_hitl_responder(mode: str, params: dict[str, Any] | None = None) -> HitlResponder | None:
    key = (mode or "").strip().lower()
    if not key or key == "interactive":
        return None
    variant = get_registry().resolve_by_type("hitl_responder", key)
    if variant is None:
        names = get_registry().list_names("hitl_responder")
        raise ValueError(
            f"unsupported spec.governance.hitl_mode: {mode!r}; expected one of {sorted([*names, 'interactive'])}"
        )
    cls = variant.load_class()
    if not params:
        return cls()
    resolved = prepare_manifest(dict(params))
    kwargs = _constructor_kwargs(cls, resolved)
    unknown = sorted(str(k) for k in set(resolved) - set(kwargs))
    if unknown:
        raise ValueError(f"spec.governance.hitl_responder: {key!r} does not accept {unknown}")
    return cls(**kwargs)


def resolve_hitl_from_manifest(
    manifest: dict[str, Any] | None,
    *,
    session_interactive: bool = False,
    flavour_spec: dict[str, Any] | None = None,
    agent_id: str = "",
    entry_id: str = "",
    delegate_ids: list[str] | tuple[str, ...] = (),
    groups: Mapping[str, Sequence[str]] | None = None,
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
        groups=groups,
    )
    gov = parse_governance(spec.get("governance"))
    holds_possible = bool(
        gov.plugins or gov.hitl_on_tool or gov.hitl_on_tool_result or gov.policies or gov.gov_policy_profile
    )
    if not holds_possible:
        return None, None

    mode = (gov.hitl_mode or "interactive").strip().lower()
    if mode == "interactive":
        if session_interactive:
            return None, None
        # No operator can answer: a hold is refused, never approved. Approval is
        # only ever an explicit ``hitl_mode: auto-approve``.
        return _load_hitl_responder("auto-deny"), None
    return _load_hitl_responder(mode, gov.hitl_responder), None
