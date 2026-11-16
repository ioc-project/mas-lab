#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Engine package — simulated adapter."""

from mas.runtime.engine.simulated import SimMode, SimulatedEngine, simulated_next_step

__all__ = [
    "SimMode",
    "SimulatedEngine",
    "simulated_next_step",
]
