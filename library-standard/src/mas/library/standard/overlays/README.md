<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Overlays (MAS Library Standard)

Index of every overlay shipped in this package. Each file is a `kind: Overlay`
manifest (`apiVersion: mas/v1`) merged onto an agent with `-o`.

Packaged URI: `pkg://mas.library.standard/overlays/<file>`.

Checkout path: `library-standard/src/mas/library/standard/overlays/<file>`.

| File | `metadata.name` | Target | What it does |
|------|-----------------|--------|----------------|
| `observability-native.yaml` | `observability-native` | Agent | Writes native `events.jsonl` (`spec.observability` → `native`). |
| `with-hardened.yaml` | `with-hardened` | Agent | Production posture: appends `gov_no_undeclared_tool` and `retry_on_error` to the governance chain; merges `control.retry` + `circuit_breaker` (transient/unavailable retry, application errors stay in working memory); caps `spec.budget.max_llm_calls` / `max_tool_calls` and `design_pattern.params.max_steps` (512). |
| `cheap-summarizer.yaml` | `cheap-summarizer` | Agent | Sets `summarizer.params.model` to `gpt-4o-mini` (same `summarising` type; params merge). |
| `openclaw.yaml` | `openclaw` | Agent | Enables LLM-call checkpoints, self-contained persistence, auto-resume, and repeated-error backtracking. |

`spec.observability` is a sequence (every plugin sees every event).
`spec.governance` is a chain: BLOCK stops and returns the error; ALLOW
passes to the next plugin. `with-hardened` uses `$op.add`, so it keeps any
governance plugins already on the agent, then merges reliability knobs
under `spec.control`. Stack more overlays the same way — a future
OpenClaw-like flavour would compose `with-hardened` + observability +
checkpoint/backtrack overlays rather than inlining each plugin
([reliability.md](../../../../../../docs/references/reliability.md),
[governance.md](../../../../../../docs/manifests/governance.md)).

```bash
mas-ctl chat agent.yaml \
  -o pkg://mas.library.standard/overlays/with-hardened.yaml \
  -o pkg://mas.library.standard/overlays/observability-native.yaml
```

CLI shortcut `--events` is equivalent to `observability-native` for one run.
See [docs/cli/observability.md](../../../../../../docs/cli/observability.md).

The `openclaw` control preset enables full LLM-call checkpointing, self-contained
resume, and repeated-error backtracking. See
[Session checkpoints](../../../../../../docs/manifests/checkpoint.md).

`gov_no_undeclared_tool` example (not a sample app):
[examples/governance/undeclared-tool/](../../../../../examples/governance/undeclared-tool/).

Summarizer model override example:
[examples/context/summarizer-override/](../../../../../examples/context/summarizer-override/).
