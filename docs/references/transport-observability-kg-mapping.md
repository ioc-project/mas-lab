<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Transport events to the knowledge graph

How native `transport.*` events become SCOPE nodes and edges, and which
observe-sdk names the OTel projector emits so the existing OTel path keeps
working. Companion of
[transport-observability.md](transport-observability.md).

Installed `ioc-scope-norm` 1.0.0 has no realtime transport handlers. The
order-agnostic source (`ioc-scope-norm` with realtime `ioa_observe`
handlers) maps each record alone. Native handlers match that mapping.

## Category

Message and discovery kinds fold **by default**, matching SCOPE's norm
(no category gate). They declare the existing `Agent` and `Tool` catalog.
`categories=transport` remains a valid name and adds no extra class.
Session, internal and error stay unmapped.

## Field table

| Native kind | Alias (observe-sdk) | Required fields | Nodes | Edges | Missing fields | Duplicate / order |
| --- | --- | --- | --- | --- | --- | --- |
| `transport.message.sent` / `.received` / `.completed` | `a2a.message.*`, `slim.message.*` | `attributes.source`, `attributes.target` | `Agent` id = agent id (each end); `MAS` if `mas_id` is set | `belongsToMAS` from each Agent | An empty end is skipped | Same graph; no Agent–Agent edge (ontology has none) |
| `transport.discovery` (`discovery_kind=agent_card`) | `a2a.agent_card` | `attributes.card.name` | `Agent` named by the card | `belongsToMAS` | No name → nothing | Merge by Agent id |
| `transport.discovery` (`discovery_kind=tools`) | `mcp.tools.discovered` | `attributes.tools` list/object with `name` | `Tool` per name | `usesTool` | Empty list → nothing | Merge by Tool id |
| `transport.session` | none | — | none | none | — | Dropped (`KIND_TO_CLASS` is `None`) |
| `transport.internal` | none | — | none | none | — | Dropped |
| `transport.error` | none | — | none | none | — | Dropped |

`mcp.message.*` is not aliased. SCOPE maps those names to nothing
(`INFORMATIONAL_EVENTS`); the tool call is the fact.

A tools discovery declares only the `Tool`s, as norm's `mcp.tools.discovered`
does. Its `usesTool` edge names the listing agent and stays only when another
event declares that agent; the graph drops an edge whose source is not a node,
on both paths. Node ids follow the native catalog (`catalog:tool:<name>`),
norm's are the bare name, so equivalence is on the sets of agents, tools and
`usesTool` pairs, not on id strings
(`library-telemetry/tests/transport/test_norm_equivalence.py`, run with the
order-agnostic norm on `PYTHONPATH`; skipped without it).

## No-class list

| Fact | Proposal |
| --- | --- |
| Link / channel setup (`transport.session`) | Drop from native KG until an ontology class exists |
| Protocol internals (`transport.internal`) | Drop |
| Transport error (`transport.error`) | Drop |
| Agent–Agent message edge | Do not invent; declare the two Agents only |

## Version differences (installed 1.0.0 vs order-agnostic)

Installed 1.0.0 has no `handle_agent_pair` / `handle_tools_declared` /
`handle_agent_card`. Equivalence tests for the OTel path use the
order-agnostic normalizer on `PYTHONPATH` when it is present; otherwise
they compare native graphs only.

Golden `(nodes, edges)` live in
`library-kg/tests/kg/native/fixtures/transport/`.
