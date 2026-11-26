<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-kg-dump(1)

## NAME

`mas-lab kg dump` — Deserialize a KG from Neo4j

## SYNOPSIS

```text
mas-lab kg dump [OPTIONS]
```

## DESCRIPTION

Read a session or run KG back from Neo4j. Same `neo4j_dump` step as `kg:neo4j-dump`.

## OPTIONS

| Flag | Meaning |
|------|------|
| `--session-id` | — |
| `--run-id` | — |
| `--output`, `-o` | — |
| `--infra` | — |
| `--uri` | — |
| `--username` | — |
| `--database` | — |

## PIPELINE

`kg:neo4j-dump`

## SEE ALSO

- `mas-lab kg`
- `mas-lab kg normalize`
- `mas-lab kg validate`
- `mas-lab kg verify`
- `mas-lab kg otel-to-events`
- `mas-lab kg otel-to-kg`
- `mas-lab --help`
