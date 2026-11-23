#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Pytest plugin for lab test trees: a ``mas-lab`` command run in-process leaves no secret store behind.

The CLI installs its store and its settings for the whole process; each test starts and ends
without them. The process resolver and hostenv are reset by ``mas.runtime.hostenv_pytest``.
"""

from __future__ import annotations

import pytest


def _clear() -> None:
    from mas.lab.secret_host import set_process_settings

    set_process_settings(None)


@pytest.fixture(autouse=True)
def _no_lab_secret_store_left_behind():
    _clear()
    yield
    _clear()
