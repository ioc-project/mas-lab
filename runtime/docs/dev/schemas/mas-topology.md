<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# MAS manifest (topology) schema

This document has moved.

**Authoritative reference:** [docs/manifests/mas.md](../../../../docs/manifests/mas.md)

**JSON Schema (YAML):** [`docs/schemas/runtime/mas.schema.yaml`](../../../../docs/schemas/runtime/mas.schema.yaml)

A multi-agent system is declared by a `kind: MAS` manifest (`apiVersion: mas/v1`). The
agents are listed under `spec.agency.agents` and the delegation graph (the topology) under
`spec.workflow`:

```yaml
apiVersion: mas/v1
kind: MAS
metadata:
  name: trip-planner
spec:
  agency:
    agents:
      - id: broker
        ref: ./agents/broker.yaml
      - id: flights
        ref: ./agents/flights.yaml
  workflow:
    entry: broker
    nodes:
      - id: broker
        delegates_to: [flights]
      - id: flights
```

Topology variants (extra agents, different delegation edges, governance changes) are
expressed as overlays on top of this base manifest. See [docs/manifests/overlay.md](../../../../docs/manifests/overlay.md)
and [Tutorial 2](../../../../docs/tutorials/02-creating-a-mas/README.md).

The legacy `mas.framework/v1` shape (`spec.agents[].agent_config`, `spec.topology.connections`,
per-agent `replicas` and `resources`) is no longer used.
