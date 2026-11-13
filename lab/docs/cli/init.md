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

The infra file names the address as an env var, so several providers can coexist:

```yaml
spec:
  proxy:
    api_base: env:OPENAI_BASE_URL|https://api.openai.com/v1
    api_key_env: OPENAI_API_KEY
```

The OpenAI default URL uses `OPENAI_BASE_URL`. A custom URL uses `<infra-name>_API_BASE` so a leftover `OPENAI_BASE_URL` in the shell does not steal the call. `standard:openai` ships as `env:OPENAI_BASE_URL|https://api.openai.com/v1`.

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
