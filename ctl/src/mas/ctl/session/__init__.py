#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Ctl session control plane."""

from typing import TYPE_CHECKING

from mas.runtime.lazy import lazy_exports

# Loaded on first use, like ``mas.ctl``: the controller imports the kernel.
_EXPORTS = {
    **{
        name: "mas.ctl.session.controller"
        for name in ("ConversationConfig", "SessionController", "TurnResult", "close_observability", "run_session_loop")
    },
    "SessionManager": "mas.ctl.session.manager",
}

if TYPE_CHECKING:
    from mas.ctl.session.controller import (
        ConversationConfig,
        SessionController,
        TurnResult,
        close_observability,
        run_session_loop,
    )
    from mas.ctl.session.manager import SessionManager

__all__ = [
    "ConversationConfig",
    "SessionController",
    "TurnResult",
    "close_observability",
    "run_session_loop",
    "SessionManager",
]


__getattr__, __dir__ = lazy_exports(__name__, globals(), _EXPORTS)
