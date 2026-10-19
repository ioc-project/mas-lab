<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-kg(1)

## NAME

`mas-lab kg` — normalize, validate, push, and dump knowledge graphs

## SYNOPSIS

```text
mas-lab kg [COMMAND] [ARGS]...
```

## DESCRIPTION

Library CLI component (entry point `mas.lab.cli.components`). Commands call
the same step functions as the pipeline YAML under `library-kg/pipelines/`.

## COMMANDS

| Command | Summary | Page |
|---------|---------|------|
| `normalize` | Native events or OTel → kg.json | [normalize](normalize.md) |
| `validate` | Validate a KG document | [validate](validate.md) |
| `verify` | Verify native events.jsonl | [verify](verify.md) |
| `otel-to-events` | OTel spans → clickhouse-shaped JSONL | [otel-to-events](otel-to-events.md) |
| `otel-to-kg` | OTel spans → kg.json | [otel-to-kg](otel-to-kg.md) |
| `push` / `neo4j-push` | Serialize KG to Neo4j | [push](push.md) |
| `dump` | Deserialize a KG from Neo4j | [dump](dump.md) |

Site index: [docs/cli/mas-lab.md](../../../docs/cli/mas-lab.md).

## SEE ALSO

[normalization.md](../normalization.md), [neo4j.md](../neo4j.md),
[cli-pipelines.md](../../../lab/docs/cli-pipelines.md)
