<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-library-standard User Guide

`mas-library-standard` provides reusable plugins, tools and flavours for MAS runtime projects. This guide is for users who apply them to an agent manifest.

```bash
uv pip install -e library-standard
mas-ctl chat agent.yaml -o pkg://mas.library.standard/overlays/with-hardened.yaml
```

## What you get

- Design-pattern plugins (`dp_*`)
- Skills and tool providers (`sk_*`, `tool_*`)
- Context and memory plugins (`ctx_*`, `memory_*`)
- Observability and governance plugins (`obs_*`, `gov_*`)
- Overlays (`observability-native`, `with-hardened`) — [index](../src/mas/library/standard/overlays/README.md)
- Transport and integration plugins (`tp_*`, `tool_server_*`)

## Typical usage

1. Install the library package.
2. Reference plugin and tool module paths in the agent manifest.
3. Activate optional overlays or flavours per environment.

## Shared tutorial tools

- `WebSearchTool`
- `VerifyFactTool`
- `GetAttractionsTool`
- `GetScheduleTool`

Use these for tutorials and integration smoke tests. App-specific samples belong in labs or apps, not in this library.

## Common tasks

### Enable memory behavior

Use the standard memory plugins for workspace and session context and for compaction hooks.

### Overlays

Index: [src/mas/library/standard/overlays/README.md](../src/mas/library/standard/overlays/README.md).

| Overlay | Apply |
|---------|--------|
| `observability-native` | native `events.jsonl` |
| `with-hardened` | append `gov_no_undeclared_tool` + `retry_on_error`; merge retry/circuit knobs |
| `cheap-summarizer` | `summarizer.params.model: gpt-4o-mini` |

```bash
mas-ctl chat agent.yaml \
  -o pkg://mas.library.standard/overlays/with-hardened.yaml \
  -o pkg://mas.library.standard/overlays/observability-native.yaml
```

CLI shortcut `--events` is equivalent to `observability-native` for one run.
Full flag matrix: [docs/cli/observability.md](../../docs/cli/observability.md).

### Add governance controls

Enable budget and policy plugins to constrain calls, tokens or tool access.

The `with-hardened` overlay refuses tool names the model was not given in this LLM call's `tools` list, even if they appear on the agent spec. You can also list the plugin on the governance chain: `spec.governance` is a chain (BLOCK stops, ALLOW continues), while `spec.observability` is a sequence. The overlay also installs `retry_on_error` and the `spec.control` retry and circuit knobs; see
[reliability.md](../../docs/references/reliability.md),
[governance.md](../../docs/manifests/governance.md).

```yaml
governance:
  - gov_no_undeclared_tool
```

Feature example (not a sample app):
[examples/governance/undeclared-tool/](../examples/governance/undeclared-tool/).
Index: [examples/](../examples/README.md).
Card: [no-undeclared-tool.md](../src/mas/library/standard/plugins/governance/no-undeclared-tool.md).

```bash
mas-ctl validate library-standard/examples/governance/undeclared-tool/agent.yaml
mas-ctl chat library-standard/examples/governance/undeclared-tool/agent.yaml \
  -q "Investigate the latency spike for payment-service."
```

### Override the history summarizer

The default `summarising` manager with `summarizer: llm` uses the summarizer slot (Agent, then MAS, then `experiment.models.summarizer`) or else this agent's resolved turn model, plus a package system prompt. Compaction fires on the token budget, not on that prompt. `summarizer.params.model` overrides the summary call only.

```yaml
context_manager:
  type: summarising
  params:
    summarizer:
      type: llm
      params:
        model: gpt-4o-mini
        instructions: |
          Preserve city names and dates. One paragraph.
```

Feature example (not a sample app):
[examples/context/summarizer-override/](../examples/context/summarizer-override/).
Card: [summarizer.md](../src/mas/library/standard/plugins/context/summarizer.md).
Docs: [summarization.md](../../docs/manifests/summarization.md).

```bash
mas-ctl validate library-standard/examples/context/summarizer-override/agent.yaml
mas-ctl chat library-standard/examples/context/summarizer-override/agent.yaml -v \
  -q "We fly to Lyon on 12 May."
```

Or overlay: `-o pkg://mas.library.standard/overlays/cheap-summarizer.yaml`.

### Add reasoning patterns

Apply design-pattern plugins (CoT, ReAct, plan-execute, introspection) through overlays.

### Troubleshooting

- Plugin not found: verify package installation and manifest module path.
- Tool schema mismatch: check ToolContract input types and field names.
- Unexpected behavior: inspect active plugin order and overlay precedence.
