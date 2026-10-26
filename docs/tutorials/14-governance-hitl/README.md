<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Tutorial 14 — Putting Human Approval Around Agent Actions

> **Packages:** `mas-ctl`, `mas-runtime`, `mas-library-standard`
> **Time:** about 40 minutes
> **Prerequisite:** [Tutorial 0](../00-environment-setup/README.md) for
> installation and LLM access. [Tutorial 1](../01-building-an-agent/README.md)
> introduces overlays. Run every command from the repository root.

## Goal

A prompt such as "ask before acting" is not a reliable control, because the
model decides whether to follow it. You build a budget assistant whose
calculator runs only after a human, or an explicit automated policy,
approves the call.

Governance means rules that inspect a proposed operation before or after it
crosses the agent boundary. HITL (human in the loop) means a rule pauses the
operation and asks a person. An overlay is a small YAML file merged on top
of a manifest with `-o`. All files are in `docs/tutorials/14-governance-hitl/`:

| File | Step | Purpose |
| --- | --- | --- |
| `agent.yaml` | 1 | Base agent |
| `overlays/tools.yaml` | 2 | Adds the calculator |
| `overlays/governance-interactive.yaml` | 3 | Asks an operator |
| `overlays/governance-auto-approve.yaml`, `governance-auto-deny.yaml` | 5 | Decide without a person |
| `overlays/agent-asks-user.yaml` | 7 | Agent asks a question |

The runtime enforces the rule. The agent cannot bypass it by changing its
answer or tool arguments. The flow is: the agent proposes a tool call, a rule
asks for approval, a reject stops it, an approval runs the tool, and an
optional second approval comes before the model sees the result.

## Step 1 — Start with the budget assistant

`agent.yaml` has no tool and no approval rule. Its core is:

```yaml
metadata:
  name: governed-budget-agent
spec:
  description: "Calculate and explain simple project budgets."
  context:
    role: |
      Help the user calculate a project budget. Show the expression used and
      explain the result. Use the calculator when it is available.
```

```bash
mas-ctl validate docs/tutorials/14-governance-hitl/agent.yaml
```

You should see `OK ... kind=agent`. The agent can reason about a budget but
cannot call a calculator.

## Step 2 — Add the calculator

The overlay adds the calculator from the installed `mas-library-samples`
package (the `samples:` prefix), so no relative path is needed. `$op: add`
appends to existing tools; Step 3 uses it for governance rules.

```yaml
spec:
  patch:
    tools:
      $op:
        add:
          - ref: samples:tools/calc.tool.yaml
```

```bash
mas-ctl chat docs/tutorials/14-governance-hitl/agent.yaml \
  -o docs/tutorials/14-governance-hitl/overlays/tools.yaml \
  -q "A workshop costs 125 dollars per person for 3 people, plus 42 dollars for materials. Calculate the total."
```

The agent calls `calc` with `125 * 3 + 42` and returns `$417`. Nothing
requires approval yet.

## Step 3 — Require an operator before the tool runs

```yaml
spec:
  patch:
    governance:
      $op:
        add:
          - sample_governance:
              hitl_on_tool: true
              hitl_on_tool_result: false
              hitl_once_per_turn: false
              hitl_mode: interactive
```

`hitl_on_tool: true` asks a human before every tool call.

```bash
mas-ctl chat docs/tutorials/14-governance-hitl/agent.yaml \
  -o docs/tutorials/14-governance-hitl/overlays/tools.yaml \
  -o docs/tutorials/14-governance-hitl/overlays/governance-interactive.yaml \
  --trace -i
```

At `You:`, enter `A workshop costs 125 dollars per person for 3 people, plus 42
dollars for materials. Calculate the total.` When the agent requests `calc`, the
prompt changes from `You:` to `HITL>` and shows the proposed operation.
Enter `ALLOW`: the tool runs and the agent answers `$417`.

Run the same command again and enter `BLOCK` at `HITL>`. The calculator does
not run, and the turn ends. The user-visible answer is the operator denial
(`Operator blocked tool 'calc' via HITL.`), not a model-computed `417`. Optional
steering is appended to that denial. The model does not get another call after
a BLOCK, so it cannot substitute the result. `SKIP` is different: it still
skips the tool, injects optional steering as the tool result, and lets the
model continue (for example to ask a question).

For the curses terminal interface, run the same manifest and overlays with
`mas-ctl tui`. It shows the same `HITL>` decision.

```bash
mas-ctl tui docs/tutorials/14-governance-hitl/agent.yaml \
  -o docs/tutorials/14-governance-hitl/overlays/tools.yaml \
  -o docs/tutorials/14-governance-hitl/overlays/governance-interactive.yaml
```

## Step 4 — Review results too

To review a tool result before the model sees it, set
`hitl_on_tool_result: true`. One tool use then pauses twice: before the tool
runs and after it returns. This tutorial leaves it off.

## Step 5 — Use explicit decisions in batch and CI

A job with no terminal must state how HITL requests are resolved. These
overlays use the same rule with a different `hitl_mode`. Do not use the
interactive overlay without a terminal.

```bash
# auto-approve: exercises the full tool path
mas-ctl chat docs/tutorials/14-governance-hitl/agent.yaml \
  -o docs/tutorials/14-governance-hitl/overlays/tools.yaml \
  -o docs/tutorials/14-governance-hitl/overlays/governance-auto-approve.yaml \
  -q "Calculate 125 * 3 + 42."

# auto-deny: proves a protected operation cannot run silently
mas-ctl chat docs/tutorials/14-governance-hitl/agent.yaml \
  -o docs/tutorials/14-governance-hitl/overlays/tools.yaml \
  -o docs/tutorials/14-governance-hitl/overlays/governance-auto-deny.yaml \
  -q "Calculate 125 * 3 + 42."
```

With auto-approve you should see `$417`. With auto-deny the calculator does not
run (`tool_call_end` for `calc` is absent) and the answer is the operator
denial, not `417`:

```bash
mas-ctl chat docs/tutorials/14-governance-hitl/agent.yaml \
  -o docs/tutorials/14-governance-hitl/overlays/tools.yaml \
  -o docs/tutorials/14-governance-hitl/overlays/governance-auto-deny.yaml \
  --trace -q "Calculate 125 * 3 + 42."
```

Repeat runs can return from the LLM response cache. Pass `-v` to log a cache
hit. Disable with `--no-cache-read` or `MAS_LLM_CACHE_READ=false` (a cached
ALLOW can replay `417` after a denial). See
[Tutorial 0](../00-environment-setup/README.md) and
[llm-cache.md](../../manifests/llm-cache.md).

## Step 6 — Inspect the evidence in the trace

`--trace` prints a readable exchange log on stderr. The machine record is
`traces/events.jsonl`, written beside the agent manifest on every chat run
(see [Tutorial 1, step 7](../01-building-an-agent/README.md#step-7-inspecting-the-trace)).

```bash
grep -E 'governance|hitl' \
  docs/tutorials/14-governance-hitl/traces/events.jsonl
```

The trace records the proposed operation, the rule, the decision and the
reason. `hitl_resolve` includes `resolution`, `operator_id` (who decided),
and `steering` when the operator supplied text. You can tell which tool was
tried, whether approval came before or after, who resolved it, and whether
the tool ran.

## Step 7 — Let the agent ask when information is missing

| Situation | Use |
| --- | --- |
| Every matching operation must be reviewed | A governance rule such as `hitl_on_tool` |
| The agent cannot continue without a user choice | The `request_human_input` system tool |

`agent-asks-user.yaml` gives the model `request_human_input` and tells it to
confirm totals over $400:

```bash
mas-ctl chat docs/tutorials/14-governance-hitl/agent.yaml \
  -o docs/tutorials/14-governance-hitl/overlays/tools.yaml \
  -o docs/tutorials/14-governance-hitl/overlays/agent-asks-user.yaml \
  -i
```

Enter `A workshop costs 125 dollars per person for 3 people, plus 42 dollars
for materials. Calculate the total.` The agent calculates, then asks whether it may
accept the total. Answer `ALLOW` or `BLOCK` at `HITL>`. Use governance for
mandatory control and `request_human_input` for missing preferences.

## Step 8 — Know the possible decisions

| Decision | Effect |
| --- | --- |
| `ALLOW` | Continue normally |
| `LOG` | Record the decision and continue |
| `HITL` | Pause and request a human or configured automated response |
| `BLOCK` | Refuse the operation and end the turn with that denial (optional steering is appended) |
| `SKIP` | Skip the operation and provide a controlled result (optional steering text becomes the tool result; the model continues) |
| `RETRY` | Ask for another attempt |
| `MODIFY` | Replace approved parts of the proposed operation |
| `TERMINATE` | End the session |
| `BLACKLIST` | Prevent further use of the selected tool in the session |

You can write your own rule. This one allows only `approved-search`:

```python
from mas.runtime.boundary.gov.plugin import EgressDecision
from mas.runtime.boundary.gov.policy import EgressIntentView
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.coupling import GovDecision


class ReadOnlyToolsOnly:
    def evaluate_egress(
        self, intent: EgressIntentView, *, config: KernelConfig
    ) -> EgressDecision:
        if intent.op == "TOOL_CALL" and intent.tool_name != "approved-search":
            return (
                GovDecision.BLOCK,
                "read-only-tools",
                "only approved-search is allowed",
            )
        return GovDecision.ALLOW, "read-only-tools", "operation is allowed"
```

For production, use the typed
[`GovernancePlugin` contract](https://github.com/ioc-project/mas-lab/blob/main/runtime/src/mas/runtime/boundary/gov/plugin.py)
and register the plugin in a library. Rules run in listed order. `ALLOW`
passes to the next rule, and `BLOCK` stops at once.

## What you learned

1. Governance rules, not prompts, decide whether a tool runs.
   `hitl_on_tool` pauses before the call and `hitl_on_tool_result` before the
   model sees the result.
2. Batch and CI runs need an explicit `auto-approve` or `auto-deny` overlay.
   `BLOCK` / auto-deny stops the tool and ends the turn with the operator
   denial. `SKIP` with steering lets the model continue from that text. The
   decision, operator, and steering land in `traces/events.jsonl`.
3. Each layer is a separate file, so control changes without editing
   `agent.yaml`.

## Troubleshooting

- **No HITL prompt.** Check that both the tool and governance overlays and
  `-i` are present. The prompt appears only after the model asks for a tool.
- **A non-interactive command blocks or exits.** Use a Step 5 overlay.
- **You still see 417 after a denial.** `BLOCK` / auto-deny ends the turn
  with the operator denial. `417` means the call was allowed, you used
  `SKIP`, or a cached allow was replayed. Use `--no-cache-read`.
- **A repeat run returned in tens of milliseconds.** The LLM response cache is
  on by default. Use `--no-cache-read` or `MAS_LLM_CACHE_READ=false`.
- **Trace file missing.** Pass `--events --events-file PATH` to choose a
  location, and do not pass `--no-events`.

## Next

Continue with the labs in the [tutorial index](../index.md#whats-next). See the
[plugin bindings reference](../../manifests/plugin-bindings.md) and the
[governance plugin guide](https://github.com/ioc-project/mas-lab/blob/main/library-standard/src/mas/library/standard/plugins/governance/README.md).
