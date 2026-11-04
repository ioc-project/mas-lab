#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""CLI entry point — delegates to the Click-based implementation in ``mas.lab.cli``.

The ``mas-lab`` console script calls ``app()`` from this module.
All sub-commands are defined in :mod:`mas.lab.cli.commands`.
"""
import mas.third_party_otel  # noqa: F401
from mas.lab.cli import app

__all__ = ["app"]
