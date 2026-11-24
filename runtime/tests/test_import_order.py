#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""A runtime module imports on its own, whichever module a process loads first.

``import mas.runtime`` does not load the kernel, so a module that is the first one imported must not need the kernel's
modules to have been imported before it.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

FIRST = (
    "mas.runtime.machines.model",
    "mas.runtime.machines.gov",
    "mas.runtime.machines.tool",
    "mas.runtime.boundary.gov.filter",
    "mas.runtime.boundary.gov.plugin",
)


@pytest.mark.parametrize("module", FIRST)
def test_module_imports_as_the_first_import_of_a_process(module: str) -> None:
    done = subprocess.run([sys.executable, "-c", f"import {module}"], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr[-600:]
