#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""ContextManagerContract — conversation-history strategies for M_ctx.

Plugin authors: see runtime/docs/dev/contracts/state-and-context.md
(ContextManagerContract). The rules below are the payload contract the
provider enforces; this ABC is the trim/summarize hook that must not break them.

Two layers (do not confuse them):

1. **Protocol** — the HTTP body is a typed ``messages[]`` list (roles, tool
   call ids, tool result ids). The provider walks that structure *before*
   tokenization. It does not parse tool-result *text*. After an assistant
   message that declares tools A and B, the next messages must be the result
   of A and the result of B (those ids, that count, nothing in between).
2. **Tokens** — only after that check does a chat template flatten the same
   objects into a token stream. Then it is next-token prediction.

``manage_history`` returns layer-1 objects. Keep each user turn intact (user
plus everything until the next user). Never split a tool ask from its results.
Do not summarize the live tool round (that lives in working memory, after
this method).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ContextManagerContract(ABC):
    """Trim or compress committed history before each LLM call.

    Input ``past`` is committed turns only (not the current user message, not
    in-turn working memory). Output must remain a valid provider ``messages[]``
    prefix under the protocol rules in this module's docstring.
    """

    @abstractmethod
    def manage_history(
        self,
        past: list[dict[str, Any]],
        budget_tokens: int,
    ) -> list[dict[str, Any]]:
        """Return a possibly shorter version of *past* (excludes current user turn).

        A strategy that waits on I/O (summarising with an LLM) is ``async def``; the assembler awaits it.
        """
