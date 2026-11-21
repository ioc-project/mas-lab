<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Agent runner

An agent runner takes a task and returns a reply. That is the whole interface.

MAS Lab runs an agent by its manifest through one runner today: the kernel main loop, a Mealy-style
state machine that calls a model and tools, with governance hooks around each call. A second runner
lets the same agent be run by something else, such as another team's agent harness. The listener,
the identity layer, the Controls and the events stay MAS Lab's. Only the part that does the work
changes.

Two things follow:

- **Front a harness.** A command-line or A2A agent built elsewhere is served as a MAS Lab agent:
  an A2A listener with an agent card, admission checks at the boundary and an audit trail.
- **Drive a harness in an experiment.** The lab runs its prompts against that harness and collects
  the same events file it collects for a kernel agent.

Nothing changes unless a manifest names a runner. An agent with no `spec.runner` runs on the kernel
loop, byte for byte as before (see [Proof that the default did not move](#proof-that-the-default-did-not-move)).

## How an agent is run today

Every route ends in the same two calls, `RuntimeInstance.run_user_text` or `arun_user_text`, which
feed a `UserInputReceived` symbol to the kernel driver.

| Route | Code | What it does |
| --- | --- | --- |
| Build the agent | `mas.ctl.session.bootstrap.instantiate_runtime`, `MasRuntimePyKernelBackend.create_agent_runtime` | Reads the manifest, applies the Controls, builds the engine and context, and returns a `RuntimeInstance` (kernel plus driver plus observability). |
| Chat, run-mas, lab | `SessionController._arun_user_turn` | One turn: working memory in, `arun_user_text`, HITL drain, working memory out. |
| Serve over A2A | `mas.ctl.session.exposure.make_runtime_handler` and `A2AExposure` | The A2A SDK calls `MasLabAgentExecutor`, which calls a plain handler `(prompt, turn_id, session_id, ...)`. The handler queues the task onto the conversation's session (one runtime per `contextId`) and runs `SessionController.run_turn`. |
| Delegate to a peer | `SendTool`, `make_workflow_send`, the `agent_comm` stack | A peer send is a call on the comm stack; the peer's side is one of the routes above. |
| Lab experiment | `MasBenchRunner.run` | Resolves the manifest and its overlays, builds the instance, runs the scripted queries through `SessionController`, writes `traces/events.jsonl`. |
| `mas-mcp` | `library_ioa.plugins.mcp.server` | Serves tool manifests as an MCP tool server. It does not run an agent, so it is outside this seam. |

The A2A route already had a small seam. `MasLabAgentExecutor` knows nothing about the kernel: it
calls a handler and turns the answer into A2A events. The handler is where the kernel enters. The
runner interface names that seam and makes it a plugin type.

## The seam

```python
class AgentRunner(Protocol):
    runner_id: str                              # the plugin id, hyphenated
    def run(self, task: RunnerTask) -> RunnerReply: ...
    def close(self) -> None: ...
```

It lives in `mas.runtime.boundary.runner` and is under eighty lines of types.

| `RunnerTask` field | Meaning |
| --- | --- |
| `text` | The task. |
| `session_id` | The conversation: the A2A `contextId`. Empty starts a new one. |
| `task_id` | This turn. |
| `caller` | An opaque reference to the caller's identity. Never a credential. |
| `headers` | The string metadata that came with the call (trace headers, `mas.*` keys). |
| `parent_call_id`, `upstream_correlation_id` | Where the task sits in a delegation. |

| `RunnerReply` field | Meaning |
| --- | --- |
| `text`, `context_id` | The answer and its conversation. |
| `artifacts`, `task_state` | Structured outputs and an A2A task state such as `input_required`. |
| `stream` | Text chunks, when the runner streamed. A host that cannot stream joins them. |
| `events` | The runner's own events, when its protocol reports any (a harness's tool calls). Plain dicts. |

`run` blocks and a host calls it from a worker thread, as the A2A executor already does for the kernel.
Failures are `RunnerError`; a refusal by governance is `RunnerDenied`, a subclass.

`agent_runner` is a **library plugin type**, like `agent_expose` and `webserver`. It is not one of the
closed boundary slots and not a spec identity type, so the kernel gained no symbol and no hook.

### Where the seam does not go

The seam is at the host handler, not inside `RuntimeInstance`. `SessionController`, the checkpoint
store, HITL and working memory are written against a kernel instance and stay that way. A runner that
is not the kernel therefore does not get pause, resume, steer, checkpoints or HITL. The listener,
the lab run and `serve` choose a runner before any kernel is built; they do not swap a runner under a
live kernel session.

## What stays outside the seam and what is inside

| Concern | Where | In proxy mode |
| --- | --- | --- |
| A2A listener, agent card, `contextId`, task states | `agent_expose` plugin (`A2AExposure`) | Unchanged: the same listener serves a harness. |
| Other transports and links (SLIM, HTTP, A2A framing) | `agent_comm` stack, Flavour `bind` | Unchanged. The `a2a-harness` runner sends through an `agent_comm` link, so layers above the transport (identity, redaction, retry) apply to the task as to a peer send. |
| Identity of the caller | Identity layer on the stack, `RunnerTask.caller` | The field exists but the A2A exposure does not fill it today, so it is empty. See [Open questions](#open-questions). |
| Governance at the boundary | `GovernedRunner` (see below) | The task is judged before the harness runs, the reply after. |
| Observability and redaction | The agent's observability plugins and the native events sink | `user_input`, `agent_response`, `governance_decision`, plus `runner_call` and `runner_event` events. The last two reach observability plugins but are not written to `events.jsonl` (see [Open questions](#open-questions)). |
| The kernel main loop (state machine, context assembly, model and tool calls) | `RuntimeKernel`, `KernelDriver` | **Not used.** The harness has its own loop. |
| Per-model-call and per-tool-call governance, tool offering, context, compaction, steer, checkpoints | Inside the kernel | **Not available.** |

## What a third-party harness does not give you

A kernel agent is governed call by call: every model call and every tool call passes an egress and an
ingress chokepoint, so a Control can see the arguments of one tool call and refuse it. A harness runs
its own loop. MAS Lab does not see its model calls, does not offer or withhold its tools, and cannot stop
one tool call in the middle of a task. What is left is **admission and audit at the boundary**:

- Admission: a Control can refuse the whole task before the harness starts.
- Audit: the task, the reply and the decision are recorded.
- Bounds: the runner itself limits what it can do (an allow-listed command, a deadline, size caps, an
  explicit environment). A harness that needs per-tool control has to provide it.

### What a governance plugin sees in proxy mode

`GovernedRunner` (in `library-standard`) judges the call with the chokepoints that exist, so a Control
written for a kernel agent applies unchanged:

- **Egress.** One `EgressIntentView` for a `TOOL_CALL` named `runner:<id>` (for example
  `runner:stdio-harness`). `tool_arguments` is `{task, session_id, task_id, caller}`. `offered_tools` is
  that one name. A plugin sees the **task text in** and nothing about what the harness will do with it.
  It is evaluated by `evaluate_egress_at_chokepoint` against the agent's governance, so `budget`
  (`max_tool_calls` caps harness calls), `gov_no_undeclared_tool` (passes) and any plugin that reads
  `tool_arguments` work as they do for a tool.
- **Ingress.** One `IngressIntentView` with `response_kind: TOOL_RESULT` after the runner replied,
  through `evaluate_ingress_chain`. An ingress plugin sees the **kind of the response, not its text**.
  A Control cannot read the reply today. That is a property of the existing ingress hook, not a choice
  here.
- **Harness events.** If the harness reports its own events over its protocol (the stdio JSON-RPC
  framing carries `event` notifications), they come back in `RunnerReply.events` and are emitted as
  `runner_event` observability events. Governance does not see them, because they arrive while the
  task is already running. The A2A link returns text only, so an A2A harness reports none.
- **Decisions.** Only ALLOW and LOG let the task through. A boundary has no operator to ask and no
  model to retry, so HITL, RETRY and MODIFY refuse the task as BLOCK, SKIP, TERMINATE and BLACKLIST
  do, and so does a plugin that raises. The refusal is a `RunnerDenied`; over A2A it is a failed task.

The `gov_trigger_destructive` flag marks the call destructive, as it does for any tool call.

## Choosing a runner: `spec.runner`

An Agent names its runner in its manifest:

```yaml
kind: Agent
metadata: {name: front}
spec:
  description: Fronts an external harness.
  runner:
    type: stdio-harness
    params:
      command: [/usr/local/bin/some-harness, --stdio]
      allow: [/usr/local/bin/some-harness]
      framing: jsonrpc
      timeout: 120
```

Absent, the kernel loop runs, as always. Present, `type` is the id of a registered `agent_runner`
plugin (hyphenated, no underscores) and `params` are its arguments. The shape is the one every other
plugin binding has (`{type, params}`). `mas-ctl validate` checks the shape against the schema and checks that
`type` is registered.

Why the manifest and not a Flavour `bind` entry: a Flavour links an agent to infra rows and does not
define anything of its own, while the runner decides what the agent is. It is also what an experiment
varies. An overlay patches `spec.runner`, so one experiment can run the same prompts against a kernel
agent and against a harness. The Controls still come from the Flavour, so the same Control gates
both.

An agent with `spec.runner` needs no model, tools or context of its own.

### Where it is honoured

| Entry point | Honours `spec.runner` |
| --- | --- |
| `mas-ctl serve` | Yes. No kernel is built; the A2A listener and the Controls are the same. |
| The lab runner (`MasBenchRunner`) for a standalone agent | Yes, including through an overlay. The events file has the shape of a kernel run, with `runner` on `execution_start`. |
| `mas-ctl chat`, `tui`, `run-mas`, a MAS of several agents with a runner-backed member | **No.** They build a kernel for every agent. A runner-backed agent there is built as a kernel agent. |

## Runners that ship

| Id | Library | What it does |
| --- | --- | --- |
| (none, the default) | `mas-lab-ctl` (`KernelRunner`) | The kernel main loop: one session per conversation, as before. |
| `stdio-harness` | `library-standard` | Spawns a command and speaks line or JSON-RPC framing on its stdin and stdout. |
| `a2a-harness` | `library-ioa` | Sends the task to a remote A2A agent and returns its reply. |

`stdio-harness` is built to be safe to point at a program you do not control:

- The command is a list, never a shell string, and `command[0]` must be an absolute path that is in
  `allow`. There is no `PATH` lookup.
- The child gets exactly the `env` the spec gives it. Nothing is inherited, so an API key in the
  host's environment does not reach the harness.
- Every read has a deadline (`timeout`) and a size cap (`max_task_bytes`, `max_reply_bytes`).
- `per-task` lifecycle (the default) spawns one process per task and ends it after the reply.
  `persistent` keeps one process, serves tasks one at a time and respawns it if it died.
- The process group is sent SIGTERM, then SIGKILL after `term_grace` seconds, when a task times
  out or fails, and when the runner closes.

`a2a-harness` sends through an `AgentCommContract`: by default the `a2a` link at `url` (with `headers` and
`timeout`), or any other link or built stack passed as `comm`. A failed, rejected or canceled remote
task is a `RunnerError`, not reply text.

## Proof that the default did not move

The default runner is not new code beside the old path: `make_runtime_handler` now builds a
`KernelRunner` and calls it, and the body that ran after the session lookup is that runner's `run`.
`ctl/tests/test_default_runner_parity.py` replays a scripted two-turn conversation and compares the handler results
and every recorded observability event, byte for byte, with a file captured from the code before the
change (`ctl/tests/golden/default_runner.json`; call ids are random UUIDs and are replaced by a token on
both sides).

## Who can reuse this

- **Lab experiments over other harnesses.** Put the harness behind `spec.runner`, point an
  experiment at the agent, and run the usual prompts and metrics. An overlay that sets `spec.runner` against
  one without it compares a kernel agent with a harness on the same dataset and the same events
  schema. The events show the task, the reply, the Control's decision and, for the JSON-RPC framing, the
  harness's own tool events.
- **Fronting a harness with admission and audit.** `mas-ctl serve` on a manifest with `spec.runner`
  makes a command-line agent an A2A server. The listener, a Control (a call budget, a plugin that
  reads the task), the observability sink and the identity layer on any `agent_comm` stack are MAS
  Lab's. The harness is whatever the manifest points at.
- **A thin proxy for a remote agent.** `a2a-harness` makes MAS Lab a gate in front of another A2A agent.

## What was not done

- A runner is chosen when the host starts. `chat`, `tui`, `run-mas` and a multi-agent MAS do not honour
  `spec.runner`. A MAS with one runner-backed agent among kernel agents needs the delegation path
  (`make_workflow_send`) to take a runner for that member, which was left alone.
- No per-tool control, no pause, steer or checkpoint for a runner-backed agent.
- The native events file records the `runner` field on `execution_start`. `runner_call` and
  `runner_event` reach observability plugins as session events, but the native sink only writes the
  kinds it already knows, so they are not in `events.jsonl`.
- `stdio-harness` limits that matter for a real command-line agent:
  - `framing: line` returns the **first** line the harness writes and then ends it. A harness that prints a
    multi-line answer is cut after line one unless it escapes newlines (`framing: jsonrpc` has no such limit).
    A mode that reads to end of output is not built.
  - Its standard error is discarded, so a harness that fails says only "exit status N".
  - `persistent` is one process for **every** conversation of the served agent, and the line framing carries no
    session id. Two callers share the harness's memory. Use `per-task` when callers must not see each other.
- An `a2a-harness` target that answers a failed, rejected or canceled task raises through a check added in
  `A2AAgentComm`; `fix/peer-failures` carries the same check for peer sends, so the two must be merged once.
- No new Control. Existing ones apply to the task as a tool call; a Control that reads the reply would
  need an ingress hook that carries it.

## Open questions

- **Reply visibility to governance.** Ingress plugins do not get the reply text. Admission of the
  reply (redaction, leak checks) needs either an ingress view that carries it or a runner-level
  output filter. Which one belongs to the kernel contract is a decision for its owners.
- **Caller identity and headers.** `RunnerTask.caller` and `headers` exist. The A2A exposure handler does
  not receive them today (it passes the `contextId`, the message id and the `mas.*` metadata), so
  over A2A they arrive empty. Forwarding the verified caller of the A2A request means extending that
  handler call; the identity layer's output would then ride `caller`.
- **Per-send headers on the A2A link.** The `a2a` link keeps the headers it was built with and does not
  send per-call headers; the `a2a-harness` runner hands them to the link, which drops them.
- **Streaming.** The reply can carry chunks; neither shipped harness runner streams.
- **A second kernel instance as shell.** `spec.runner` builds an idle `RuntimeInstance` to reuse the
  governance chain and the observability plugin wiring. A smaller host object would remove it.
