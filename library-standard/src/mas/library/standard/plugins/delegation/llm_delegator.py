#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Compatibility alias: the send tool is the default delegation plugin."""

from mas.library.standard.plugins.system_tools.send import SendTool as LlmDelegator

__all__ = ["LlmDelegator"]
