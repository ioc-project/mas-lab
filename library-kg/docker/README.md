<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Local OXP / Neo4j for Tutorial 9

MAS-Lab Tutorial 9 (KG & OXP) serializes knowledge graphs in two ways:

| Store | How | This package |
| --- | --- | --- |
| `kg.jsonld` on disk | `normalize_events` / `normalize_otel` | always |
| Neo4j | `neo4j_push` + `infra/local-neo4j.yaml` | `[neo4j]` extra |

```bash
docker compose -f library-kg/docker/compose.yaml up -d
export NEO4J_URI=bolt://localhost:7687   # shortcut for infra/local-neo4j.yaml
```

The compose file starts Neo4j 5 (Bolt on 7687, browser on 7474) with
authentication disabled; use it on a laptop only.

`$NEO4J_URI` is equivalent to passing `--infra library-kg/infra/local-neo4j.yaml`.

The Observe and eXplain Platform (OXP) `norm.normalize()` path that Tutorial 9
uses for **OTel → KG** does not need this compose — it is an in-process Python
call (extra `[norm]`, installed from git until it is published). Bring up the
full OXP stack (API, workers, UI) from the OXP repository when you want its
UI; it is not needed for the tutorial pipelines:

https://github.com/outshift-open/observe-and-explain-platform/blob/main/backend/docker-compose.yml

The ClickHouse instance OXP can ingest from belongs to `mas-library-telemetry`
(`library-telemetry/docker/compose.yaml`).
