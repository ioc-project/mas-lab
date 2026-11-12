#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Generic layers and stacks for agent-to-agent calls."""

from mas.library.standard.lib.stack.call import CallLayer, LayerLifecycle
from mas.library.standard.lib.stack.core import Stack
from mas.library.standard.lib.stack.envelope import Envelope
from mas.library.standard.lib.stack.errors import StackError, StackSendError
from mas.library.standard.lib.stack.handle import StackHandle
from mas.library.standard.lib.stack.layer import Handler, Layer

__all__ = [
    "CallLayer",
    "LayerLifecycle",
    "Envelope",
    "Handler",
    "Layer",
    "Stack",
    "StackError",
    "StackHandle",
    "StackSendError",
]
