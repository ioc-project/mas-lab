<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Flavour manifest (`kind: Flavour`)

**Package:** `runtime/` | **Schema:** [`flavour.schema.yaml`](../schemas/runtime/flavour.schema.yaml)

A **flavour** (`kind: Flavour`) is the glue between the logic spec (the MAS and its
agents, which name no protocol and no endpoint) and the infra (the rows that say what
exists). Its [`bind`](#bind) section links each agent to infra rows and says what this
deployment does with it. It defines nothing itself: no endpoint, no protocol, no
plugin of its own.

`spec.control` attaches [`kind: Control`](control.md) documents. `bind.events`
names Application rows of observability sinks. `spec.require_usage` refuses an
agent with no bind chain or agent-keyed Application row (bundled `prod` sets it).
Flavours also still accept leftover keys from before `bind` (`observability`,
`telemetry`, `agent_comm`, `tools`, and a leftover `control` mapping). They are
not what a Flavour is. Those leftover keys are accepted and read by nothing.
See [Leftover keys](#leftover-keys).

A flavour never holds model API URLs.
Those live in **infra** manifests (the bundles that say where endpoints are; see [infra.md](infra.md)) and in agent `models`.

This is the shipped `local` flavour. It is empty glue: leftover keys are not set.

```yaml
apiVersion: flavour/v1
kind: Flavour
metadata:
  name: local
spec: {}
```

Shipped flavours use `apiVersion: flavour/v1`.
The schema validates `apiVersion` as a string and does not constrain it to a constant.
RAG and skills backends are agent concerns.

Terms are in the [glossary](../glossary.md).
Back to the [manifest hub](README.md).

## Leftover keys

These keys predate `bind`. The schema still accepts them. They are
`deprecated: true`. Validation warns once, listing the keys that are set; a Flavour file you pass with `--flavour PATH` prints that warning, and the bundled flavours stay quiet.
A new Flavour sets `bind` and, when it attaches Controls, `control`.

| Key | What happens |
|-----|------------------|
| `agent_comm` | Accepted and read by nothing. The in-process link is the library default `agent_comm` plugin. How agents reach each other is `bind`. |
| `tools` | Accepted and read by nothing. Tool exposure is configured through infra. |
| leftover `control` mapping | Accepted and read by nothing. Live Flavour `control` is a list of Control refs. See [control.md](control.md). |
| `telemetry` | Accepted and read by nothing. |
| `observability` | Accepted and read by nothing. Put it in a [Control](control.md). `--events*` is a shortcut on the run. `bind.events` names sink rows and adds those plugins. Native records unless a Control named the observability list (including `[]`) or leftover agent observability named plugins. |

Governance plugins that alter the agent's trajectory are declared in a Control the Flavour attaches, or on Agent `governance[]` (deprecated, still applied when no matching Control names that list).
Retry and circuit_breaker stay on Agent `spec.control` (deprecated, still applied). They are not Control plugins.

To mimic another product (for example an OpenClaw-like profile), attach Controls instead of copying every plugin into the flavour YAML.
`with-hardened` adds an undeclared-tool BLOCK, a leftover-send dest BLOCK, call ceilings and a typed ingress retry.
Add an observability Control next, and later checkpoint and backtrack.
See [governance.md](governance.md) and [reliability.md](../references/reliability.md#where-reliability-behaviour-goes).

## Schema fields

The schema keeps `spec` open (`additionalProperties: true`).
Its declared sections are:

| `spec` field | Purpose |
|--------------|---------|
| `bind` | Links each agent to infra rows: routing, placement, overrides. See [bind](#bind). |
| `agent_comm` | Deprecated and ignored: the in-process link block (`protocol: agent-local`, `mode`, `emulation`). Nothing reads it. |
| `capabilities` | Feature flags and capability overrides. |
| `telemetry` | Deprecated and ignored: basic telemetry sink configuration. Nothing reads it. |
| `observability` | Deprecated and ignored. Observability is a Control entry or `--events*`. `bind.events` names sink rows. Native records unless a Control named the observability list (including `[]`) or leftover agent observability named plugins. |
| `control` | Control manifests this Flavour attaches: a list of `{ref, agents}`. `agents` is `$all` (default), `$entry`, `$not-entry`, `$delegates`, one agent id, a list of ids, or the same agent path as `--override` (`.mas.agent[id]` / `.mas.agent[*]`) — the same idea as `bind.agents.<id>` for one agent and the default `bind.agent_comm` chain for every agent. A leftover mapping is unread. |
| `require_usage` | When true, every agent of the session must have a bind chain or an agent-keyed Application row. Bundled `prod` sets this. A file named `prod.yaml` does not. |
| `tools` | Deprecated and ignored: leftover tool-exposure block. Tool exposure is configured through infra. |
| `config` | Free-form deployment configuration. Never store secret values. |
| `operator` | Operator/provisioner metadata. |

## bind

`spec.bind` is routing and placement: it links the agents of a
session to infra rows. Infra rows are servers and endpoints named by what they are
(a SLIM node, an A2A server, an in-process channel), never by an agent; each row's
`protocol` is a layer plugin and its `usage` is what the row can do
([Rows a Flavour binds](infra.md#rows-a-flavour-binds)). The Flavour defines
nothing itself.

```yaml
apiVersion: flavour/v1
kind: Flavour
metadata: {name: desk-front}
spec:
  bind:
    agent_comm: [desk-node]                # the default chain, bottom first
    usage: [connect, listen]               # this deployment hosts, unless an agent says otherwise
    agents:
      billing: {usage: connect}            # dialled here, served by another deployment
      outage:
        agent_comm: [outage-server]        # its own chain: one A2A server row
        usage: connect
        params:                            # this agent's overrides of the row's params
          outage-server: {url: "https://outage.example.test/a2a"}
  control:
    - {ref: controls/company-baseline.yaml, agents: $all}   # every agent, like the default bind chain
    - {ref: controls/hitl.yaml, agents: desk}               # one agent, like bind.agents.desk
    - {ref: controls/delegates.yaml, agents: $not-entry}
    - {ref: controls/desk.yaml, agents: .mas.agent[desk]}
```

| Key | Meaning |
| --- | --- |
| `bind.agent_comm` | The default chain: row names, bottom first. The first row is the transport. |
| `bind.usage` | This deployment's default for every agent that omits its own usage: `connect`, `listen`, or both. Default `connect`. |
| `bind.agents.<id>.agent_comm` | That agent's own chain, instead of the default. |
| `bind.agents.<id>.usage` | `connect` (this deployment reaches the agent over its chain), `listen` (this deployment hosts it), or both. Omitted, it is `bind.usage`, else `connect`. It must be within the `usage` of every row of the chain. |
| `bind.agents.<id>.params` | Overrides of a row's params for that agent: row name to `{param: value}`. A string value may hold `{agent}`. |
| `bind.events` | Application row names of observability sinks this deployment uses. Each row adds the plugin its `protocol` names and gives it the row's own settings (not `url`, `secret_env` or the other endpoint keys). Native `events.jsonl` still records. `--no-events` and an explicit `--events-format` replace the set. |

Bundled names: `local` (in-process, the default) and `prod` (`require_usage`: every agent must have a bind chain or an agent-keyed Application row). `--connect` / `--listen` / `--bind` write agent-keyed Application rows; they are not copied into Flavour `bind`.

An agent with no entry rides the default chain with `connect`; with no default
chain it keeps the in-process link. Set `listen` (on the agent, or as `bind.usage`)
to host it here. An agent that delegates joins the chain of each
agent it delegates to. Only an agent with `listen` is served: a request that reaches
it on its own chain runs its turn, one request at a time, and waits while the user's
own turn on that agent runs (in `chat`, `tui`, `run-mas` and benchmark runs). On any
other chain an agent's link only sends, and a request that reaches it is refused.
A delegation that comes back over a chain to an agent in the middle of its own turn
waits for that turn to end, so do not bind a delegation cycle onto a chain. The wait
ends after 120s (the session turn wait, the same default as `reply_timeout_s`). Over
SLIM and `local-group`, `reply_timeout_s` on the link also ends a send that got no
reply. A chain of one row whose protocol is an `agent_comm` link
with a `url` (an A2A server) is the A2A client of an agent at that url, the same
path as a row `--connect` writes. Rows written by `--connect` / `--listen`, keyed by
an agent id, keep working as before; an agent cannot have both such a row and a
`bind.agents` entry.

A row's string params may hold `{agent}`, replaced by each agent's id, so one row
serves many agents (`url: https://hub.example.test/a2a/{agent}`); a SLIM agent's
default name `<identity>/<agent_id>` is the same idea. `{agent}` is the only
placeholder, and it is filled as it is, never encoded: only an agent id of letters,
digits, `.`, `_` and `-` can fill it (the url grammar).

Each layer has its own grammar for an agent id, checked at bind time for every agent
on every row of its chain, whether or not the row uses `{agent}`. `local-group` and
`slim` name an agent `<identity>/<agent_id>`, so the id is one name part: letters,
digits, `.`, `_` and `-`. `slim` is stricter: neither the identity's service nor the
agent id may contain `.`, so `x.y` is refused on a `slim` row and accepted on a
`local-group` or `http` row. `http`, `a2a` and `a2a-framing` name nothing and accept
any id that fits the url grammar where `{agent}` is used. A refused id is one line:
`Flavour bind: agent 'x.y' cannot ride row 'desk-node' (layer 'slim'): SLIM agent id
cannot contain '.': 'x.y'`.

Refused before anything connects: an id under `agents` that is no agent of the
session; a row name that is also an agent id; a name that is no row; a `usage`
beyond the capability of a row of the chain (the error names the row and the
agent); a placeholder other than `{agent}`; `params` for a row outside the agent's
chain; a first row that is not a transport; two chains that share a bottom
row (an agent on both would join it twice under one name: give the second chain its
own row, for example a second connection to the same node with its own `identity`);
a row that declares `usage: listen` on a transport that only connects (`http`).

`run-mas`, `chat`, `tui`, `serve`, `infra resolved` and benchmark runs all apply `bind`
with the same code. A one-agent session (`chat`, `tui`, a one-agent benchmark run)
delegates to no one, so it joins a chain only when the Flavour serves it there. The
lab run hash leaves `bind` out: which rows a run rides is deployment, not logic.

`bind` here is unrelated to the deprecated `--bind NAME=URI` CLI flag, which is an
alias of `--connect`.

## What does not belong here

`FlavourSeparationValidator` (`mas.ctl.validate.separation`) enforces this table.
The bench manifest validator (`mas.lab.mas_validator`) runs it over every `flavours/*.yaml`.

| Forbidden in Flavour | Belongs in |
|---------------------|------------|
| `spec.llm` (model, `api_base`) | Agent `models` + infra `LLMProxy` |
| `spec.skills` | Agent `skills` |
| `spec.prefer_local` | Workspace infra refs / runtime tuning |
| `spec.infra_refs`, `spec.infra_ref` | Workspace `config.yaml`, CLI `--infra-ref` |

## Selection at run time

A Flavour does not select MCP consumption: a `ToolServerRegistry` with `usage: connect`
is an infra dependency, and `--connect NAME=mcp://...` is a shortcut for that infra row.
Agents reach each other in-process unless the Flavour's `bind` links them to infra rows,
or an agent-keyed Application row (`--connect NAME=a2a://...`) gives one its own url.

```bash
mas-ctl run-mas mas.yaml --infra-ref ./infra/prod-bundle.yaml
```

`--flavour` takes a bundled name (`local`) or the path
of a Flavour YAML file, for example one that only sets [`bind`](#bind):

```bash
mas-ctl run-mas mas.yaml --flavour ./flavours/desk-slim.yaml --infra-ref ./infra/desk.yaml
```

For benchmarks, select a flavour on the experiment or the CLI:

```bash
mas-lab benchmark run experiment.yaml --flavour local
```

`metadata.default_flavour` on a MAS provides the default name.

In OSS mas-lab, the canonical flavour YAML lives only under `library-standard/src/mas/library/standard/flavours/`.
It contains `local.yaml` and `local-benchmark.yaml`.
The scheduler resolves flavours with `mas.lab.flavour.resolve.resolve_flavour_path()`.
Do not copy the bundled flavour YAML into individual labs or samples. A Flavour that
only binds rows, like the ones in [Tutorial 7](../tutorials/07-slim-link/README.md),
sits beside the infra it names.

## See also

- [infra.md](infra.md): where model and tool endpoints live
- [experiment.md](experiment.md): `default_flavour`
- [mas.md](mas.md): `metadata.default_flavour`
- [Tutorial 3](../tutorials/03-experiments-and-analysis/README.md): benchmarks that select a flavour
