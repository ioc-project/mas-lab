<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-serve(1)

## NAME

`mas-lab serve` — Start the benchmark UI server

## SYNOPSIS

```text
mas-lab serve [OPTIONS]
```

## DESCRIPTION

Start the benchmark UI server.

## OPTIONS

| Flag | Meaning |
|------|------|
| `--port`, `-p` | TCP port for the controller HTTP API. |
| `--host` | Bind address. Loopback stays CORS-permissive; non-loopback binds send no CORS headers unless `--cors-origin` is set. |
| `--cors-origin URL` | Repeatable. Allowed browser origin for a remote UI. |
| `--token-file PATH` | Bearer token. Clients send `Authorization: Bearer`. None = open (warning on non-loopback). |
| `--state-dir PATH` | Persist jobs, runs, and uploads. |
| `--workspace PATH` | Project folder for `config.yaml`, labs, libraries, and the writable Workspace library. Sets `MAS_WORKSPACE_ROOT`. |

## SEE ALSO

- `mas-lab`
- `mas-lab init`
- `mas-lab config`
- `mas-lab check`
- `mas-lab check-config`
- `mas-lab validate`
- `mas-lab --help`
