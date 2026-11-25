<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-benchmark-runs-merge(1)

## NAME

`mas-lab benchmark runs merge` — Merge shard results trees

## SYNOPSIS

```text
mas-lab benchmark runs merge [OPTIONS]
```

## DESCRIPTION

Merges the results trees of several shards into one. A run folder that finished `ok` is never replaced by one that did not.

## OPTIONS

| Flag | Meaning |
|------|------|
| `SOURCES` | Positional argument. |
| `-o`, `--output` | — |

## SEE ALSO

- `mas-lab benchmark`
- `mas-lab benchmark run`
- `mas-lab benchmark runs`
