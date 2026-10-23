<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# CLI overrides

`--override` changes one field of a manifest from the command line, without writing an overlay file. Use it for a quick patch to a single run. Part of [References → CLI](../references/index.md#cli). Topic index: [command line](index.md).

Terms: a *manifest* is a YAML file that declares an agent, a MAS, or an experiment. An *overlay* is a small YAML file merged on top of a manifest. See the [glossary](../glossary.md).

## The three overrides most people run

```bash
mas-ctl chat agent.yaml --override '.agent.spec.context.role="security reviewer"'   # standalone agent
mas-ctl chat agent.yaml --override '.agent.spec.models[*].max_tokens=4096'           # every model row
mas-ctl run-mas mas.yaml --override '.mas.spec.workflow.entry=moderator'             # the MAS, not an agent
```

An override never edits a file on disk. It patches the in-memory manifest for one command.

## Syntax

```text
--override PATH=VALUE
```

`PATH` starts at the current session, like jq:

```text
.mas.agent[planner].spec.context.role
.infra.spec.interceptors[middleware=llm_cache].params.allow_write
.config.defaults.model
```

- The leading `.` is the session. `.agent.spec…` and `agent.spec…` are the same path.
- The first segment is a **type**: `mas`, `infra`, `flavour`, `config` (workspace `config.yaml`), `experiment`, or `agent`.
- A name in brackets is an **id**, the same idea as Overlay `spec.target.name`: `agent[planner]` or `agent[id=planner]`.
- The rest walks that document's YAML tree. A selector in square brackets picks list entries.
- `VALUE` is a YAML value.

`agent` is a session child only for a standalone Agent command (`chat`, `compile agent.yaml`). A MAS session has several agents, so the path goes through the MAS: `.mas.agent[planner].…`.

`kind:path` (`agent:spec.context.role`) is still accepted as an alias of `kind.path`. Prefer the dotted session path.

Quote the whole assignment in the shell. Quoting is required when it contains `$`, brackets, spaces, braces, or quotes.

A CLI path is the same hierarchy as a file Overlay. The session node (type and optional id) is Overlay `spec.target`. Everything after it is Overlay `spec.patch` — `spec.models[id=main].max_tokens=4096` is `{models: [{id: main, max_tokens: 4096}]}`. File overlays and `--override` merge that dict through the same engine. Envelope fields (`metadata`) and kinds without `spec` (`library`, `experiment`, `config`) are assigned on the document, then schema-validated.

### Overlay fields vs `--override`

An Overlay is a named patch document. `--override` is one assignment, not a document, so it has no Overlay `kind` / `metadata.name`.

| Overlay | `--override` |
| --- | --- |
| `kind: Overlay` | (none — CLI one-shot) |
| `metadata.name` | (none — generated as `cli-override`) |
| `spec.target.kind` | session type (`mas`, `agent`, `infra`, …) |
| `spec.target.name` | `agent[planner]` / `mas[team]` |
| `spec.patch` | field path `= VALUE` |

## Selector examples

A selector chooses which list entry the path continues into.

| Goal | Override | Result |
| --- | --- | --- |
| Set a field | `.agent.spec.context.role="reviewer"` | The standalone agent role becomes `reviewer`. |
| Add to a list | `.agent.spec.tools={"$op":{"add":["web-search"]}}` | `web-search` joins the tool list. |
| Remove from a list | `.agent.spec.tools={"$op":{"remove":["calculator"]}}` | `calculator` leaves the tool list. |
| Select by id | `.agent.spec.models[id=main].model="gpt-test"` | Only the model entry whose `id` is `main` changes. |
| One MAS agent | `.mas.agent[planner].spec.design_pattern=cot` | Overlay `target.kind: Agent`, `target.name: planner`. |
| Wildcard | `.agent.spec.models[*].max_tokens=4096` | Every model entry gets `max_tokens: 4096`. |

A selector that matches nothing fails with an error that names the requested identity.

## Task guide

### Change a field on a standalone agent

```bash
mas-ctl chat agent.yaml --override '.agent.spec.context.role="security reviewer"'
```

### Change a field on a MAS

```bash
mas-ctl run-mas mas.yaml --override '.mas.spec.workflow.entry=moderator'
```

### Change one agent inside a MAS

```bash
mas-ctl run-mas mas.yaml \
  --override '.mas.agent[reviewer].spec.design_pattern=cot'
```

That is Overlay `target.kind: Agent` plus `target.name: reviewer`. It applies to a referenced `{id, ref}` row and to an inline `spec`. See [Agents in a MAS](#agents-in-a-mas).

### Change every agent in a MAS

```bash
mas-ctl run-mas mas.yaml \
  --override '.mas.agent[*].spec.design_pattern=cot'
```

### Inspect the effect without running

```bash
mas-ctl compile agent.yaml --override '.agent.spec.models[*].max_tokens=4096'
```

`compile` prints the resolved Agent manifest. See [compile](compile.md).

### Change an experiment setting

```bash
mas-lab benchmark run experiment.yaml \
  --override '.experiment.experiment.run.n_runs=1' \
  --override '.experiment.experiment.dataset.limit=2'
```

### Change a workspace value for one run

```bash
mas-ctl chat agent.yaml --override '.config.defaults.model="gpt-test"'
```

The workspace `config.yaml` stays unchanged. `.workspace` is the same node.

### Address a key that contains dots

```bash
mas-ctl chat agent.yaml --override '.agent.spec.context["key.with.dots"]=value'
```

The quoted key keeps dots and brackets inside one map key.

### Set observability on the flavour

```bash
mas-ctl chat agent.yaml --override '.flavour.spec.observability=[native]'
```

A *flavour* is a deployment preset. This override changes observability and tool exposure. It does not switch internal agents to A2A. That needs an infra Application row with `usage: use`, which `--bind` writes for you.

## Bind

`--bind NAME=URI` is a shortcut for infra YAML. It expands into `--override infra:…`.

An *infra bundle* is a YAML file that says where services live: agent endpoints and tool servers. The bind result merges into the same in-memory infra document as `--infra-ref`. No separate bind resolver exists. `chat`, `tui`, `run-mas`, `serve`, and `compose` all read the patched infra.

`--bind` applies first. An explicit `--override` of the same path wins.

These two forms are equivalent:

```yaml
# infra/a2a-agents.yaml — kind: Application
spec:
  endpoints:
    banking_assistant:
      protocol: a2a
      usage: use
      url: http://127.0.0.1:8080/agents/banking_assistant/
```

```bash
mas-ctl run-mas mas.yaml --infra-ref infra/a2a-agents.yaml
mas-ctl run-mas mas.yaml \
  --bind banking_assistant=a2a://127.0.0.1:8080/agents/banking_assistant/
```

Repeat `--bind` for several peers and tools:

```bash
mas-ctl run-mas mas.yaml \
  --bind banking_assistant=a2a://127.0.0.1:8080/agents/banking_assistant/ \
  --bind fraud_adjudicator=a2a://127.0.0.1:8080/agents/fraud_adjudicator/ \
  --bind analyze_transaction_risk=mcp://127.0.0.1:8080/mcp#analyze_transaction_risk \
  --bind calculate_fraud_score=mcp://127.0.0.1:8080/mcp#calculate_fraud_score
```

| Assignment | Expands to (same fields as the infra manifest) |
| --- | --- |
| `NAME=a2a://host:port/path/` | `infra:spec.endpoints['NAME']={protocol: a2a, usage: use, url: http://host:port/path/}` |
| `NAME=mcp://host:port/mcp#tool` | `infra:spec.tool_servers[id=…]={protocol: mcp, usage: use, url: http://…, tools: [tool]}` |

- `a2a://` and `mcp://` are scheme tags. The stored URL is `http://` or `https://` (`a2as://` and `mcps://` give `https://`).
- Two MCP tools on the same origin collapse to one server.
- The MAS still names the agency peer, and the agent still lists the tool. `--bind` only fills in the location on the infra document.
- The flavour stays `local` (`agent_comm.protocol: agent-local`). The Application `usage: use` row routes that peer over A2A.

## Session tree

Every loaded manifest hangs off the current session. Types are path segments; ids are jq-style brackets.

```text
.
  +-- config / workspace ------------------- .config.*
  |
  +-- experiment --------------------------- .experiment.experiment.*
  |                                            +-- application (the MAS under test)
  |                                            +-- scenarios / dataset / run
  |
  +-- mas ---------------------------------- .mas.spec.*
  |       |
  |       +-- agent[planner] --------------- .mas.agent[planner].spec.*
  |                    +-- ref: agents/planner.yaml
  |                    +-- or an inline spec
  |
  +-- agent -------------------------------- .agent.spec.*   (standalone session only)
  |
  +-- infra -------------------------------- .infra.spec.*
  +-- flavour ------------------------------ .flavour.spec.*
```

There is no generic `manifest` node. `spec.tools` is not unique until the type is in the path. The command decides which children exist. Naming a missing node is an error, not a silent no-op.

| Path | Typical commands | Meaning |
| --- | --- | --- |
| `.agent` | `chat`, `compile` of an Agent | Standalone Agent document |
| `.mas` | `run-mas`, `compose`, `compile` of a MAS | MAS or Workflow document |
| `.mas.agent[id]` | those MAS commands | One agency agent (Overlay `target.name`) |
| `.infra` | `chat`, `run-mas`, `compose`, `compile` | Infra, or the merged infra document |
| `.flavour` | runtime composition, `compile` | Flavour manifest |
| `.library`, `.tool`, `.pipeline`, `.dataset` | `compile` | Other registered schema kinds |
| `.experiment` | `mas-lab benchmark run`, `compile` | Experiment before validation and expansion |
| `.config` / `.workspace` | `chat`, `run-mas`, `compile` | In-memory effective workspace / `config.yaml` |

A MAS agent is not `.agent`:

```text
.mas.agent[planner].spec.tools
.agent.spec.tools          # standalone Agent session only
```

### Agents in a MAS

`.mas.agent[planner]` is the typed path. It becomes an Overlay with `target.kind: Agent` and `target.name: planner`, so it works for `{id, ref}` rows and for inline `spec` rows.

Walking the MAS YAML (`.mas.spec.agency.agents[id=planner].spec.tools`) still works when that row already has an inline `spec`. The usual tutorial row is `{id: planner, ref: agents/planner.yaml}` and has no `spec`, so that YAML walk fails with `path segment 'spec' does not exist`. Use `.mas.agent[planner].…` instead.

You cannot select a model through `.mas.spec`. Use `--model`, or the experiment's `models:` slots.

Resolved infra exposes its ordered middleware chain as `spec.interceptors`, including each entry's `middleware`, `applies_to`, and typed `params`:

```bash
mas-ctl chat agent.yaml --infra-ref infra/llm-cache.yaml \
  --override '.infra.spec.interceptors[middleware=llm_cache].params.allow_write=true' \
  --override '.infra.spec.interceptors[middleware=llm_cache].params.raise_on_miss=false'
```

Infra overrides apply after merging infra refs, interceptors, and runtime profiles, and they keep the other resolved resources. Provider settings live under `spec.proxy`, MCP servers under `spec.tool_servers[id=...]`, A2A peers under `spec.endpoints["name"]`, and engine policy under `spec.runtime_engine`. See [override-stack.yaml](../schemas/examples/infra/override-stack.yaml) for a complete example.

## Path grammar

```text
assignment := path "=" yaml-value
path       := ["."] node ("." node)* ("." segment)+
node       := type | type "[" id "]" | type "[*]"
segment    := identifier | identifier "[" selector "]"
selector   := integer | identity | id | "*" | quoted-key
identity   := key "=" yaml-scalar
```

`type:path` is an alias of `type.path`. More paths:

```text
.agent.spec.models[id=main].model
.mas.agent[planner].spec.context.role
.mas.spec.workflow.entry
.infra.spec.tool_servers[id=mcp].port
.experiment.experiment.run.n_runs
.config.defaults.model
```

Prefer identity selectors to numeric indexes. An index moves when an overlay adds or removes list entries. A wildcard expands in manifest order and applies the same operation to every match.

A path may create missing **keys** when the target schema documents them: declared properties, `patternProperties`, `additionalProperties` maps, and matching `if`/`then` plugin fields. Unknown names fail. Updating a field through an identity selector (`models[id=ghost].max_tokens`) requires that row to exist and to be a list; assigning the whole row (`tool_servers[id=mcp]={…}`) may add it. `$op add` appends to a list and is rejected on a scalar. So `.agent.spec.models[*].max_tokens=4096` adds `max_tokens` to every model row, `.agent.metadata.version=1.0.0` fills an omitted envelope field, and a typo such as `max_tokenz` is rejected.

## Values

The value is parsed as YAML after shell parsing:

```bash
--override '.agent.spec.models[*].max_tokens=4096'       # integer
--override '.agent.spec.models[*].temperature=0.2'       # number
--override '.agent.spec.description="qa agent"'          # string
--override '.agent.spec.context.tags=[qa,fast]'          # list
--override '.agent.spec.design_pattern={type: cot}'      # map
```

Booleans (`true` and `false`) parse the same way for boolean fields. The merged result is validated again against the target schema.

## Operators

CLI overrides accept the same `$op` forms as file overlays. Each form works where the target field's `x-merge` strategy supports it.

```bash
# Replace a collection
--override '.agent.spec.tools={"$op":{"replace":["calculator"]}}'

# Append to a list
--override '.agent.spec.tools={"$op":{"add":["web-search"]}}'

# Remove from a list
--override '.agent.spec.tools={"$op":{"remove":["calculator"]}}'

# Clear a collection
--override '.agent.spec.skills={"$op":{"clear":true}}'

# Merge a mapping
--override '.agent.spec.context={"$op":{"merge":{"role":"reviewer"}}}'
```

A raw value follows the field's normal overlay behaviour. A raw list replaces a `list_ops` field. A named list uses the identity strategy its schema declares.

## Group overrides in an overlay file

Put several overrides in an `Overlay` manifest when scenarios or commands should reuse them. The ordered list `spec.overrides` takes the same session-tree syntax as the CLI. Items apply after the manifest's regular `spec.patch`.

To patch one MAS agent, set Overlay `target.kind: Agent` and `target.name` — that is `.mas.agent[reviewer]`:

```yaml
apiVersion: mas/v1
kind: Overlay
metadata:
  name: review-policy
spec:
  target: {kind: Agent, name: reviewer}
  patch: {}
  overrides:
    - '.mas.agent[reviewer].spec.design_pattern=cot'
```

Omit `target.name` and write `.mas.agent[*].spec…` to patch every nested agent.

`target.kind` must match the session type of every `spec.overrides` expression. Selectors act on entries already in the effective document. An unmatched selector is an error. The overlay is validated as a normal `mas/v1` Overlay, and source manifests stay unchanged.

## Priority

Later layers win:

```text
workspace/config defaults
  < base manifest
  < file overlays in argument order
  < legacy shortcut flags (`--bind` expands to `--override infra:…` here)
  < --override arguments in argument order
  < runtime defaults for fields still absent
```

The later CLI override wins when the field strategy allows several writes. Conflicting wildcard operations and unsupported operations fail. They never fall back to dictionary order.

## Legacy shortcuts

These flags still work:

| Flag | Equivalent |
| --- | --- |
| `--tool NAME` | Add `NAME` to `.agent.spec.tools`. |
| `--skill NAME` | Add `NAME` to `.agent.spec.skills`. |
| `--memory ID` | Replace `.agent.spec.memory`. |
| `--set KEY=VALUE` | Set an agent context value. |
| `--bind NAME=URI` | Shortcut for Application and ToolServerRegistry `usage: use` rows on the merged infra document. |
| `--max-tokens N` | Alias of `--override '.agent.spec.models[*].max_tokens=N'`. Placed before explicit `--override` values (`chat`, `tui`). |
| `--overlay PATH` | Load a file overlay before CLI values. |
| `--scenario-id ID` | Select a benchmark scenario. |
| `--dataset-item ID` | Select a benchmark dataset item. |

Use `--override` when the path, root, selector, or merge operation matters.

## Safety

Overrides never rewrite source YAML files or `config.yaml`. They patch the effective in-memory document for one command.

For a MAS agent, use the typed id, not a list position:

```bash
mas-ctl run-mas mas.yaml \
  --override '.mas.agent[planner].spec.context.role=planner'
```

That includes agents referenced by `ref:`. See [Agents in a MAS](#agents-in-a-mas).

Invalid paths, selectors, operations, or final schema values fail before the runtime starts. The error shows the override expression and the failing path.

A schema can mark a field `x-cli: {allowed: false}` to protect generated or runtime-owned values. Such a path is rejected before the merge.

## Related

- [mas-ctl command reference](mas-ctl.md)
- [Overlay manifest](../manifests/overlay.md)
- [Schema index](../references/schemas.md)
- [Experiment manifest](../manifests/experiment.md)
- [Infra manifest](../manifests/infra.md)
