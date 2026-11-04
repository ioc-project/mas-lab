#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Infrastructure services registry and emulation."""

from __future__ import annotations

from typing import Any, Dict, List

# Registry of available infrastructure services
INFRASTRUCTURE_SERVICES = [
    {
        "id": "observe",
        "type": "infra",
        "label": "observe",
        "description": "Observability and monitoring service",
        "status": "emulated",
        "port": None,  # Will be assigned when real service is available
    },
    {
        "id": "eval",
        "type": "infra",
        "label": "eval",
        "description": "Evaluation and quality assessment service",
        "status": "emulated",
        "port": None,
    },
    {
        "id": "explain",
        "type": "infra",
        "label": "explain",
        "description": "Explanation and reasoning service",
        "status": "emulated",
        "port": None,
    },
]


def get_infrastructure_services() -> List[Dict[str, Any]]:
    """Get list of available infrastructure services."""
    return INFRASTRUCTURE_SERVICES.copy()


def get_service_by_id(service_id: str) -> Dict[str, Any] | None:
    """Get infrastructure service by ID."""
    for service in INFRASTRUCTURE_SERVICES:
        if service["id"] == service_id:
            return service.copy()
    return None


def is_infrastructure_node(node_id: str) -> bool:
    """Check if a node ID corresponds to an infrastructure service."""
    return any(service["id"] == node_id for service in INFRASTRUCTURE_SERVICES)
