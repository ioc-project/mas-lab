#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""An engine that can try a tool outside the model's turn, so a layer can tell whether a tool is back."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class ToolProber:
    """Base of the call body that can run a tool on its own. Raises when the tool fails."""

    async def aprobe_tool(self, name: str, arguments: Mapping[str, Any]) -> None:
        raise NotImplementedError(f"{type(self).__name__} cannot probe tools")


class PeerProber:
    """Base of the call body that can check a peer on its own."""

    async def aprobe_peer(self, name: str) -> bool | None:
        """Raise when the peer does not answer; ``True`` when it does; ``None`` when its link cannot be probed."""
        raise NotImplementedError(f"{type(self).__name__} cannot probe peers")


def peer_prober_for(inner: Any) -> PeerProber | None:
    """The :class:`PeerProber` at the bottom of the call chain *inner*, or ``None``."""
    from mas.runtime.engine.infra_pipeline import innermost_call

    source = innermost_call(inner)
    return source if isinstance(source, PeerProber) else None


def tool_prober_for(inner: Any) -> ToolProber | None:
    """The :class:`ToolProber` at the bottom of the call chain *inner*, or ``None``."""
    from mas.runtime.engine.infra_pipeline import innermost_call

    source = innermost_call(inner)
    return source if isinstance(source, ToolProber) else None
