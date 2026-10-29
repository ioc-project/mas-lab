#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Schema registry is process-local; validate must not rebuild it every call."""

from mas.ctl.validate.schemas import lab_schema_registry


def test_lab_schema_registry_is_reused() -> None:
    first = lab_schema_registry()
    second = lab_schema_registry()
    assert first is second
