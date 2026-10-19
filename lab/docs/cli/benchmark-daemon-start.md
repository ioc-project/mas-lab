<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-benchmark-daemon-start(1)

## NAME

`mas-lab benchmark daemon start` — Start the UI daemon

## SYNOPSIS

```text
mas-lab benchmark daemon start [OPTIONS]
```

## DESCRIPTION

Start the UI daemon.

## OPTIONS

| Flag | Meaning |
|------|------|
| `--port`, `-p` | TCP port for the Benchmark Explorer. |
| `-o`, `--output-dir` | Benchmarks root directory (persisted in the PID file). |
| `--force` | Kill an existing daemon before starting a new one. |

## SEE ALSO

- `mas-lab benchmark`
- `mas-lab benchmark run`
- `mas-lab benchmark list`
- `mas-lab benchmark show`
- `mas-lab benchmark update`
- `mas-lab benchmark rename`
- `mas-lab --help`
