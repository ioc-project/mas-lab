<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Tutorial 01 — Building an Agent

> **Packages:** `mas-ctl` (chat/validate), `mas-runtime` (contracts/plugins), `mas-lab` (plots/benchmarks)
> **Time:** ~30 min
> **Prerequisite:** [Tutorial 0](../00-environment-setup/README.md) (install, infra bundles, API key).
> Run all commands from `docs/tutorials/01-building-an-agent`. Validation runs offline; live runs need `OPENAI_API_KEY` in `.env`.

You go from an empty YAML file to a question-answering agent with tools, a skill, and memory.
You then run it from the CLI, serve it over the Agent2Agent (A2A) protocol, and inspect its trace.

## What you need

- A working install from Tutorial 0, with a default infra or `--infra-ref`.
- `OPENAI_API_KEY` in a gitignored `.env` in this directory or any parent. `mas-ctl` loads it itself.

## Quick commands

These commands run everything this tutorial builds. Each step below explains one layer.

```bash
mas-ctl chat agent.yaml -o overlays/tools.yaml -o overlays/skills.yaml -o overlays/memory.yaml \
  -q "What is the current price of Apple?"

# Operator steering mid-run, then type: /steer Use web search for stock prices, not fruit.
mas-ctl chat agent.yaml -i -o overlays/tools.yaml --trace

# Terminal UI (TUI)
mas-ctl tui agent.yaml -o overlays/tools.yaml -o overlays/skills.yaml
```

A manifest is a YAML file that declares a MAS Lab object. An overlay is a small YAML file that changes a manifest
at run time. The base `agent.yaml` never changes; each step adds an overlay with `-o`.
Overlays write tool references as `<library>:<path>`. These use the `samples` library from `mas-library-samples`.
A lab can ship its own library: see [labs-and-libraries.md](../../labs-and-libraries.md).

> **Exchange log.** This directory's `config.yaml` sets `mas_ctl.trace: summary`, so every `mas-ctl` command prints
> a readable log (`USER -> AGENT -> LLM -> TOOL …`) on stderr. Elsewhere, add `--trace`
> ([CLI reference](../../cli/mas-ctl.md#exchange-log)). The conversation stays on stdout.
>
> **LLM response cache.** Repeat runs can return in tens of milliseconds with identical wording.
> Pass `-v` to log a cache hit. Disable with `--no-cache-read`
> or `MAS_LLM_CACHE_READ=false`. See [llm-cache.md](../../manifests/llm-cache.md).

---

## Overview

An agent manifest (`kind: Agent`) says what the agent does, not how. The runtime handles the how:
design pattern, tool calls, context management, and tracing. You will:

1. Write a minimal agent manifest and run it.
2. Add tools with an overlay.
3. Stack a skill overlay.
4. Stack memory.
5. Try the other CLI modes.
6. Serve the agent card over HTTP.
7. Inspect the trace.

---

## Step 1 — Write the minimal manifest

`agent.yaml` is the base manifest for the whole tutorial:

```yaml
apiVersion: mas/v1
kind: Agent

metadata:
  name: qa-agent

spec:
  models:
    - model: gpt-4o

  description: Answer general knowledge questions.
  context:
    intent: "Answer general knowledge questions."
    role: |
      Answer questions clearly and concisely.
```

Everything else has a default. Run `mas-ctl compile agent.yaml` to print the expanded object per plugin
(also in [Compiled agent defaults](../../references/defaults.md)).

| Field | Default | Meaning |
|-------|---------|---------|
| `spec.models[0].model` | `any` when omitted | Main LLM. Pin a LiteLLM id, or omit it to inherit from MAS / `experiment.models.main`, then `config.yaml` `defaults.model`. |
| `spec.design_pattern` | `react` | ReAct loop with `max_cot_pass: 1`, `parallel: true`. [plugin-bindings.md](../../manifests/plugin-bindings.md) |
| `spec.assembler` | `assembler` | Builds `messages[]`. [context-assembly.md](../../manifests/context-assembly.md) |
| `spec.context_manager` | `summarising` | Keeps the last 10 user turns verbatim; `summarizer: llm`. [summarization.md](../../manifests/summarization.md) |

### How the runtime reaches an LLM

| Layer | Answers | Holds |
|-------|---------|-------|
| `agent.yaml` | What | Model, context, tools, plugins |
| Flavour | How | Protocol and plugins (local in-process, remote). Not the LLM. |
| `infra/` | Where | Model endpoint, tool providers, OpenTelemetry (OTel) collector |
| `.env` | Secret | API key, never committed |

The manifest model name (`gpt-4o`) is canonical. Infra can map it to the provider's name, such as `azure/gpt-4o`.
The runtime finds tools and skills next to the manifest first, then in the standard library.

A flavour is a named runtime environment (`kind: Flavour`, bundled in `mas-library-standard`).
`mas-ctl flavour list` shows `standard:local` (default), `standard:local-benchmark`, and `standard:mcp`.
Select one with `--flavour`. Today `chat` and `tui` wire only `local` and `mcp`.
`local` runs agents in-process, resolves tools without servers, and writes file-based traces and native `events.jsonl`.

The `llm_cache` middleware records and replays LLM answers without calling the provider ([llm-cache.md](../../manifests/llm-cache.md)).

### Run it

```bash
cd docs/tutorials/01-building-an-agent
mas-ctl validate agent.yaml
mas-ctl chat agent.yaml -q "What is the capital of France?"
mas-ctl chat agent.yaml -i        # interactive; type /quit to exit
```

`validate` prints `OK  agent.yaml  kind=agent`. The agent has no tools yet, so it answers directly. Wording varies by model. The exchange log (abridged) shows each hop:

```text
+0.000s [2026-…Z] USER -> AGENT[qa-agent] What is the capital of France?
+0.019s [2026-…Z] AGENT[qa-agent] -> LLM[gpt-4o] What is the capital of France?
+0.912s [2026-…Z] LLM[gpt-4o] -> AGENT[qa-agent] The capital of France is Paris.
```

The `Agent: …` line on stdout is the answer.

---

## Step 2 — Add tools with an overlay

An overlay is a partial manifest that merges into the base. Only the fields it declares change.

```bash
mas-ctl validate agent.yaml -o overlays/tools.yaml
mas-ctl chat agent.yaml -o overlays/tools.yaml \
  -q "If Tokyo has 14 million people and Paris has 2.1 million, how many times larger is Tokyo?"
```

Without a key, only `validate` runs. `overlays/tools.yaml` (shortened) adds tools; it does not add prompt text:

```yaml
apiVersion: mas/v1
kind: Overlay
metadata:
  name: tools
spec:
  target:
    kind: Agent            # use MAS to overlay a MAS
  patch:
    tools:
      $op:
        add:               # append to the list; a plain list would replace it
          - ref: samples:tools/web-search.tool.yaml
          - ref: samples:tools/calc.tool.yaml
          - kind: system   # built-in: ask the operator / inform the user
            name: request_human_input
          - kind: system
            name: inform_user
```

A `ref` points to a `kind: Tool` manifest that names the tool, describes its parameters, and points at its Python implementation.
The name, description and parameters of every tool are sent to the model with each request (the `tools` field of the chat call), so you only add prompt text if you want to steer *when* to use a tool beyond what its description says.
The exchange log now shows the tool request and result (values abridged):

```text
LLM[gpt-4o] -> TOOL[calc] {"expression": "14000000 / 2100000"}
TOOL[calc] -> AGENT[qa-agent] 6.666…
LLM[gpt-4o] -> AGENT[qa-agent] Tokyo is about 6.7 times larger than Paris.
```

---

## Step 3 — Stack a skill overlay

A skill is a markdown document that gives the agent domain expertise. You reference it by name; the runtime searches
`skills/` next to the manifest, then the installed libraries.

```yaml
# overlays/skills.yaml (patch section)
patch:
  skills:
    $op:
      add:
        - answer-formatting
```

- `skills/answer-formatting/SKILL.md` must start with YAML frontmatter (`---`, then `name` and `description`).
  A comment before the frontmatter makes the file a body with no name.
- Listing a skill makes the runtime add the `activate_skill` system tool ([System tools](../../manifests/system-tools.md)).
  The `description` is the when-to-use text in the catalog; the body loads on activation.

```bash
mas-ctl validate agent.yaml -o overlays/tools.yaml -o overlays/skills.yaml
mas-ctl chat agent.yaml -o overlays/tools.yaml -o overlays/skills.yaml \
  -q "Who is the current president of the United States?"
```

Overlays apply left to right. A later overlay wins for scalars; lists (tools, skills) append.
The model activates the skill, then answers with a one-sentence summary, bullets, and a HIGH / MEDIUM / LOW confidence line.

---

## Step 4 — Add memory

Memory is a resource, not a tool. The runtime injects relevant memories into the prompt
(retrieval-augmented generation, RAG), and the agent calls memory tools.

```yaml
# overlays/memory.yaml (patch section, shortened)
patch:
  context:
    memory_usage: |
      You have a long-term memory. When the user corrects you or shares
      a preference, store it with memory_store.
  memory: semantic    # shorthand; the memory tools do the work
  tools:
    $op:
      add:
        - ref: samples:tools/memory-search.tool.yaml   # memory_search, memory_get, memory_store
```

The `memory-search` sample tool (`memory-search`, `memory_store`) is what stores and recalls facts.
It opens a `SemanticMemoryPlugin` on a SQLite file, so memory survives across runs of the same agent:

```text
$XDG_DATA_HOME/mas/memory/semantic/<agent_id>.sqlite    # default: ~/.local/share/mas/memory/semantic/qa-agent.sqlite
```

Run 1 below leaves a fact in that file, and a later run of `qa-agent` can recall it. Delete the file to start clean.
`db_path` is a constructor argument of `SemanticMemoryPlugin` (it accepts `{agent_id}`). No overlay sets it today, and there is no `semantic-persistent` mode.
Plain `memory: semantic` is accepted by the schema but not read by the runtime ([agent.md](../../manifests/agent.md#memoryconfig)).

Check the full stack before running it. `validate` prints one `OK` line per overlay and one for the agent:

```bash
mas-ctl validate agent.yaml -o overlays/tools.yaml -o overlays/skills.yaml -o overlays/memory.yaml
```

### Run 1 — Store a correction

```bash
mas-ctl chat agent.yaml -o overlays/tools.yaml -o overlays/skills.yaml -o overlays/memory.yaml \
  -q "What is the current price of Apple?" \
  -q "No, I meant Apple the fruit, not the stock. Remember this for next time."
```

Turn 1: the agent reads "Apple" as the stock. Turn 2: it calls `memory_store`. Wording and prices differ in your run:

```text
LLM[gpt-4o] -> TOOL[web-search] {"query": "current price of Apple stock"}
Agent: The current price of Apple Inc. (AAPL) stock is approximately $258.83.
LLM[gpt-4o] -> TOOL[memory_store] {"content": "User prefers information about Apple the fruit …", "source": "user_preference"}
TOOL[memory_store] -> AGENT[qa-agent] {"status": "ok", "key": "510cd938…", "version": 1}
```

### Run 2 — Pre-seed memory

A seed overlay fills memory at startup, as if learned in an earlier session:

```yaml
# overlays/memory-seed.yaml (patch section)
patch:
  memory_seed:
    $op:
      add:
        - source: user_preference
          content: >
            User always refers to the fruit when asking about 'Apple',
            not the stock.
```

```bash
mas-ctl chat agent.yaml -o overlays/tools.yaml -o overlays/skills.yaml -o overlays/memory.yaml \
  -o overlays/memory-seed.yaml -q "What is the current price of Apple?"
```

The plugin injects the seeded preference into the user message before the LLM sees it:

```text
  [Relevant memories — apply these before answering]
  - User always refers to the fruit when asking about 'Apple' …
LLM[gpt-4o] -> TOOL[web-search] {"query": "current price of Apple fruit"}
Agent: The current price of apple fruit is approximately $1.56 per pound.
```

The question matches Run 1, Turn 1, but the answer flips. The new process has no history; the seed resolved the ambiguity.

---

## Step 5 — Try the other CLI modes

Besides `-q` and `-i`:

```bash
echo "What is the GDP of France?" | mas-ctl chat agent.yaml     # query from a pipe
mas-ctl chat agent.yaml -i --flavour local                      # explicit flavour (default)

# Offline, no API key: replay answers from a recorded llm_cache file; a miss is an error.
mas-ctl chat agent.yaml -i --infra-ref standard:openai \
  --infra-ref ../../../library-samples/infra/llm-cache-replay.yaml
```

Any `--flavour` other than `local` or `mcp` exits with an error that lists the supported names. The manifest never changes.

For quick experiments, add capabilities from flags instead of YAML. This matches the tools, skills, and memory overlays of Steps 2 to 4:

```bash
mas-ctl chat agent.yaml -i --tool web-search --tool calc --skill answer-formatting --memory semantic
```

| Flag | Effect |
|------|--------|
| `--tool NAME` / `--skill NAME` | Add a tool or skill by its manifest name (repeatable) |
| `--memory NAME` | Enable a memory backend (`semantic`) |
| `--set KEY=VALUE` | Set a `spec.context` key (repeatable) |

The CLI applies these flags after all `--overlay` files.

### Overlay progression

| Step | Command | What's new |
|------|---------|------------|
| 1 | `agent.yaml` alone | `context.intent` + `context.role` |
| 2 | `+ overlays/tools.yaml` | `web-search`, `calc` |
| 3 | `+ overlays/skills.yaml` | `answer-formatting` |
| 4 | `+ overlays/memory.yaml` | Memory plugin, context injection, `memory-search` tool |

`agent-final.yaml` is a hand-trimmed, commented snapshot of that merge. To generate the real merge, use
[`mas-ctl compile`](../../cli/compile.md):

```bash
mas-ctl compile agent.yaml -o overlays/tools.yaml -o overlays/skills.yaml -o overlays/memory.yaml \
  -O compiled-agent.yaml
mas-ctl validate agent-final.yaml
```

---

## Step 6 — Serve the agent card over HTTP

The A2A protocol lets other agents discover and call your agent. An agent card is the JSON description that A2A clients fetch.

```bash
mas-ctl serve agent.yaml --infra-ref infra/a2a-qa.yaml
```

`infra/a2a-qa.yaml` is an Application endpoint named `qa-agent` with `protocol: a2a`, `usage: deploy`, and
`url: http://127.0.0.1:9005`. In another terminal, use the official A2A CLI (a Go binary named `a2a`) or another client.
Stop the server with `Ctrl-C`:

```bash
a2a card get http://127.0.0.1:9005
a2a send -a http://127.0.0.1:9005 "What is the capital of France?"
```

See [Open standards → A2A](../../standards/a2a.md) for the full walkthrough.

---

## Step 7 — Inspecting the trace

`events.jsonl` is the machine-readable trace: one JSON event per line. `--events` records it;
`--events-file` sets the path (default `traces/events.jsonl`). A flavour with an observability plugin does the same
declaratively for a whole MAS (Tutorial 3).

### Which log do I need?

Three switches, three audiences. The outputs below come from a local stub model, so no key was needed:

| Switch | Goes to | Use it to |
| --- | --- | --- |
| `--trace` (here set by `config.yaml`) | stderr | See each hop: `USER -> AGENT -> LLM -> TOOL`. |
| `-v` / `-vv` (before the subcommand) | stdout and stderr | `-v`: `You:` / `Agent:` labels. `-vv`: session lines and engine payloads. |
| `--events` | `events.jsonl` | Keep a machine-readable record for analysis. |

`-v` is a global flag, so it goes before the subcommand: write `mas-ctl -vv chat ...`, not `mas-ctl chat -vv ...`.
With the trace on, `-vv` adds the endpoint and session lines before the exchange log:

```text
Engine mode=live (resolved infra → http://127.0.0.1:8799/v1)
── session_id: fec1291e-5b25-4b26-a16b-1d91ec3428fc ──
You: Hello
+0.009s [...] AGENT[qa-agent] -> LLM[azure/gpt-4o] Hello
+0.079s [...] LLM[azure/gpt-4o] -> AGENT[qa-agent] Hello from the stub model.
```

Without `--trace` (a directory with no `config.yaml`), `-v` instead prints a multi-line log on stderr, with the prompt sections and the reply.

### 7a — Read the raw event stream

The tool call makes the trace show a full ReAct loop: LLM, tool, LLM.

```bash
mkdir -p output
mas-ctl chat agent.yaml -o overlays/tools.yaml \
  -q "What is 15 * 23?" \
  --events --events-file output/events.jsonl

head -5 output/events.jsonl
```

```json
{"kind": "mas_call_start", "agent_id": "mas", "turn_id": "", "call_id": "…", "timestamp": 1790838315.21}
{"kind": "execution_start", "agent_id": "agent", "turn_id": "u1", "call_id": "agent-u1-exec", "input": "What is 15 * 23?", "timestamp": 1790838315.22}
…
```

Real lines carry more fields. Each event has a `kind`, `agent_id`, `timestamp`, a `call_id` linking start and end pairs,
and a kind-specific payload. The repository ships a sample trace (`traces/events.jsonl`, "What is the speed of light?"),
so you can skip the live run. The same stream feeds the plots and Tutorial 3's pipelines.

| Command | What it shows |
|---------|---------------|
| `mas-lab telemetry show traces/events.jsonl` | Event count, agents, and a breakdown by event kind (offline) |
| `mas-lab plot trajectory traces/events.jsonl --format mermaid` | Mermaid delegation diagram (offline) |

### 7b — Plot trajectories

`plot trajectory` prints the Mermaid diagram and writes `trajectory.mmd` in the current directory.

For the full picture, render a multilevel trajectory from your own run (Step 7a): swim lanes for the session, agents, and each call, with hover cards.
The shipped sample trace currently fails this plot with `Duplicate open call_id`, so use your own `output/events.jsonl`:

```bash
mas-lab plot multilevel-trajectory output/events.jsonl --format html -o output/trajectory.html   # self-contained, offline
mas-lab plot multilevel-trajectory output/events.jsonl --format svg -o output/trajectory.svg
```

Open `output/trajectory.html` in a browser:

- **Lanes:** Session, MAS, Agents, Calls. A lower lane expands the bar above it.
- **Bars:** an LLM call, a tool call, or a `⚙ context` step (assembles the prompt). Hover for input and output.
- **State boxes** (`S1`, `S2`, …): the content after one action and before the next. `S1` is the user's question.
  Click one to see where each part of the prompt came from, with token counts.

A single-agent turn reads: question, `⚙ context`, prompt, LLM, tool call, tool, LLM, answer.
[Tutorial 8](../08-telemetry/README.md) converts the stream to OpenTelemetry; [Tutorial 9](../09-kg-scope/README.md) builds a knowledge graph.
Tutorial 3 runs this plot (`mas.lab.plots.multilevel_trajectory`) as a pipeline step on every run.

---

## Automated checks

`demo/scenario.yaml` lists this tutorial's commands with expected exit codes. From the repository root:

```bash
pytest tests/tutorials/test_scenario_commands.py -v -k tuto-01
```

Live `mas-ctl chat` steps need `TUTORIAL_ONLINE=1` and a configured LLM.

## What you learned

- An agent is a small `kind: Agent` manifest. You add behaviour by stacking `kind: Overlay` manifests with `-o`.
- You declare tools, skills, and memory by reference or name. The runtime and infra layer resolve them.
- `mas-ctl compile` shows the merged agent. `events.jsonl` and `mas-lab plot` show what it did.

See the [glossary](../../glossary.md) and the [user guide](../../user-guide.md).

## Next step

[Tutorial 2 — Creating a Multi-Agent System](../02-creating-a-mas/README.md) composes a four-agent trip planner
with a routing topology and overlay-driven topology switching.
