#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Logging setup — parity with v1 mas-runtime verbosity flags."""

from __future__ import annotations

import logging

from mas.runtime import hostenv

# Loggers whose debug output carries request or response headers (a caller's bearer in the a2a-sdk call
# context, a token a server echoes in WWW-Authenticate in httpcore and httpcore2, the frames of h2 and hpack,
# request options in the vendor SDKs). They stay at this level whatever the verbosity.
_HEADER_LOGGERS = {
    "a2a": logging.INFO,
    "httpcore": logging.WARNING,
    "httpcore2": logging.WARNING,
    "httpx2": logging.WARNING,
    "h2": logging.WARNING,
    "hpack": logging.WARNING,
    "openai": logging.INFO,
    "anthropic": logging.INFO,
}
_QUIET_LOGGERS = ("httpx", "httpx2", "openai", "httpcore", "httpcore2", "h2", "hpack")


def _cap_header_loggers() -> None:
    for name, floor in _HEADER_LOGGERS.items():
        logger = logging.getLogger(name)
        if logger.level == logging.NOTSET or logger.level < floor:
            logger.setLevel(floor)


def setup_logging(log_level: str | None = None, verbosity: int = 0) -> None:
    """Configure logging from ``-v`` count or explicit level.

    * ``0`` — quiet (WARNING for mas.runtime / mas.ctl)
    * ``1`` (``-v``) — exchange log at INFO
    * ``2`` (``-vv``) — DEBUG for mas.runtime
    * ``3`` (``-vvv``) — DEBUG for all loggers
    """
    env_level = hostenv.get("LOG_LEVEL")
    if log_level or env_level:
        level_str = log_level or env_level
        log_level_attr = getattr(logging, str(level_str).upper(), logging.INFO)
        _apply_root_logging(log_level_attr)
        _cap_header_loggers()
        return

    log_format = hostenv.get("LOG_FORMAT", "simple")
    format_str = (
        "[%(asctime)s] %(levelname)s [%(name)s:%(lineno)s] %(message)s" if log_format == "full" else "%(message)s"
    )

    if verbosity >= 3:
        root_level = logging.DEBUG
        rt_level = logging.DEBUG
    elif verbosity == 2:
        root_level = logging.WARNING
        rt_level = logging.DEBUG
    elif verbosity == 1:
        root_level = logging.WARNING
        rt_level = logging.INFO
    else:
        root_level = logging.WARNING
        rt_level = logging.WARNING

    logging.basicConfig(level=root_level, format=format_str, force=True)
    for name in ("mas.runtime", "mas.ctl", "mas.runtime", "mas.ctl"):
        logging.getLogger(name).setLevel(rt_level)

    if verbosity < 3:
        _quiet_loggers()
    _cap_header_loggers()


def _apply_root_logging(level: int) -> None:
    log_format = hostenv.get("LOG_FORMAT", "simple")
    format_str = (
        "[%(asctime)s] %(levelname)s [%(name)s:%(lineno)s] %(message)s" if log_format == "full" else "%(message)s"
    )
    logging.basicConfig(level=level, format=format_str, force=True)
    _quiet_loggers()


def _quiet_loggers() -> None:
    logging.getLogger("sqlalchemy").setLevel(logging.ERROR)
    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
