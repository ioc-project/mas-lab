# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
from pathlib import Path


def package_root() -> Path:
    return Path(__file__).resolve().parent


def require_native_hooks() -> None:
    try:
        from library_ioa.plugins.a2a.delivery import DeliveryController, DeliveryPolicy
        from library_ioa.plugins.a2a.exposure import A2AExposure
        from mas.runtime.boundary.gov.transition import GovTransition
    except ImportError as error:
        raise RuntimeError("MasLab with the native A2A delivery-policy extension is required") from error
    if not callable(getattr(DeliveryController, "release", None)):
        raise RuntimeError("Unsupported MasLab A2A delivery-policy interface")
    if not callable(getattr(A2AExposure, "validate_transports", None)):
        raise RuntimeError("MasLab with native A2A transport validation is required")
    del DeliveryPolicy, GovTransition
