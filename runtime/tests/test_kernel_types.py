#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for kernel.types shared symbols and import-cycle hygiene."""

from __future__ import annotations

import importlib
import sys


def test_kernel_types_has_no_machines_import():
    mod = importlib.import_module("mas.runtime.kernel.types")
    source_path = mod.__file__
    assert source_path is not None
    text = open(source_path, encoding="utf-8").read()
    assert "mas.runtime.machines" not in text


def test_gov_state_lives_in_kernel_types():
    from mas.runtime.kernel.state import QProduct
    from mas.runtime.kernel.types import GovState
    from mas.runtime.machines.gov import gov_is_hitl_pending

    q = QProduct()
    q.gov_state = GovState.HITL_PENDING.value
    q.hitl_request_id = 1
    assert gov_is_hitl_pending(q) is True


def test_orchestrator_imports_without_machines_lifecycle_at_module_level():
    """lifecycle → coupling is deferred; orchestrator should not pull machines at import."""
    saved = {k: sys.modules.pop(k) for k in list(sys.modules) if k.startswith("mas.runtime.machines.lifecycle")}
    try:
        if "mas.runtime.kernel.orchestrator" in sys.modules:
            del sys.modules["mas.runtime.kernel.orchestrator"]
        mod = importlib.import_module("mas.runtime.kernel.orchestrator")
        assert "mas.runtime.machines.lifecycle" not in sys.modules
        assert mod.RuntimeKernel is not None
    finally:
        sys.modules.update(saved)
