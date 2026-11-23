#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The loggers that print request or response headers stay quiet at every verbosity."""

from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest
from mas.ctl.logging_setup import setup_logging

_NAMES = ("a2a", "httpcore", "httpcore2", "httpx", "httpx2", "h2", "hpack", "openai", "anthropic")


@pytest.fixture(autouse=True)
def _restore_levels() -> Iterator[None]:
    root = logging.getLogger()
    saved = {name: logging.getLogger(name).level for name in _NAMES}
    root_level, handlers = root.level, list(root.handlers)
    yield
    for name, level in saved.items():
        logging.getLogger(name).setLevel(level)
    root.setLevel(root_level)
    root.handlers[:] = handlers


@pytest.mark.parametrize("kwargs", [{"verbosity": 3}, {"log_level": "debug"}])
def test_header_loggers_are_capped_at_full_debug(kwargs: dict) -> None:
    # kills: -vvv / LOG_LEVEL=debug letting a2a-sdk log the call context (a bearer) or httpcore response headers
    for name in _NAMES:
        logging.getLogger(name).setLevel(logging.NOTSET)
    setup_logging(**kwargs)
    assert not logging.getLogger("a2a.server.agent_execution.active_task").isEnabledFor(logging.DEBUG)
    assert not logging.getLogger("httpcore.http11").isEnabledFor(logging.DEBUG)
    assert not logging.getLogger("httpcore").isEnabledFor(logging.INFO)
    assert logging.getLogger("a2a").isEnabledFor(logging.INFO)
    # the http stack of the MCP 2.x client prints headers at debug
    for name in ("httpcore2.http11", "httpx2", "h2.connection", "hpack.hpack"):
        assert logging.getLogger(name).getEffectiveLevel() == logging.WARNING, name
