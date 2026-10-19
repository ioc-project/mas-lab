<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-control-start(1)

## NAME

`mas-lab control start` — Start the controller daemon

## SYNOPSIS

```text
mas-lab control start [OPTIONS]
```

## DESCRIPTION

Start the controller daemon.

## OPTIONS

| Flag | Meaning |
|------|------|
| `--port`, `-p` | HTTP port for UI. |
| `--no-http` | IPC only — no HTTP server. |
| `--foreground`, `-f` | Run daemon in foreground (dev). |

## SEE ALSO

- `mas-lab control`
- `mas-lab control stop`
- `mas-lab control status`
- `mas-lab --help`
