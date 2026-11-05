#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for benchmark runner initialization."""

import tempfile

from mas.lab.benchmark import MultiRunOrchestrator, ResultStorage


def test_orchestrator_init():
    """Test MultiRunOrchestrator initialization."""
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = ResultStorage(tmpdir)

        orchestrator = MultiRunOrchestrator(storage=storage, n_runs=3, pause_between_runs=0.5)

        assert orchestrator.storage == storage
        assert orchestrator.n_runs == 3
        assert orchestrator.pause_between_runs == 0.5


# Note: Full integration tests require async setup and agent factory
# Those are better suited for end-to-end tests
