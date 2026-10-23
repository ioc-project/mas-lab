---
date: 2026-09-30
slug: v0-2-release
authors:
  - mas.team
categories:
  - Releases
---

# MAS Lab v0.2: Skills, Tools, and Agents That Can Work Together

MAS Lab is an open-source foundation for building multi-agent systems the way
you'd build any other production software: from a declarative specification,
with a runtime that enforces it, and with experiments and traces that show
what actually happened. v0.1 gave us a way to specify, run, and test agent
logic. v0.2 connects that same specification to the rest of the agentic
world — skills, tools, and other agents — without rewriting the agent to do
it.

<!-- more -->

## One system, from experiment to operation

MAS Lab is not another agent framework or test harness. It is a continuity
architecture: one versioned specification gives the system an identity across
design, experiments, execution, and operations. The spec declares roles, tools,
delegation, and policies; the runtime applies those boundaries; isolated plugins
provide capabilities without burying them in agent logic. Traces and results
then refer back to the system that was actually declared and run.

In the lab, we can vary an agent's skills, models, context, or topology and
retain comparable runs. The [paper labs](../../../paper/index.md) show what
reproducible artifacts look like: rerunnable experiments that regenerate their
figures and tables. In production, we can deploy only the capabilities needed,
while keeping observability and governance part of the same contract. None of
this guarantees that a model's answer is correct. It shifts the cost of
building reliable agents from reconstructing what happened after a failure
toward specifying, testing, and observing behavior from the start. v0.2
carries that continuity through to the protocols agents actually use to reach
skills, tools, and each other.

## Skills without coupling them to an agent

We already had skills in v0.1. In v0.2, `library-skills` expands that support
around the agentskills.io format: discover `SKILL.md` by name, disclose the
instructions on demand, load accompanying resources, and run embedded scripts.
Skills can add expertise or executable capabilities through an overlay without
changing the base agent specification. The same skill directory remains portable
to other harnesses that support agentskills.io; it is not tied to MAS Lab's
runtime or to a copy of the agent prompt.

## Tools and peers across protocols

MCP (Model Context Protocol) serves tools from separate processes. The agent
still asks for the same logical tool. As
[Tutorial 4](../../../tutorials/04-mcp-tools/README.md) shows, moving from a
local experiment to an MCP provider is an infrastructure switch: set
`protocol: mcp` in a `ToolServerRegistry`, then specify any non-default
transport settings. To use an existing remote MCP server, provide an infra
manifest with its address and any required access details. Discovery and routing
stay behind the tool contract, so the agent does not change; the tutorial even
combines a local calculator and remote web search in one turn.

<!-- markdownlint-disable MD033 -->
<figure class="mas-blog-figure mas-blog-figure--wide">
  <a href="../../../../../blog/2026-09-30-v0-2-release/mcp-tool-contract.svg">
    <img src="../../../../../blog/2026-09-30-v0-2-release/mcp-tool-contract.svg" alt="The same agent and tool contract dispatch locally or to an MCP server through different infrastructure adapters." />
  </a>
  <figcaption><em>Fig. 1. The agent keeps its tool contract; infrastructure switches the execution path to MCP.</em></figcaption>
</figure>

A2A (Agent2Agent) applies the same idea to delegation: select `protocol: a2a` for an agent
endpoint and supply its URL and any other non-default settings. You can expose
an existing agent with `mas-ctl serve` and an infra `Application` endpoint that
uses `protocol: a2a`. Or the MAS can connect to a remote peer by declaring its
endpoint. The agent ids, workflow, and delegation contract stay unchanged. [Tutorial 5](../../../tutorials/05-a2a-agents/README.md)
demonstrates a mixed local/remote team; a single application can also use MCP
tools and A2A peers together.

<figure class="mas-blog-figure mas-blog-figure--wide">
  <a href="../../../../../blog/2026-09-30-v0-2-release/a2a-agent-contract.svg">
    <img src="../../../../../blog/2026-09-30-v0-2-release/a2a-agent-contract.svg" alt="The same MAS delegation contract targets a local agent or a remote A2A peer according to infrastructure configuration." />
  </a>
  <figcaption><em>Fig. 2. The delegation target stays the same; infrastructure routes it to an A2A peer.</em></figcaption>
</figure>
<!-- markdownlint-enable MD033 -->

The adapters are measured against official conformance suites, rather than
asking each application to implement protocol details by hand. The v0.2 results
cover 98.3% of required checks for the MCP 2026-07-28 stateless revision and
99/99 executed A2A requirements; the A2A TCK exercises 79.8% of the
specification. These results do not amount to blanket protocol certification.

## Built with real workloads

Beyond protocol integration, v0.2 grew from our research and prototypes within
Outshift and contributions from teams using MAS Lab. It also brings
[new features, improvements, and bug fixes](https://github.com/ioc-project/mas-lab/blob/v0.2.0/CHANGELOG.md).

An example of MAS Lab in use is the published
[study of silent cognitive failures](https://outshift.cisco.com/blog/ai-ml/cognitive-challenges-in-multi-agent-systems):
agents can skip verification or agree on the wrong answer while ordinary
operational signals stay green. MAS Lab helped turn that observation into
repeatable research. We defined an SRE triage use case under two coordination
patterns, varied specific failure conditions through overlays, and automated
repeated runs and trace processing to compare outcomes and diagnostic metrics.
The same experiment machinery lets us scale beyond a handful of anecdotes,
and further artifacts from that work are on the way.

This also supports our ongoing research on the
[Internet of Cognition (IoC)](https://outshift.cisco.com/internet-of-cognition):
how agents share intent and context, reason together, and detect cognitive
failures before a team commits to the wrong result. MAS Lab gives us a way to
reproduce and measure those failures. Cognitive observability means looking at
what agents represented, checked, and committed to across their interactions,
not only whether requests succeeded or latency stayed low.

Observability and governance are first-class concerns that we will keep
strengthening in future releases.

## What comes next

Keeping behavior separate from transport makes integrations easier to test and
reuse; we plan to extend this approach to more protocols in the next releases.
It reflects why we participate in the
[Agentic AI Foundation](https://jordan.auge.synaxe.net/news/2026-07-22-agntcon-mcpcon-mas-lab/)
(AAIF): standards make systems interoperable, while a shared specification and
reproducible evidence let us ask whether they behaved as intended. We want to
build reliability in from the design phase: make contracts explicit, test
isolated plugins, and catch structural errors before deployment. That reserves
the more expensive runtime checks for what cannot be prevented earlier,
including silent failures in production that require cognitive observability.
This is both an accuracy goal and a way to reduce the cost of verification.

We presented MAS Lab and its protocol extensions at
[AGNTCon Europe](https://agntconmcpconeu26.sched.com/event/2RB9i/mas-lab-an-open-framework-for-spec-driven-interoperable-multi-agent-systems-jordan-auge-cisco-systems)
(slides linked from the session page; recording available online). Read the
[v0.2 release](https://github.com/ioc-project/mas-lab/releases/tag/v0.2.0)
for the full notes and source archives, follow
[Tutorial 0](../../../tutorials/00-environment-setup/README.md) to try it, or
explore the
[repository](https://github.com/ioc-project/mas-lab) and its
[contribution guide](https://github.com/ioc-project/mas-lab/blob/main/CONTRIBUTING.md).
Issues, feedback, and contributions are welcome.
