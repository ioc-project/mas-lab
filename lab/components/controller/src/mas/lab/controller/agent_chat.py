#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""In-process agent chat for the controller UI.

Runs agents via v2 ``SessionController`` (no subprocess / stdout capture) and
returns structured interaction fields: user query in, agent response or error out.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

import yaml

logger = logging.getLogger(__name__)

_STATUS = Literal["ok", "error"]


@dataclass(frozen=True)
class AgentTurnResult:
    status: _STATUS
    response: str = ""
    error_message: str = ""
    error_detail: str = ""
    session_id: str = ""


def format_error(raw: str) -> tuple[str, str]:
    """Return (short_message, full_detail) from a raw agent/LLM error string."""
    full = (raw or "").strip()
    if not full:
        return "Agent execution failed.", ""

    short = full

    msg_match = re.search(r"['\"]message['\"]\s*:\s*['\"]([^'\"]+)['\"]", full)
    if msg_match:
        short = msg_match.group(1)
    elif "ExceededBudget" in full:
        budget_match = re.search(r"(ExceededBudget:[^\n'\"]+)", full)
        if budget_match:
            short = budget_match.group(1).strip()

    if len(short) > 240:
        short = short[:237] + "..."

    return short, full


class _SilentDisplay:
    """No-op conversation display for embedded UI turns."""

    def on_user(self, text: str, *, turn_id: str = "") -> None:
        return

    def on_agent(self, text: str) -> None:
        return

    def on_turn_error(self, message: str, *, detail: str = "") -> None:
        return

    def on_hitl_request(self, request: object) -> None:
        return

    def on_working(self, message: str = "Agent working…") -> None:
        return

    def end_working(self) -> None:
        return

    def on_system(self, message: str) -> None:
        return

    def on_error(self, message: str) -> None:
        return


def _load_workspace_dotenv(base_dir: Path) -> None:
    """Load optional .env files; container env from docker/.env still wins (override=False)."""
    import os

    try:
        from dotenv import load_dotenv
    except ImportError:
        return

    candidates = [
        Path("/workspace/.env"),
        base_dir / ".env",
        Path(os.environ.get("MAS_WORKSPACE_ROOT", "")) / ".env",
    ]
    for path in candidates:
        if path.is_file():
            load_dotenv(path, override=False)


def _quiet_logging_for_chat() -> None:
    """Suppress exchange logs; UI chat must not capture CLI-style stdout."""
    import os

    from mas.ctl.logging_setup import setup_logging

    os.environ.pop("LOG_LEVEL", None)
    setup_logging(verbosity=0)
    logging.getLogger("mas.runtime").setLevel(logging.WARNING)
    logging.getLogger("mas.ctl").setLevel(logging.WARNING)


def run_agent_turn(
    manifest_yaml: str,
    query: str,
    *,
    base_dir: Path,
    flavour: Optional[str] = None,
    session_id: Optional[str] = None,
) -> AgentTurnResult:
    """Execute one user → agent turn in-process via v2 SessionController."""
    from mas.ctl.infra.resolve import resolve_infra_refs
    from mas.ctl.runtime_cli import pattern_from_manifest
    from mas.ctl.session.bootstrap import InstantiationOptions, instantiate_runtime
    from mas.ctl.session.controller import ConversationConfig, SessionController
    from mas.ctl.session.hitl_config import resolve_hitl_from_manifest
    from mas.ctl.ui.turn_result import turn_to_agent_result
    from mas.ctl.workspace.config import (
        UserConfig,
        WorkspaceConfig,
        merge_infra_interceptors,
        merge_infra_refs,
    )

    _quiet_logging_for_chat()
    _load_workspace_dotenv(base_dir)

    base_dir = Path(base_dir)
    sid = session_id or f"ui:{uuid.uuid4()}"
    prompt = (query or "").strip()
    if not prompt:
        return AgentTurnResult(
            status="error",
            error_message="No query provided.",
            session_id=sid,
        )

    try:
        current_manifest = yaml.safe_load(manifest_yaml)
    except yaml.YAMLError as exc:
        return AgentTurnResult(
            status="error",
            error_message=f"Invalid manifest YAML: {exc}",
            error_detail=str(exc),
            session_id=sid,
        )

    if not isinstance(current_manifest, dict):
        return AgentTurnResult(
            status="error",
            error_message="Manifest must be a YAML mapping.",
            session_id=sid,
        )

    try:
        workspace = WorkspaceConfig.load(base_dir)
        user = UserConfig.load()
        pattern = pattern_from_manifest(current_manifest)
        merged_infra = merge_infra_refs(
            workspace_refs=workspace.effective_infra_refs,
            user_refs=[user.default_infra] if user.default_infra else [],
            cli_refs=[],
            workspace_found=workspace.found,
        )
        resolved_infra = resolve_infra_refs(
            merged_infra,
            anchor=base_dir,
            workspace=workspace,
            user=user,
            interceptors=merge_infra_interceptors(
                workspace_interceptors=workspace.infra_interceptors,
                cli_interceptors=[],
            ),
        )
        flavour_spec = None
        if flavour:
            from mas.ctl.session.flavour import resolve_session_flavour

            flavour_spec = resolve_session_flavour(flavour, search_dirs=[base_dir])
        hitl_responder, hitl_terminal = resolve_hitl_from_manifest(
            current_manifest, session_interactive=False, flavour_spec=flavour_spec
        )
        instance, _store = instantiate_runtime(
            InstantiationOptions(
                pattern_plugin_id=pattern,
                agent_manifest=current_manifest,
                manifest_dir=base_dir,
                resolved_infra=resolved_infra,
                workspace=workspace,
                validate_manifests=False,
                flavour_spec=flavour_spec,
            ),
            hitl=hitl_responder,
        )
        controller = SessionController(
            instance=instance,
            display=_SilentDisplay(),
            hitl_terminal=hitl_terminal,
            config=ConversationConfig(single_turn=True),
            verbose=0,
            session_id=sid,
        )
        turn = controller.run_turn(prompt, turn_id=sid, auto_hitl=True)
        mapped = turn_to_agent_result(turn)
    except Exception as exc:
        logger.debug("Agent session failed", exc_info=True)
        short, full = format_error(str(exc))
        return AgentTurnResult(
            status="error",
            error_message=short,
            error_detail=full,
            session_id=sid,
        )

    return AgentTurnResult(
        status=mapped.status,
        response=mapped.response,
        error_message=mapped.error_message,
        error_detail=mapped.error_detail,
        session_id=sid,
    )
