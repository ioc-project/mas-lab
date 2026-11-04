<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Bench infrastructure types (`lab/components/bench`)

**Not part of mas-runtime or mas-ctl.** These modules are lab/bench-only helpers
for benchmark pipelines and telemetry codecs.

| Module | Purpose |
|--------|---------|
| `mas.lab.infra.datastore` | `DatastoreSpec`, `ArtifactBinding` — typed connection specs for ClickHouse, Neo4j, filesystem backends |
| `mas.lab.infra.stores` | `resolve_datastore()` — resolve named stores from workspace infra YAML via `mas.ctl.infra.resolve` |

Runtime execution uses `infra/v1` manifests and ctl resolution. Bench codecs
(`benchmark/codecs/`) and serialize/deserialize pipeline steps need a stable,
bench-local dataclass layer because v2 ctl does not expose datastore connection
types for post-run export.

Consumers:

- `mas.lab.benchmark.codecs.base`, `otel_codecs`
- `mas.library.lab.steps.data.serialize`, `deserialize`

If datastore specs become shared outside bench, consider promoting them to a
library package — not to runtime/ctl.
