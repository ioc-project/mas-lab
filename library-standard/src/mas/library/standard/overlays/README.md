<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Overlays (MAS Library Standard)

Index of every overlay and Control shipped in this package. Overlay files are
`kind: Overlay` (`apiVersion: mas/v1`) merged onto an agent with `-o`. Control
files are `kind: Control` (`apiVersion: control/v1`); attach them with
`--control`. A scenario's `overlays.control` attaches Control files the same way.
Passing a Control with `-o`, or listing Controls under an Overlay's
`spec.control`, is deprecated.

Packaged URI: `pkg://mas.library.standard/overlays/<file>`.

Checkout path: `library-standard/src/mas/library/standard/overlays/<file>`.

Governance, control-plane settings and event sinks are not patches of the agent: they are Controls, in
`library-standard/src/mas/library/standard/controls/` and attached with `--control pkg://mas.library.standard/controls/<file>`:

| File | `metadata.name` | What it does |
|------|-----------------|--------------|
| `observability-native.yaml` | `observability-native` | Writes native `events.jsonl`. |
| `with-hardened.yaml` | `with-hardened` | Production guidelines, written once for every agent: `gov_no_undeclared_tool`, `gov_no_undeclared_peer`, the `budget` call ceilings, and `retry_on_error`. |
| `openclaw.yaml` | `openclaw` | Repeated-error backtracking. Pair it with the `openclaw` overlay. |

The overlays below are patches of the agent spec.

| File | `metadata.name` | Kind | What it does |
|------|-----------------|------|----------------|
| `openclaw.yaml` | `openclaw` | Overlay | Enables LLM-call checkpoints, self-contained persistence, auto-resume. |
| `production-memory.yaml` | `production-memory` | Overlay | Reviewed `keep_turns` / hysteresis / trimmer margin. Archive path lives in `config.yaml`, not this overlay. See [context-management.md](../../../../../../docs/references/context-management.md). |
| `cheap-summarizer.yaml` | `cheap-summarizer` | Overlay | Sets `summarizer.params.model` to `gpt-4o-mini` (same `summarising` type; params merge). |

A Control's `observability` is a sequence (every plugin sees every event).
A Control's `governance` is a chain: BLOCK stops and returns the error; ALLOW
passes to the next plugin. `with-hardened` replaces the agent's governance. Stack more files the same way — a future OpenClaw-like flavour
would compose `with-hardened` + observability + checkpoint/backtrack rather
than inlining each plugin
([reliability.md](../../../../../../docs/references/reliability.md),
[governance.md](../../../../../../docs/manifests/governance.md)).

```bash
mas-ctl chat agent.yaml \
  --control pkg://mas.library.standard/controls/with-hardened.yaml \
  --control pkg://mas.library.standard/controls/observability-native.yaml
```

CLI shortcut `--events` records native events for one run.
See [docs/cli/observability.md](../../../../../../docs/cli/observability.md).

The `openclaw` Control enables full LLM-call checkpointing, self-contained
resume, and repeated-error backtracking. See [Session checkpoints](../../../../../../docs/manifests/checkpoint.md).

`gov_no_undeclared_tool` example (not a sample app):
[examples/governance/undeclared-tool/](../../../../../examples/governance/undeclared-tool/).

Summarizer model override example:
[examples/context/summarizer-override/](../../../../../examples/context/summarizer-override/).
