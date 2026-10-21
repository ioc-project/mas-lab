<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Library-Standard Infrastructure Bundles

This directory holds the infrastructure manifest bundles that agent and flavour manifests reference with the `standard:` prefix. Use a bundle to point an agent at a model endpoint without writing an infra manifest.

```bash
mas-runtime run-agent agent.yaml --infra-ref standard:ollama
```

## Available Bundles

### Individual Infrastructure Manifests

- **`standard:openai`** — Direct OpenAI API connection
  - Models: gpt-4o, gpt-4o-mini, gpt-4-turbo, gpt-3.5-turbo
  - Requires: `OPENAI_API_KEY` environment variable
  - Endpoint: `https://api.openai.com/v1`

- **`standard:ollama`** — Local Ollama instance
  - Models: llama3.2, mistral, codellama, phi3, qwen2.5
  - Default: llama3.2
  - No API key required (assumes localhost:11434)

### Composite Bundles

- **`standard:production`** — Production configuration
  - Includes: `standard:openai`
  - Recommended for deployed agents and experiments

- **`standard:development`** — Development configuration
  - Includes: `standard:ollama`
  - For offline development and testing

- **`standard:llm-cache`** — `llm_cache` middleware (read+write, XDG cache path)

- **`standard:llm-proxy-cached`** — OpenAI provider with cache middleware in front

Write-only and strict-replay manifests with an explicit `cache_path`:
`library-samples/infra/llm-cache-{write,replay}.yaml`.

See [docs/manifests/llm-cache.md](../../../../../../docs/manifests/llm-cache.md) (guide) and
[docs/references/llm-cache.md](../../../../../../docs/references/llm-cache.md) (reference).

## Usage

### In User Config

Set a default infrastructure bundle in `$XDG_CONFIG_HOME/mas/config.yaml` (see [User config](../../../../../../docs/user-config.md)):

```yaml
apiVersion: mas.config/v1
kind: UserConfig

default_infra: standard:production
```

### In workspace `config.yaml`

```yaml
infra_refs:
  - standard:production  # or standard:development
```

Flavour manifests must not declare `infra_refs` (see `FlavourSeparationValidator`).

### Via CLI

Override at runtime with `--infra-ref`:

```bash
mas-runtime run-agent agent.yaml --infra-ref standard:ollama
mas-runtime run-agent agent.yaml --infra-ref standard:production
```

## Bundle Resolution

The runtime resolves `standard:*` references via Python entry points:

```toml
[project.entry-points."mas.runtime.manifest_libraries"]
standard = "mas.library.standard"
```

Directory layout:

```text
mas.library.standard/
  libs/
    standard/
      openai.yaml
      ollama.yaml
      production.yaml
      development.yaml
```

Resolution examples:

- `standard:openai` → `libs/standard/openai.yaml`
- `standard:production` → `libs/standard/production.yaml`

## Environment Variables

| Bundle | Required env vars |
| --- | --- |
| `standard:openai` | `OPENAI_API_KEY` |
| `standard:ollama` | None |

```bash
export OPENAI_API_KEY=your-key-here
mas-runtime run-agent agent.yaml
```

## Listing Available Bundles

```bash
mas-runtime list-bundles
```

## Creating Custom Bundles

See [CONTRIBUTING.md](../../../../../../CONTRIBUTING.md) for registering custom manifest libraries via entry points.
