<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-init(1)

## NAME

`mas-lab init` — Bootstrap user config and optional LLM infra

## SYNOPSIS

```text
mas-lab init [OPTIONS]
```

## DESCRIPTION

Bootstrap user config and optional LLM infra. Prints a short summary and the first commands to run; `--quiet` hides it.

The infra file holds a plain URL and a SecretRef:

```yaml
spec:
  proxy:
    api_base: https://api.openai.com/v1
    api_key: {secret: openai}
```

Set the key with `echo '<your-key>' | mas-ctl secrets set openai`. Overlay a different `api_base` when the host is not api.openai.com.

## OPTIONS

| Flag | Meaning |
|------|------|
| `--yes` | Use defaults without prompts. |
| `--local` | Write config.yaml and infra/<name>.yaml in the current project. |
| `-q`, `--quiet` | Print nothing once the files are written (prompts and errors still show). |

## SEE ALSO

- `mas-lab`
- `mas-lab config`
- `mas-lab check`
- `mas-lab check-config`
- `mas-lab validate`
- `mas-lab serve`
- `mas-lab --help`
