<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Example: undeclared tool (`gov_no_undeclared_tool`)

Governance feature example. Not a sample app.

A skill (and the skill catalog) names `get_deployments`. This agent's
`spec.tools` does **not**. The model can still emit that name.
`gov_no_undeclared_tool` is on the governance *chain*: BLOCK exits the
chain with that error; the kernel feeds the reason back as a tool
observation. Native observability writes `kind: governance_decision`.

`llm_call.json` is the recorded chat-completions payload that shows the
same mismatch. It is not a runner.

| File | Role |
|------|------|
| `agent.yaml` | Runnable agent: listed tools and skill |
| `control.yaml` | Control: the `gov_no_undeclared_tool` governance chain |
| `flavour.yaml` | Flavour: attaches the Control to the agent |
| `tools/` | `get_metrics`, `get_logs`, `get_service_health` (no `get_deployments`) |
| `skills/data-access-protocol/SKILL.md` | Skill body that names `get_deployments` |
| `llm_call.json` | Recorded LLM payload: `tools` omit `get_deployments` |

From the repo root (workspace config / Tutorial 0 infra):

```bash
mas-ctl validate library-standard/examples/governance/undeclared-tool/agent.yaml
mas-ctl validate library-standard/examples/governance/undeclared-tool/flavour.yaml
mas-ctl chat library-standard/examples/governance/undeclared-tool/agent.yaml \
  --flavour library-standard/examples/governance/undeclared-tool/flavour.yaml \
  -q "Investigate the latency spike for payment-service."
```

To add the same chain rule to some other agent, attach the Control with a Flavour
(`spec.control`), or use the overlay:

```bash
mas-ctl chat other-agent.yaml \
  --control pkg://mas.library.standard/controls/with-hardened.yaml \
  --control pkg://mas.library.standard/controls/observability-native.yaml
```

Plugin card: [no-undeclared-tool.md](../../../src/mas/library/standard/plugins/governance/no-undeclared-tool.md).
Overlay index: [overlays/README.md](../../../src/mas/library/standard/overlays/README.md).
Category: [governance examples](../README.md).
