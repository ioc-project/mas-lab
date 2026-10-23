<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Tutorial 6 — Teaching an Agent a Reusable Skill

You give a general-purpose agent a reusable skill. A skill is a directory of instructions, references, and scripts
that an agent loads on demand. The agent then follows a fixed answer procedure, reads a reference, and runs a checked
calculation, with no change to the agent itself.

## What you need

- **Packages:** `mas-ctl` and `mas-library-skills` (bundles the `agentskills` parser and the skill sandbox).
- **Prerequisite:** [Tutorial 0](../00-environment-setup/README.md) (install, LLM access). [Tutorial 1](../01-building-an-agent/README.md) helps but is optional.
- About 35 minutes. Run every command from the repository root. Only `mas-ctl chat` calls an LLM (it needs `OPENAI_API_KEY`). Validate and the script run offline.

## The problem

Several agents must answer science questions the same way: direct answer first, an authoritative value with units,
useful conversions, and a confidence statement. Copying these rules into every prompt duplicates them and loads them
for unrelated questions. Instead, you add an `answer-expert` skill as a separate directory.
Use a skill when agents share instructions or files, or need them only for some tasks.

A skill follows the [agentskills.io specification](https://agentskills.io/). Its required `SKILL.md` has a short
name and description, then detailed instructions that load only when the agent picks the skill.
This tutorial's example is complete:

```text
docs/tutorials/06-agent-skills/
├── agent.yaml
├── overlays/with-skills.yaml
└── skills/answer-expert/
    ├── SKILL.md
    ├── references/physical-constants.md
    └── scripts/convert_speed.py
```

## Step 1 — Run the agent without the skill

The starting agent is ordinary on purpose:

```yaml
apiVersion: mas/v1
kind: Agent
metadata:
  name: skill-tutorial-agent
spec:
  description: "Answer general knowledge questions."
  context:
    role: |
      Answer the user's question clearly. Do not invent facts that you cannot
      support.
```

```bash
mas-ctl validate docs/tutorials/06-agent-skills/agent.yaml
mas-ctl chat docs/tutorials/06-agent-skills/agent.yaml \
  -q "What is the speed of light in kilometres and miles per second?"
```

`validate` prints `OK ... kind=agent`. The answer may be correct, but its format, sources, and confidence statement depend on the model. This is your baseline.

## Step 2 — Describe when the skill applies

`skills/answer-expert/SKILL.md` starts with YAML frontmatter, the skill's catalog entry:

```yaml
---
name: answer-expert
description: >
  Use when answering factual science questions that require a direct answer,
  supporting evidence, unit conversions, and an explicit confidence statement.
tags: [science, factual-answers, verification]
---
```

At session start the model sees only this entry, so the description must say when to use the skill.
The Markdown below holds the procedure. The model sees it only after calling `activate_skill("answer-expert")`:

```markdown
1. Load the relevant file from `references/` before answering.
2. Use `run_skill_script` when the question asks for a conversion supported by
   a script in this skill.
3. Start with a one-sentence direct answer.
4. Follow with the source value, assumptions, and conversions.
5. End with `Confidence: HIGH`, `MEDIUM`, or `LOW` and one short reason.
```

## Step 3 — Add a reference and a calculation

`references/physical-constants.md` holds the exact speed of light in vacuum and when it applies.
`scripts/convert_speed.py` converts metres per second to kilometres and miles per second. Run it directly:

```bash
python docs/tutorials/06-agent-skills/skills/answer-expert/scripts/convert_speed.py \
  299792458
```

```json
{"kilometres_per_second": "299792.458", "miles_per_second": "186282.397"}
```

Through `run_skill_script`, MAS Lab limits the script's time and memory, removes sensitive environment variables,
and blocks reads outside the skill directory. A skill script is still code, so review it before you add the skill.

## Step 4 — Attach the skill with an overlay

The overlay adds one entry to the agent's `skills` list. `$op: add` keeps skills the agent already declares:

```yaml
apiVersion: mas/v1
kind: Overlay
metadata:
  name: with-answer-expert
spec:
  target:
    kind: Agent
  patch:
    skills:
      $op:
        add:
          - answer-expert
```

```bash
mas-ctl validate docs/tutorials/06-agent-skills/agent.yaml \
  -o docs/tutorials/06-agent-skills/overlays/with-skills.yaml
```

You should see `OK` for the agent and the overlay.

## Step 5 — Run the complete path

```bash
mas-ctl chat docs/tutorials/06-agent-skills/agent.yaml \
  -o docs/tutorials/06-agent-skills/overlays/with-skills.yaml \
  --trace \
  -q "What is the speed of light in kilometres and miles per second? Use the answer-expert skill."
```

The wording varies. The answer should contain the exact value in m/s from the reference, the converted values from the script,
the condition "in vacuum", and a confidence statement. The model decides whether to use a skill, so name it in the request.

## Step 6 — Check what loads, and when

This is progressive disclosure: the model loads details only when needed.

| Moment | What the agent receives |
| --- | --- |
| Session starts | Skill name and description |
| Skill is selected | Full `SKILL.md` instructions |
| A source is needed | One file from `references/` |
| A calculation is needed | Output from one script |

| Runtime tool | Purpose |
| --- | --- |
| `activate_skill` | Load `SKILL.md` instructions |
| `list_skill_files` | List references, scripts, and assets |
| `read_skill_file` | Read one selected file |
| `run_skill_script` | Run one script from `scripts/` |

After the Step 5 run, `mas-ctl chat` has written `events.jsonl` to `traces/` beside the manifest. Confirm the calls (expect lines for each tool the model used):

```bash
grep -E 'activate_skill|read_skill_file|run_skill_script' \
  docs/tutorials/06-agent-skills/traces/events.jsonl
```

## Step 7 — Reuse or replace the implementation

The default is MAS Lab's native skills support, which runs scripts with `skill-sandbox`.
Adapters for Google ADK and LangChain use the same skill directory but need optional extras; this tutorial does not use them.
Use the native implementation unless your application already depends on another framework.

## What you learned

- A skill separates reusable expertise from the agent. An overlay attaches it with `$op: add`.
- Progressive disclosure loads the catalog entry first, then instructions, references, and script output on demand.
- `events.jsonl` shows which skill tools the agent called.

| Concern | File |
| --- | --- |
| What the agent does | `agent.yaml` |
| Which skill it may use | `overlays/with-skills.yaml` |
| How to answer, what to read, what to run | `skills/answer-expert/` |

## Next

[Tutorial 8 — Telemetry](../08-telemetry/README.md) exports and replays these traces.
[Tutorial 14](../14-governance-hitl/README.md) applies the same separation to control: which actions need human approval.

## Troubleshooting

- **Skill not found.** Run from the repository root, keep `skills/` beside `agent.yaml`, and match the frontmatter `name` to the overlay.
- **Answer ignores the reference or script.** Name `answer-expert` in the request, then check the trace for the three tool calls.
- **Script cannot run.** Run it directly with the Step 3 command. Scripts must live in `scripts/`.

See the [skills user guide](https://github.com/ioc-project/mas-lab/blob/main/library-skills/docs/user-guide.md) and [agentskills.io](https://agentskills.io/).
