#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""MAS ctl — compose, placement, session UI, protocol adapters (not the Mealy kernel)."""

from typing import TYPE_CHECKING

from mas.runtime.lazy import lazy_exports

# Loaded on first use: importing any ``mas.ctl.*`` submodule runs this file, and the session controller pulls in
# the kernel (about 0.4 s), which commands that never run a session do not need.
_EXPORTS = {name: "mas.ctl.session.controller" for name in ("ConversationConfig", "SessionController", "TurnResult")}

if TYPE_CHECKING:
    from mas.ctl.session.controller import ConversationConfig, SessionController, TurnResult

__all__ = [
    "ConversationConfig",
    "SessionController",
    "TurnResult",
]


__getattr__, __dir__ = lazy_exports(__name__, globals(), _EXPORTS)
