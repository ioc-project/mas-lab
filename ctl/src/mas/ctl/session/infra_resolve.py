#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Shared infra-ref resolution for chat / tui session bootstrap.

Both ``mas-ctl chat`` and ``mas-ctl tui`` merge infra references from the MAS
manifest, the workspace, the user default, and CLI flags, then resolve them to
concrete bundles. This helper centralises that logic so the two commands cannot
drift apart.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def resolve_session_infra(
    agent_data: dict | None,
    workspace: Any,
    user: Any,
    *,
    infra_refs_cli: tuple[str, ...] | list[str],
    overrides: tuple[str, ...] | list[str] = (),
    anchor: Path | str,
    with_interceptors: bool = False,
    runtime_refs_cli: tuple[str, ...] | list[str] = (),
    middleware_refs: tuple[str, ...] | list[str] = (),
) -> Any:
    """Merge MAS/workspace/user/CLI infra refs and resolve them to bundles.

    When *with_interceptors* is true (the ``chat`` path) infra interceptors and
    the MAS config are also threaded through; the ``tui`` path omits them.
    """
    from mas.ctl.infra.resolve import resolve_infra_refs
    from mas.ctl.workspace.config import (
        merge_infra_interceptors,
        merge_infra_refs,
    )

    merged = merge_infra_refs(
        workspace_refs=workspace.effective_infra_refs,
        user_refs=[user.default_infra] if user.default_infra else [],
        cli_refs=list(infra_refs_cli),
        workspace_found=workspace.found,
    )
    kwargs: dict[str, Any] = {"anchor": anchor, "workspace": workspace, "user": user}
    cli_middleware = list(middleware_refs)
    if with_interceptors or cli_middleware:
        kwargs["interceptors"] = merge_infra_interceptors(
            workspace_interceptors=list(workspace.infra_interceptors) if with_interceptors else [],
            cli_interceptors=cli_middleware,
        )
    kwargs["runtime_refs"] = list(runtime_refs_cli)
    from mas.ctl.overrides.parser import parse_override

    kwargs["overrides"] = [
        source for source in overrides if parse_override(source).path.root == "infra"
    ]
    return resolve_infra_refs(merged, **kwargs)
