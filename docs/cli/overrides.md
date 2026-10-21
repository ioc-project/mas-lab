<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# CLI Override Reference

`--override` changes one field of a manifest from the command line, without writing an overlay file. Use it for a quick patch to a single run.

Terms: a *manifest* is a YAML file that declares an agent, a MAS, or an experiment. An *overlay* is a small YAML file merged on top of a manifest. See the [glossary](../glossary.md).

## The three overrides most people run

```bash
mas-ctl chat agent.yaml --override 'agent:spec.context.role="security reviewer"'   # replaces the agent role for this run
mas-ctl chat agent.yaml --override 'agent:spec.models[*].max_tokens=4096'           # caps output tokens on every model entry
mas-ctl run-mas mas.yaml --override 'mas:spec.workflow.entry=moderator'             # starts the workflow at another agent
```

An override never edits a file on disk. It patches the in-memory manifest for one command.

## Syntax

```text
--override ROOT:PATH=VALUE
```

- `ROOT` names the document to patch (`agent`, `mas`, `infra`, `flavour`, `experiment`, `workspace`).
- `PATH` walks the YAML tree with dots. A *selector* in square brackets picks list entries.
- `VALUE` is a YAML value.

Quote the whole assignment in the shell. Quoting is required when it contains `$`, brackets, spaces, braces, or quotes.

The CLI converts each override into an `apiVersion: mas/v1`, `kind: Overlay` document. File overlays use the same merge and validation code.

## Selector examples

A selector chooses which list entry the path continues into.

| Goal | Override | Result |
| --- | --- | --- |
| Set a field | `agent:spec.context.role="reviewer"` | The agent role becomes `reviewer`. |
| Add to a list | `agent:spec.tools={"$op":{"add":["web-search"]}}` | `web-search` joins the tool list. |
| Remove from a list | `agent:spec.tools={"$op":{"remove":["calculator"]}}` | `calculator` leaves the tool list. |
| Select by id | `agent:spec.models[id=main].model="gpt-test"` | Only the model entry whose `id` is `main` changes. |
| Select by position | `mas:spec.agency.agents[0].spec.design_pattern=cot` | Only the first agency entry changes. Position moves when the list changes, so prefer an id. |
| Wildcard | `agent:spec.models[*].max_tokens=4096` | Every model entry gets `max_tokens: 4096`. |

A selector that matches nothing fails with an error that names the requested identity.

## Task guide

### Change a field on a standalone agent

```bash
mas-ctl chat agent.yaml --override 'agent:spec.context.role="security reviewer"'
```

### Change a field on a MAS

```bash
mas-ctl run-mas mas.yaml --override 'mas:spec.workflow.entry=moderator'
```

### Change one inline agent inside a MAS

```bash
mas-ctl run-mas mas.yaml \
  --override 'mas:spec.agency.agents[id=reviewer].spec.design_pattern=cot'
```

This works only for agency entries that carry an inline `spec`. See [Agents in a MAS](#agents-in-a-mas).

### Change every inline agent

```bash
mas-ctl run-mas mas.yaml \
  --override 'mas:spec.agency.agents[*].spec.design_pattern=cot'
```

### Inspect the effect without running

```bash
mas-ctl compile agent.yaml --override 'agent:spec.models[*].max_tokens=4096'
```

`compile` prints the resolved Agent manifest. See [compile](compile.md).

### Change an experiment setting

```bash
mas-lab benchmark run experiment.yaml \
  --override 'experiment:experiment.run.n_runs=1' \
  --override 'experiment:experiment.dataset.limit=2'
```

### Change a workspace value for one run

```bash
mas-ctl chat agent.yaml --override 'workspace:defaults.model="gpt-test"'
```

The workspace `config.yaml` stays unchanged.

### Address a key that contains dots

```bash
mas-ctl chat agent.yaml --override 'agent:spec.context["key.with.dots"]=value'
```

The quoted key keeps dots and brackets inside one map key.

### Set observability on the flavour

```bash
mas-ctl chat agent.yaml --override 'flavour:spec.observability=[native]'
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

## Roots and the manifest tree

The path starts at a named root. The root picks the document. The path then walks that document's YAML tree.

```text
workspace/config.yaml
  |
  +-- experiment.yaml ---------------------- experiment:experiment.*
  |                                            |
  |                                            +-- application (the MAS under test)
  |                                            +-- scenarios
  |                                            +-- dataset (items)
  |                                            +-- run / post (hooks)
  |
  +-- mas.yaml ----------------------------- mas:spec.*
  |       |
  |       +-- spec.agency.agents[id=planner]
  |                    |
  |                    +-- ref: agents/planner.yaml
  |                    +-- inline spec
  |
  +-- agent.yaml --------------------------- agent:spec.*
  |       |
  |       +-- tools, skills, models
  |       +-- context, memory, governance
  |
  +-- infra refs --------------------------- infra:spec.*
  +-- flavour ------------------------------ flavour:spec.*
```

The root is explicit because `spec.tools` can belong to a standalone agent, an entry agent inside a MAS, or an infrastructure provider. The command decides which roots it loads. Naming an unavailable root is an error, not a silent no-op.

| Root | Typical commands | Meaning |
| --- | --- | --- |
| `agent` | `chat`, `compile` | Standalone Agent manifest |
| `mas` | `run-mas`, `compose`, `compile` | MAS or Workflow manifest |
| `infra` | `chat`, `run-mas`, `compose` | Effective merged infrastructure |
| `flavour` | runtime composition | Effective deployment flavour |
| `experiment` | `mas-lab benchmark run` | Experiment before validation and expansion |
| `workspace` | `chat`, `run-mas` | In-memory effective workspace and config values |

Address an entry agent inside a MAS through the MAS root:

```text
mas:spec.agency.agents[id=planner].spec.tools
```

This differs from `agent:spec.tools`, which addresses a standalone Agent document.

### Agents in a MAS

A selector can only descend into what the document contains. The usual agency entry is `{id: planner, ref: agents/planner.yaml}`, as in the tutorials. It has no `spec`. So `…agents[id=planner].spec.tools` fails with `path segment 'spec' does not exist`.

To change a referenced agent, use one of these:

- an Agent overlay (`-o`, `target.kind: Agent`);
- an edit of the referenced manifest;
- a MAS-level field such as `mas:spec.workflow.…`.

You cannot select a model through the `mas:` root. Use `--model`, or the experiment's `models:` slots.

## Path grammar

```text
assignment := ROOT ":" path "=" yaml-value
path       := segment ("." segment)*
segment    := identifier | identifier "[" selector "]"
selector   := integer | identity | "*"
identity   := key "=" yaml-scalar
```

More paths:

```text
agent:spec.models[id=main].model
mas:spec.agency.agents[id=planner].spec.context.role
mas:spec.agency.agents[0].spec.memory
infra:spec.tool_servers[id=mcp].port
experiment:experiment.run.n_runs
workspace:defaults.model
```

Prefer identity selectors to numeric indexes. An index moves when an overlay adds or removes list entries. A wildcard expands in manifest order and applies the same operation to every match.

Intermediate path segments must already exist. The final key may be absent only when the target schema declares it. So `agent:spec.models[*].max_tokens=4096` adds `max_tokens` to every model row, and a typo such as `max_tokenz` is rejected.

## Values

The value is parsed as YAML after shell parsing:

```bash
--override 'agent:spec.models[*].max_tokens=4096'       # integer
--override 'agent:spec.models[*].temperature=0.2'       # number
--override 'agent:spec.description="qa agent"'          # string
--override 'agent:spec.context.tags=[qa,fast]'          # list
--override 'agent:spec.design_pattern={type: cot}'      # map
```

Booleans (`true` and `false`) parse the same way for boolean fields. The merged result is validated again against the target schema.

## Operators

CLI overrides accept the same `$op` forms as file overlays. Each form works where the target field's `x-merge` strategy supports it.

```bash
# Replace a collection
--override 'agent:spec.tools={"$op":{"replace":["calculator"]}}'

# Append to a list
--override 'agent:spec.tools={"$op":{"add":["web-search"]}}'

# Remove from a list
--override 'agent:spec.tools={"$op":{"remove":["calculator"]}}'

# Clear a collection
--override 'agent:spec.skills={"$op":{"clear":true}}'

# Merge a mapping
--override 'agent:spec.context={"$op":{"merge":{"role":"reviewer"}}}'
```

A raw value follows the field's normal overlay behaviour. A raw list replaces a `list_ops` field. A named list uses the identity strategy its schema declares.

## Group overrides in an overlay file

Put several overrides in an `Overlay` manifest when scenarios or commands should reuse them. The ordered list `spec.overrides` takes the same `ROOT:PATH=VALUE` syntax as the CLI. Items apply after the manifest's regular `spec.patch`.

```yaml
apiVersion: mas/v1
kind: Overlay
metadata:
  name: review-policy
spec:
  target: {kind: MAS}
  patch: {}
  overrides:
    - 'mas:spec.agency.agents[id=reviewer].spec.design_pattern=cot'
    - 'mas:spec.agency.agents[*].spec.design_pattern=cot'
```

The root must match the overlay target kind. Selectors act on entries already in the effective document. An unmatched selector is an error. The overlay is validated as a normal `mas/v1` Overlay, and source manifests stay unchanged.

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
| `--tool NAME` | Add `NAME` to `agent:spec.tools`. |
| `--skill NAME` | Add `NAME` to `agent:spec.skills`. |
| `--memory ID` | Replace `agent:spec.memory`. |
| `--set KEY=VALUE` | Set an agent context value. |
| `--bind NAME=URI` | Shortcut for Application and ToolServerRegistry `usage: use` rows on the merged infra document. |
| `--max-tokens N` | Alias of `--override 'agent:spec.models[*].max_tokens=N'`. Placed before explicit `--override` values (`chat`, `tui`). |
| `--overlay PATH` | Load a file overlay before CLI values. |
| `--scenario-id ID` | Select a benchmark scenario. |
| `--dataset-item ID` | Select a benchmark dataset item. |

Use `--override` when the path, root, selector, or merge operation matters.

## Safety

Overrides never rewrite source YAML files or `config.yaml`. They patch the effective in-memory document for one command.

For inline MAS agents, use an identity selector instead of list position:

```bash
mas-ctl run-mas mas.yaml \
  --override 'mas:spec.agency.agents[id=planner].spec.context.role=planner'
```

Agents referenced by `ref:` are not expanded before overrides run. See [Agents in a MAS](#agents-in-a-mas).

Invalid roots, paths, selectors, operations, or final schema values fail before the runtime starts. The error shows the override expression and the failing root and path.

A schema can mark a field `x-cli: {allowed: false}` to protect generated or runtime-owned values. Such a path is rejected before the merge.

## Related

- [mas-ctl command reference](mas-ctl.md)
- [Overlay manifest](../manifests/overlay.md)
- [Schema index](../references/schemas.md)
- [Experiment manifest](../manifests/experiment.md)
- [Infra manifest](../manifests/infra.md)
