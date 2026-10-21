<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Governance contracts

Governance contracts are policy boundaries applied before and after capability contracts fire.
This page is for contributors who add budget or policy-engine rules. Governance plugins raise `PolicyViolation` to deny;
the kernel maps decisions to egress chokepoints.

Related: [governance-policy-engine.md](../../governance-policy-engine.md),
[semantic-protocols.md](../../semantic-protocols.md), [mealy-envelope.md](../../mealy-envelope.md),
the user reference [governance.md](../../../../docs/manifests/governance.md), and
[reliability.md](../../../../docs/references/reliability.md) (retries, `error_policy`, logging).

Governance contracts are orthogonal to capability contracts: you can add budget or policy-engine rules without changing
tool or model plugins. The kernel evaluates egress policy in `kernel/envelope.py` (`_evaluate_egress`,
`_evaluate_ingress`) at the `GOVERNANCE_AUTHORIZE` and `GOVERNANCE_VALIDATE` symbols. `GovEnvelopeMachine` records
state and telemetry on those symbols; the envelope coordinator runs the imperative evaluation (see the
`gov_envelope.py` module docstring).

## BudgetTracker

Token and call-rate ceilings over the lifetime of a run (`mas.runtime.boundary.gov.budget.BudgetTracker`).
The counters are monotonic.

## Declarative policies

For YAML-driven governance (HITL, content filters, budget triggers), use the Governance Policy Engine plugin:
[governance-policy-engine.md](../../governance-policy-engine.md). It composes `trigger x evaluation x action`
and attaches to hook phases.

## Envelope mapping

Every governed egress walks:

```text
GOV_AUTHORIZE_START -> GOVERNANCE_AUTHORIZE -> GOV_AUTHORIZE_END
  -> ... execute ...
GOV_VALIDATE_START -> GOVERNANCE_VALIDATE -> GOV_VALIDATE_END
```

The observability summand records `governance.decision` events at the before and after checkpoints.
`mas-ctl chat --without-gov` disables governance (it collapses sigma).

## Implementation status

| Contract | v2 kernel chokepoint | Notes |
|----------|---------------------|-------|
| Budget | Via policy engine / sample gov | Manifest `governance.policies` |

OSS tutorials and paper labs use budget caps, guardrails and HITL.
