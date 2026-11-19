#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Generic transport intake and projector. Protocol names live in plugins."""

from mas.library.telemetry.transport.intake import IntakeConfig, IntakeReport, install_intake
from mas.library.telemetry.transport.projector import project
from mas.library.telemetry.transport.registry import ClassifierSet

__all__ = [
    "ClassifierSet",
    "IntakeConfig",
    "IntakeReport",
    "install_intake",
    "project",
]
