#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Errors a layer or a stack raises."""

from __future__ import annotations

from mas.runtime.boundary.agentcomm.protocol import AgentCommError


class StackError(RuntimeError):
    """A layer could not complete a send, or a stack could not be built."""


class StackSendError(AgentCommError, StackError):
    """A ``StackError`` as the agent-call contract reports it.

    ``agent_comm/stack`` raises it from ``send`` / ``asend`` so a caller that
    handles ``AgentCommError`` (the send tool) handles a layer failure too.
    The layer's own error is the ``__cause__``.
    """
