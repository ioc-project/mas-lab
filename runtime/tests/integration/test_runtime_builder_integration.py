#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""RuntimeBuilder end-to-end embed integration."""

from mas.runtime.driver.instance import RuntimeInstance
from mas.runtime.factory.builder import RuntimeBuilder
from mas.runtime.kernel.config import KernelConfig


def test_builder_kernel_driver_round_trip():
    instance = RuntimeBuilder(config=KernelConfig()).build()
    assert isinstance(instance, RuntimeInstance)
    trace = instance.run_user_text("ping")
    assert trace is not None
