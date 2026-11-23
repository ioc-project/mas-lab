#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import logging

from mas.library.telemetry.transport.warnings import count, reset, warn_once


def test_repeated_faults_log_once(caplog) -> None:
    reset()
    caplog.set_level(logging.WARNING, logger="mas.library.telemetry.transport")
    for _ in range(1000):
        warn_once("classifier_error", "Boom:ValueError", "classifier Boom raised ValueError on span x")
    assert count("classifier_error", "Boom:ValueError") == 1000
    lines = [r for r in caplog.records if r.name == "mas.library.telemetry.transport"]
    assert len(lines) == 1
    reset()
