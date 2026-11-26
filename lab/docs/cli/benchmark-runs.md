<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-benchmark-runs(1)

## NAME

`mas-lab benchmark runs` — Plan, shard, merge, retry and summarize runs as separate steps

## SYNOPSIS

```text
mas-lab benchmark runs [OPTIONS]
```

## DESCRIPTION

Each subcommand is one role (generation and scheduling, retry, analysis) working on files only, so a CI job can run it on its own and the runs themselves on many machines. See `docs/manifests/ci-sharding.md`.

## OPTIONS

This command takes no extra flags. See `--help` for inherited group options.

## SEE ALSO

- `mas-lab benchmark`
- `mas-lab benchmark run`
- `mas-lab benchmark runs`
