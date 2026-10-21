<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Tutorial 12 — Spawned subagents

> **Packages:** `mas-runtime`, `mas-ctl`
> **Prerequisite:** [Tutorial 0](../00-environment-setup/README.md) and
> [Tutorial 1](../01-building-an-agent/README.md). Run all commands from the
> repository root.
> **Files:** [`agent.yaml`](agent.yaml) (parent) and
> [`reviewer.yaml`](reviewer.yaml) (the template).

## Goal

A parent agent can ask a temporary specialist to do one bounded task, such as
reviewing a change. The specialist is a pre-authored Agent manifest, called a
template.

The parent cannot create tools or prompts at runtime. It can only pick from
the templates its manifest declares.

## Step 1 — Declare a template

```yaml
spec:
  tools:
    - kind: system
      name: spawn_subagent
      params:
        templates:
          - id: reviewer
            ref: ./reviewer.yaml
            description: Check a proposed change for defects.
        max_spawns: 8
        max_depth: 3
```

`reviewer.yaml` is a normal Agent manifest with the child's prompt, model,
tools and skills. The parent LLM chooses the template and writes the task.
`max_spawns` is the total number of children. `max_depth` is how deep
children can spawn children.

## Step 2 — Run and inspect

```bash
mas-ctl validate docs/tutorials/12-subagents/agent.yaml
mas-ctl chat docs/tutorials/12-subagents/agent.yaml \
  -q "Ask the reviewer to check this change: def add(a, b): return a + b"
```

Validation prints `OK`. In chat you should see the parent call
`spawn_subagent`, then reply with the reviewer's findings. Calling the tool is
the model's choice, and the answer wording varies. If the parent answers
itself, name the reviewer in the request, as above. Add `--trace` to see the call and its result as a separate
exchange. The reviewer runs as its own instance for this one call.

## Step 3 — How dispatch works

- `SpawnSubagentTool` (`mas-library-standard`) only advertises
  `spawn_subagent`, with a schema built from the template ids. It fails
  closed if executed.
- `SubagentSpawner` (`engine_tools/subagent_spawner.py`) builds the child
  from the template, runs one turn and removes the instance, even when the
  turn fails. The child's final text is the tool result. Child calls join the
  parent's observability sink and keep the parent call's identity.
- `load_subagent_templates()` (`ctl/src/mas/ctl/executor/subagent_spawner.py`)
  resolves each `ref:` under the parent manifest's directory and validates it
  against the Agent schema. A bad or outside path is rejected before the
  model can call it.
- `SpawnLedger` enforces spawn count and depth, across the whole tree and
  across siblings spawned in one turn (for example through `asyncio.gather`);
  each child gets its own working-memory registry. A child can spawn only if
  its own manifest allows it.

## What you learned

1. `spawn_subagent` plus a template list gives the parent a bounded "ask a
   specialist" capability with no tool code.
2. A template is an ordinary Agent manifest.
3. The runtime, not the model, enforces budgets and template paths.

## Next

[Tutorial 13 — Control attach and debug](../13-control-and-debug/README.md).
Reference: [Subagent manifests](../../manifests/subagents.md) and
[Schema examples](../../references/schemas.md).
