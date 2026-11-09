#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Worker IO capture includes logging lines the CLI polls."""

from __future__ import annotations

import logging

from mas.lab.controller.io_capture import capture_worker_io
from mas.lab.controller.worker_model import WorkerKind, WorkerRecord


def test_capture_worker_io_includes_logger_error() -> None:
    record = WorkerRecord(id="w-1", kind=WorkerKind.BENCHMARK)
    logger = logging.getLogger("mas.lab.benchmark.engine")
    with capture_worker_io(record):
        logger.error("Experiment YAML not found: /tmp/missing.yaml")
    assert "Experiment YAML not found: /tmp/missing.yaml" in record.combined_stderr()
