<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Tutorial 2 — Creating a Multi-Agent System

> **Packages:** `mas-ctl` (run-mas, validate), `mas-lab` (telemetry/plots)
> **Deployment:** `deployments/local-inproc.yaml` with `runtime_id: mas-runtime-py`
> **Time:** ~30 min

## Goal

You compose a multi-agent system (MAS) from a `kind: MAS` manifest and run it.
The example is a trip planner: a moderator delegates to three specialists in the fictional "Arborian Network".

## What you need

- [Tutorial 0](../00-environment-setup/README.md) (config and infra) and [Tutorial 1](../01-building-an-agent/README.md) (agent manifests, overlays).
- A terminal in `docs/tutorials/02-creating-a-mas`.
- An LLM API key for Steps 5 and 6. Validate, compile, telemetry, and plots run offline.

`mas-ctl run-mas` uses the same `default_infra` / `infra_refs` as `mas-ctl chat`.

```
user ──▶ moderator ──delegates_to──▶ schedule_agent   (schedules + attractions)
                                 ├─▶ itinerary_agent  (route plan)
                                 └─▶ concierge_agent  (fares + costs)
```

## Step 1 — Read the specialists

Each specialist is a `kind: Agent` manifest (Tutorial 1) with its own tools and skills.
The files in `agents/` also pin the model (`gpt-4o`), temperature, and token limits.

```yaml
# agents/schedule-agent/agent.yaml (abridged)
metadata:
  name: schedule_agent
spec:
  description: "Transport schedule and attractions specialist."
  context:
    role: |
      You can look up train and airplane schedules and attractions inside cities. ...
  tools:
    - ref: ../../tools/lookup_schedule.tool.yaml
  skills:
    - transport-schedule-lookup
```

| Agent | Tools | Skill |
|-------|-------|-------|
| `schedule_agent` | `lookup_schedule` | `transport-schedule-lookup` |
| `itinerary_agent` | `query_graph_database` | `route-planning` |
| `concierge_agent` | `get_fares`, `calc` | `fare-and-itinerary-assembly` |

Full manifests: [schedule](agents/schedule-agent/agent.yaml), [itinerary](agents/itinerary-agent/agent.yaml), [concierge](agents/concierge-agent/agent.yaml).
Tools live in `tools/` (Python plus a `*.tool.yaml` descriptor). Skills are `SKILL.md` files in `skills/`
that the agent loads on demand ([Tutorial 6](../06-agent-skills/README.md)).

## Step 2 — Read the moderator

The moderator is the entry point. It has no domain tools. The runtime injects delegation tools instead.

```yaml
# agents/moderator/agent.yaml (abridged)
spec:
  context:
    role: |
      You are a moderator coordinating a team of specialized agents. ...
      Call only one agent at a time. Make sure you use ALL agents. ...
  skills:
    - trip-orchestration
```

Full manifest: [agents/moderator/agent.yaml](agents/moderator/agent.yaml).

## Step 3 — Read the MAS manifest

`spec.agency.agents` lists agent identities (`id` plus `ref`). `spec.workflow` holds the topology.
Each agent keeps its own tools and skills, so it can be validated and reused without the MAS.

```yaml
# mas.yaml (abridged)
kind: MAS
metadata: {name: trip-planner, version: "0.1.0"}
spec:
  agency:
    agents:
      - {id: moderator,       ref: agents/moderator/agent.yaml}
      - {id: schedule_agent,  ref: agents/schedule-agent/agent.yaml}
      # itinerary_agent, concierge_agent likewise
  workflow:
    entry: moderator
    nodes:
      - id: moderator
        delegates_to: [ schedule_agent, itinerary_agent, concierge_agent ]
      - id: schedule_agent
      # ...
```

Full manifest: [mas.yaml](mas.yaml). Overlays: [single-agent](overlays/single-agent.yaml), [linear](overlays/linear.yaml).

The schema has no `workflow.type`. The moderator's LLM chooses which allowed specialist to call, and in what order.
Each delegation is a tool call, `delegate_to_<agent_id>`, with a `task` argument, sent over the in-process bus (CommBus).

### Topology overlays

An overlay changes participants and topology without touching agent files.

| Topology | Configuration | Who decides order? |
|----------|---------------|--------------------|
| LLM-directed | Entry node lists `delegates_to` | The entry agent's LLM |
| Single-agent | One participant, one workflow node | No delegation |
| Deterministic linear | Entry `design_pattern: deterministic_linear` plus ordered `delegates_to` | The design-pattern plugin |

```yaml
# overlays/single-agent.yaml (patch only; see overlays/linear.yaml for the linear one)
spec:
  target: {kind: MAS}
  patch:
    agents:
      $op:
        replace:
          - {id: generalist, ref: agents/generalist/agent.yaml}
    workflow: {entry: generalist, nodes: [{id: generalist}]}
```

`overlays/linear.yaml` keeps the roster and adds this to the patch:

```yaml
    agents:
      moderator:
        design_pattern: {type: deterministic_linear}
```

An experiment scenario selects an overlay by name (file name without `.yaml`, equal to its `metadata.name`).
You write one in [Tutorial 3](../03-experiments-and-analysis/README.md):

```yaml
scenarios:
  - id: linear
    overlays: {logic: [linear], control: [], infra: []}
```

## Step 4 — Check the flavour

A flavour is a named runtime environment (Tutorial 1). This tutorial uses the bundled `local` flavour
from `mas-library-standard` (`mas-ctl flavour list`). It runs all agents in-process (`agent_comm.protocol: agent-local`).

`config.yaml` here selects it (`mas_ctl.flavour: local`) and sets `mas_ctl.trace: summary`,
so `run-mas` prints each delegation on stderr. The LLM endpoint comes from your Tutorial 0 infra.

## Step 5 — Run the MAS

```bash
# Default topology (LLM-directed): the moderator delegates dynamically
mas-ctl run-mas mas.yaml -q "Plan a 3-day trip to Thornhaven, budget €500"

# Interactive multi-turn
mas-ctl run-mas mas.yaml -i

# Single-agent topology: one generalist, no delegation
mas-ctl run-mas mas.yaml -o overlays/single-agent.yaml -q "Plan a 3-day trip to Thornhaven, budget €500"

# Deterministic-linear: specialists run in the declared order
mas-ctl run-mas mas.yaml -o overlays/linear.yaml -q "Plan a 3-day trip to Thornhaven, budget €500"
```

To inspect the resolved MAS, compile it (use `--layout bundle` for one file):

```bash
mas-ctl compile mas.yaml -o overlays/linear.yaml -O ./compiled-linear/
```

In the default topology the moderator typically calls `schedule_agent`, `itinerary_agent`, `concierge_agent`,
then assembles the itinerary with prices. Order and wording are the LLM's choice and vary between runs.

## Step 6 — Read the event stream

`mas-ctl run-mas` records `events.jsonl`, one JSON event per line, at `traces/events.jsonl` by default.
`--events --events-file PATH` chooses another path:

```bash
mas-ctl run-mas mas.yaml --events --events-file logs/events.jsonl \
  -q "Plan a 3-day trip to Thornhaven, budget €500"
```

Delegation shows up as `tool_call_*` events on `delegate_to_<agent_id>`, with the specialist's own tool calls in between.
Real lines carry more fields, such as `call_id`, `session_id`, and `timestamp`:

```json
{"kind": "tool_call_start", "agent_id": "moderator", "tool_name": "delegate_to_schedule_agent", "arguments": {"task": "…"}, …}
{"kind": "tool_call_start", "agent_id": "schedule_agent", "tool_name": "lookup_schedule", …}
{"kind": "tool_call_end", "agent_id": "schedule_agent", "tool_name": "lookup_schedule", …}
{"kind": "tool_call_end", "agent_id": "moderator", "tool_name": "delegate_to_schedule_agent", …}
```

Inspect the trace:

```bash
mas-lab telemetry show logs/events.jsonl     # event count, agents, kinds
mas-lab plot multilevel-trajectory logs/events.jsonl --format html -o output/trajectory.html
```

`telemetry show` prints the event count, the agents, and a breakdown by event kind.
`plot` writes an HTML trajectory with delegation arrows.
Without an API key, run both on `fixtures/events.jsonl`, a pre-recorded single-agent trace (15 events, no delegation).
Steps 5 and 6 need `OPENAI_API_KEY` (or a cache replay from Tutorial 0). Without it, `run-mas` stops with a "Live LLM configured but API key unset" error.

## Going further

- **A2A:** point an agent id at a remote peer with `--bind`. The MAS manifest does not change.
  [Tutorial 5](../05-a2a-agents/README.md) covers serving and mixed topologies.

  To expose a specialist as a server instead, run `mas-ctl serve docs/tutorials/02-creating-a-mas/agents/schedule-agent/agent.yaml --infra-ref library-samples/infra/mixed-agents.infra.yaml` from the repo root.

  ```bash
  mas-ctl run-mas mas.yaml --bind schedule_agent=a2a://127.0.0.1:9006/ -q "Plan a 3-day trip to Thornhaven, budget €500"
  ```

- **Design-space lab:** `labs/design-space.lab/` extends the trip planner with more patterns, topologies, and datasets.
- **CI:** `demo/scenario.yaml` lists each step's commands and expected exit codes.
  Run `pytest tests/tutorials/test_scenario_commands.py -v -k tuto-02`. Live steps need `TUTORIAL_ONLINE=1` and credentials.

## What you learned

- A `kind: MAS` manifest separates agent identity (`agency`) from topology (`workflow`). Each agent keeps its own tools and skills.
- Overlays change participants and delegation without touching agent files. `mas-ctl compile` shows the result.
- `events.jsonl` records delegation as `delegate_to_<id>` tool calls.

See also the [glossary](../../glossary.md) and the [user guide](../../user-guide.md).

## Next

[Tutorial 3 — Experiments, Analysis and Evaluation](../03-experiments-and-analysis/README.md) runs benchmarks and compares topologies.
