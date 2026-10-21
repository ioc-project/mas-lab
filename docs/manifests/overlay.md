<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Overlay manifest (`kind: Overlay`)

**Package:** `mas-runtime` · **Schema:** `overlay.schema.yaml` · **apiVersion:** `mas/v1`

An **overlay** (`kind: Overlay`) declares a patch to one **Agent**, **MAS**, **Infra**, **Flavour**, **Experiment**, or in-memory **Workspace** document. You change the base manifest without copying it.
Write one to change a base manifest for a single run or a **scenario**. A scenario is a named variant in an experiment.
An experiment lists overlays per scenario (`scenarios[].overlays`). The CLI applies them with `-o path/to/overlay.yaml`.

Minimal working overlay:

```yaml
apiVersion: mas/v1
kind: Overlay
metadata:
  name: reviewer-role
spec:
  target:
    kind: Agent
  patch:
    context:
      role: |
        Review answers for factual errors.
```

An overlay is a partial or full override of one target document. Benchmark scenarios, runtime overlays (`-o`), and the UI overlay builder all use this format.
There is no `spec.contracts` list. A contract is a declared boundary the runtime checks, such as a tool call or a delegation. Express contract boundaries through schema fields such as `design_pattern`, `governance`, `observability`, `workflow`, and `tools`.

## Contents

- [`metadata` fields](#metadata-fields)
- [CLI overlays](#cli-overlays)
- [Selector overrides in an overlay](#selector-overrides-in-an-overlay)
- [Shape](#shape)
- [Target contract](#target-contract)
- [Patch contract](#patch-contract)
- [Merge semantics](#merge-semantics)
- [Inspect the compiled spec](#inspect-the-compiled-spec)
- [Experiment linkage](#experiment-linkage)

## `metadata` fields

| Field | Default | Description |
| --- | --- | --- |
| `name` | `""` | Overlay identifier, usually the file stem (`baseline`). |
| `id` | `""` | Alternative identifier that lab overlays use. Set `name` or `id`, not both. |
| `description` | `""` | Human-readable description. |
| `namespace` | `global` | Scope of the overlay: `global` or an app name. |

No `metadata` field is required. Extension keys (`x-*`) are allowed.

## CLI overlays

Command-line overrides become the same canonical Overlay document. The CLI applies them after all `-o` overlays:

```bash
mas-ctl chat agent.yaml --override 'agent:spec.context.role=reviewer'
mas-ctl run-mas mas.yaml \
  --override 'mas:spec.agency.agents[id=qa].spec.design_pattern=cot'
```

File overlays and CLI overrides share the `$op` operators and `x-merge` strategies. Selector syntax: [CLI overrides](../cli/overrides.md).

## Selector overrides in an overlay

An overlay can group the same root-qualified assignments that the CLI accepts under `spec.overrides`. They apply in list order after `spec.patch`.
Identity selectors and `[*]` wildcards update several existing list entries without repeating the surrounding manifest structure:

```yaml
apiVersion: mas/v1
kind: Overlay
metadata:
  name: reviewer-policy
spec:
  target: {kind: MAS}
  patch: {}
  overrides:
    - 'mas:spec.agency.agents[id=reviewer].spec.design_pattern=cot'
    - 'mas:spec.agency.agents[*].spec.context.environment=staging'
```

The root must match `spec.target.kind` (`mas`, `agent`, `infra`, `flavour`, `experiment`, or `workspace`). Values use YAML syntax, including `$op` collections. A selector that matches no entry is an error.

---

## Shape

```yaml
apiVersion: mas/v1
kind: Overlay
metadata:
  name: cot-ablation
spec:
  target:
    kind: MAS          # Agent | MAS | Infra | Flavour | Experiment | Workspace
    name: optional-filter
  patch:
    workflow:
      entry: broker
      nodes:
        - id: broker
          delegates_to: [researcher]
    agents:
      $entry:            # workflow.entry after this overlay's workflow patch
        design_pattern: { type: cot, params: { max_steps: 10 } }
      $not-entry:        # every agency agent except $entry
        skills: { "$op": { add: [answer-formatting] } }
      $delegates:        # workflow.nodes[$entry].delegates_to
        skills: { "$op": { add: [peer-note] } }
      broker:
        tools: { "$op": { remove: [web-search] } }
    params:              # MAS spec.params: free-form string values
      region: eu-west-1
```

`spec.tools` (outside `patch`) is a separate, scenario-level tool injection field. It is a list of semantic tool names or `{ref: path}` objects, merged onto the target agents.

`patch.agents.<id>` and the `$entry`-style keys accept a subset of Agent fields: `design_pattern`, `context_manager`, `assembler`, `llm`, `memory`, `tools`, `skills`, `context`, `description`, and `ref`.
To patch other agent fields (`governance`, `models`, `control`, ...) on agents inside a MAS, use a `target.kind: Agent` overlay. Add `target.name` to select one agent.

---

## Target contract

`spec.target` is required. It controls validation and merge dispatch.

| Field | Required | Semantics |
| --- | --- | --- |
| `kind` | yes | Target document kind: `Agent`, `MAS`, `Infra`, `Flavour`, `Experiment`, or `Workspace`. |
| `name` | no | For an `Agent` overlay applied to a MAS, selects one agency row by `id`, `name`, or `metadata.name`. For a standalone target, the command already selects the document. |

The target kind must match the root of every `spec.overrides` expression.
The overlay fails, rather than being ignored, when it has an unsupported kind, an unknown target name, or a selector with no match.

## Patch contract

`spec.patch` is required and must be a mapping. The target-specific schema fragment defines its allowed fields:

| Target kind | Patch schema | Merge behavior |
| --- | --- | --- |
| `Agent` | [`overlay-agent-patch.schema.yaml`](../schemas/runtime/fragments/overlay-agent-patch.schema.yaml) | Agent fields, `$op`, and `x-merge` metadata |
| `MAS` | [`overlay-mas-patch.schema.yaml`](../schemas/runtime/fragments/overlay-mas-patch.schema.yaml) | `agency`, `workflow`, `agents`, `telemetry`, `params`, `capabilities`, `intent` |
| `Infra` | [`overlay-infra-patch.schema.yaml`](../schemas/runtime/fragments/overlay-infra-patch.schema.yaml) | RFC 7396 merge of the Infra `spec` |
| `Flavour` | [`overlay-flavour-patch.schema.yaml`](../schemas/runtime/fragments/overlay-flavour-patch.schema.yaml) | Deployment posture (`agent_comm`, `telemetry`, `observability`, `control`, `tools`, ...) and plugin collection operators |
| `Experiment`, `Workspace` | none in the overlay schema (any mapping) | In-memory RFC 7396 merge |

A plain value follows the field's declared merge strategy. Use an explicit `$op` mapping for collection operations: `replace`, `add`, `remove`, `clear`, or `merge`.
Schema validation rejects unknown fields, so a typo cannot become a new configuration field.

## Merge semantics

Merging follows RFC 7396 (JSON Merge Patch). A mapping merges key by key, any other value replaces, and `null` deletes the key. Later overlays in a scenario stack win on conflicting keys.

What each target can patch:

- **Flavour** overlays cannot select models or API keys. They change deployment posture only.
- **Agent** overlays can patch every Agent `spec` field except `debug`. That includes `models[]` rows (sampling, `reasoning`, `extra`), `description`, `behavior`, and `tools_ref`.
- `spec.models[]` deep-merges by `id`, then `model`, then `main`.
- Access fields (`api_base`, `api_key_env`) stay on infra.

Examples: [llm-reasoning.yaml](../schemas/examples/overlays/llm-reasoning.yaml), [llm-sampling.yaml](../schemas/examples/overlays/llm-sampling.yaml).

### Collection operations

List fields such as `tools` accept an explicit `{"$op": {replace|add|remove|clear: [...]}}` form. A plain list is an implicit replace. `[]` clears the list.

`context` (prompt and role text) gets the same `$op` form, per chunk name. A `spec.context.<key>` value is a plain string, a `{ref}`, or a list of fragments. A plain string or `{ref}` replaces the whole chunk. A `$op` patch on a fragment list appends or drops one line without restating the rest of the base prompt:

```yaml
patch:
  context:
    role:
      $op:
        add:
          - "Confirm the budget before booking."
        # remove:
        #   - "Confirm the budget before booking."
        # replace:
        #   - "Whole new role text."
        # clear: true
```

Document-level `x-*` keys on the overlay (and under `spec.patch`) are copied onto the target document root, not into `spec`. Runtime merge of spec fields ignores them. Later overlays win, and nested dicts merge per RFC 7396.

### MAS overlays: agent keys and participants

On a MAS overlay, reserved `patch.agents` keys expand onto agency ids after this overlay's `workflow` patch:

| Key | Expands to |
| --- | --- |
| `$entry` | `spec.workflow.entry` |
| `$all` | every `spec.agency.agents` id |
| `$not-entry` | every agency id except `$entry` |
| `$delegates` | `workflow.nodes[$entry].delegates_to` |

The keys compose in that order. Then a named id wins. These cases are errors:

- `$entry` and the same id as a key.
- `$entry` or `$not-entry` without `spec.workflow.entry`.
- An unknown `$entry` id.
- A missing entry.

`$entry` lets a design-pattern overlay avoid naming the entry agent.

`patch.agents.$op` also edits the participant list at `spec.agency.agents`. `add` and `replace` entries are `{id, ref}` agent references. `remove` is a list of agent ids. `clear: true` empties the list:

```yaml
target:
  kind: MAS
patch:
  agents:
    $op:
      remove: [schedule_agent]
      add:
        - id: generalist
          ref: ./agents/generalist.yaml
```

The former `agents_add` and `agents_remove` patch fields are not supported. Use `patch.agents.$op`.

### Agent overlays applied to a MAS

A `target.kind: Agent` overlay applied to a MAS selects nested agency rows: `spec.agency.agents[]`, or `spec.agents[]` when agency is absent.
Optional `spec.target.name` selects one row by `id`, `name`, or `metadata.name`. When you reuse one overlay on both an agent and a MAS, the agency row id should match the agent YAML `metadata.name`.
Zero matches is an `OverlayTargetError`: the overlay must attach somewhere.

This is how labs reuse an Agent overlay such as `with-guardrail` on trip-planner. Compose and `run-mas` keep each agency row as `{id, ref}` and merge the overlay onto each referenced Agent YAML when the file loads.

- `governance` and `observability` union by plugin id. They do not replace the agent's existing lists.
- `skills` and `tools` follow one rule. A `{"$op": {"add": [...]}}` value appends to what the agent YAML declares. A raw list replaces it, and `[]` clears it.

A base MAS cannot declare skills, tools, or context on an agency row. Those fields live in the agent manifest. To change them per agent in a scenario, target the agent with `$entry`, `$all`, `$not-entry`, `$delegates`, or a named id.
`spec.models` on the MAS is the one attribute that agents inherit from the MAS. An unresolved `any` comes from the experiment, the workspace, or the CLI, never from a silent package default.

Lab and bench MAS runs already attach a shared native sink. A fanned-out default `native` list joins that sink and does not open a second `events.jsonl`. Custom observability paths stay per agent.

---

## Inspect the compiled spec

```bash
mas-ctl compile agent.yaml -o overlays/tools.yaml -o overlays/skills.yaml
mas-ctl compile mas.yaml -o overlays/linear.yaml -O ./compiled/
```

`--layout bundle` inlines MAS agents into one YAML file. The default for a directory `--output` keeps `mas.yaml` plus `agents/*.yaml`. See [compile](../cli/compile.md).

---

## Experiment linkage

An experiment stacks overlays per scenario:

```yaml
scenarios:
  - id: baseline
    overlays: {logic: [baseline], control: [], infra: []}
  - id: cot
    overlays: {logic: [cot, no-tools], control: [], infra: []}
```

Agent overlays cannot patch tool provider or connection configuration. Remote MCP endpoints belong on infra [`ToolServerRegistry`](../references/tool-server-registry.md). Agent and overlay schemas reject `providers[]`. See [tool.md](tool.md).

---

## See also

- [plugin-bindings.md](plugin-bindings.md) — string shorthand vs `{type, params}`
- [experiment.md](experiment.md) — scenario overlay stacks
- [compile](../cli/compile.md) — dump the resolved spec
- [ToolContract](../references/tool-contract.md)
- [ToolServerRegistry](../references/tool-server-registry.md)
