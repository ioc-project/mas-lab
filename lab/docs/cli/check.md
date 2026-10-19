<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-check(1)

## NAME

`mas-lab check` — Validate a MAS configuration

## SYNOPSIS

```text
mas-lab check [OPTIONS]
```

## DESCRIPTION

Validate a MAS configuration.

## OPTIONS

| Flag | Meaning |
|------|------|
| `MAS_SPEC` | Positional argument. |
| `-v`, `--verbose` | Verbosity: -v all checks, -vv debug, -vvv full debug. |
| `--base-dir` | Base directory for resolving relative file references. |
| `--strict`, `--no-strict` | Strict mode (default): warn on missing recommended fields (spec.description, spec.design_pattern, metadata.description). Use --no-strict to suppress those warnings. |

## SEE ALSO

- `mas-lab`
- `mas-lab init`
- `mas-lab config`
- `mas-lab check-config`
- `mas-lab validate`
- `mas-lab serve`
- `mas-lab --help`
