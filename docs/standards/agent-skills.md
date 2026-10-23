<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Agent Skills

<p class="eyebrow">Open standards</p>

A skill is a directory of instructions, references, and optional scripts that
an agent loads on demand. MAS-Lab skills follow the
[agentskills.io](https://agentskills.io/) specification. The required
`SKILL.md` has a short name and description, then detailed instructions that
load only when the agent picks the skill.

## How MAS-Lab implements it

`mas-library-skills` ships a ContextContract plugin and skill-access tools.
The default engine is a bundled `agentskills` parser and sandbox
(`src/agentskills` in that package). Optional extras wrap Google ADK and
Deep Agents / LangChain; they are opt-in because those frameworks pin their
own OpenTelemetry versions.

You attach a skill with an overlay. The agent manifest does not copy the
instructions:

```yaml
apiVersion: mas/v1
kind: Overlay
metadata:
  name: with-skills
spec:
  target:
    kind: Agent
  patch:
    spec:
      skills:
        - name: answer-expert
          path: skills/answer-expert
```

## Dependencies

| Piece | Package |
| --- | --- |
| Plugin, parser, sandbox | `mas-library-skills` |
| Specification | [agentskills.io](https://agentskills.io/) |
| Optional engines | extras `adk` (`google-adk`) and `langchain` (`deepagents`) |

## Compliance

Skills are valid when `SKILL.md` meets the agentskills.io front matter and
layout. MAS-Lab does not run a third-party skills TCK. Tutorial 6's
`answer-expert` tree is the in-repo fixture:

```text
docs/tutorials/06-agent-skills/skills/answer-expert/
├── SKILL.md
├── references/physical-constants.md
└── scripts/convert_speed.py
```

## Related

- [Tutorial 6 — Agent skills](../tutorials/06-agent-skills/README.md)
- [Agent manifest](../manifests/agent.md) (`spec.skills`)
- [Package map](../libraries.md)
