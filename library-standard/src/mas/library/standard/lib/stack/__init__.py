#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Generic layers and stacks for agent-to-agent calls."""

from mas.library.standard.lib.stack.call import AuthorizableTransport, CallLayer, LayerLifecycle
from mas.library.standard.lib.stack.core import Stack
from mas.library.standard.lib.stack.envelope import Envelope
from mas.library.standard.lib.stack.errors import PeerChallenge, StackError, StackSendError, Unauthenticated
from mas.library.standard.lib.stack.handle import StackHandle
from mas.library.standard.lib.stack.layer import Handler, Layer
from mas.library.standard.lib.stack.owner import conversation_id
from mas.library.standard.lib.stack.session import SESSION_HOOK_META, SESSION_TOKEN, SessionHook, SessionInfo

__all__ = [
    "conversation_id",
    "AuthorizableTransport",
    "CallLayer",
    "PeerChallenge",
    "Unauthenticated",
    "SESSION_HOOK_META",
    "SESSION_TOKEN",
    "SessionHook",
    "SessionInfo",
    "LayerLifecycle",
    "Envelope",
    "Handler",
    "Layer",
    "Stack",
    "StackError",
    "StackHandle",
    "StackSendError",
]
