<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Infrastructure Services

Modular infrastructure services for the MAS framework.

## Overview

This module provides a pluggable architecture for infrastructure services that support multi-agent systems:

- **observe**: Observability and monitoring
- **eval**: Evaluation and quality assessment
- **explain**: Explanation and reasoning

## Architecture

### Service Registry (`__init__.py`)

Defines available infrastructure services with metadata:

```python
INFRASTRUCTURE_SERVICES = [
    {
        "id": "observe",       # Unique identifier
        "type": "infra",       # Node type for UI
        "label": "observe",    # Display label
        "description": "...",  # Service description
        "status": "emulated",  # "emulated" or "active"
        "port": None,          # Port when running as real service
    },
    # ...
]
```

### Service Emulator (`emulator.py`)

Provides mock implementations that:
- Publish events to UI feed
- Respond to MAS lifecycle hooks
- Can be replaced with real services later

### Controller Integration

The controller exposes infrastructure services via API:

- `GET /api/infrastructure` - List available services
- `GET /api/startup-status` - Startup progress tracking

### UI Integration

The UI dynamically:
1. Fetches infrastructure services on init
2. Creates topology nodes automatically
3. Shows startup progress indicator
4. Discovers service capabilities via events

## Adding New Services

1. Add service to `INFRASTRUCTURE_SERVICES` in `__init__.py`
2. Create emulator class in `emulator.py` (optional)
3. Service appears automatically in UI topology
4. Replace emulator with real service when ready

## Real Service Migration

To replace an emulated service with a real one:

1. Implement service with event protocol
2. Update `status: "emulated"` → `"active"`
3. Set `port` field
4. Controller manages lifecycle
5. No UI changes needed!

## Event Protocol

Infrastructure services should emit events:

```json
{
  "timestamp": 1234567890.123,
  "kind": "infra_{service_id}",
  "service": "{service_id}",
  "payload": { ... },
  "target": "optional_target_id"
}
```

Benefits:
- UI auto-discovers nodes
- No hardcoding in frontend
- Services can be added/removed dynamically
- Easy migration from mock to real implementation
