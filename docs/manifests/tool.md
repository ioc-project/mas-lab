<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Tool manifest (`kind: Tool`)

**Package:** `mas-runtime` | **Schema:** [`tool.schema.yaml`](../schemas/runtime/tool.schema.yaml) | **apiVersion:** `mas/v1`

A **Tool** manifest declares **one** tool: its name, parameters, optional protocol extras, and an `impl` pointer to the Python class that runs it.
Write one when an agent needs an application tool that the runtime does not ship.
Agents reference the file with `spec.tools[].ref`.
The class behind `impl` implements [`ToolContract`](../references/tool-contract.md).

```yaml
apiVersion: mas/v1
kind: Tool
metadata:
  name: calc
spec:
  description: Evaluate an arithmetic expression.
  parameters:
    - {name: expression, type: string, required: true}
  impl:
    module_path: ./calc_tool.py
    class_name: CalcTool
```

`apiVersion`, `kind`, `metadata`, and `spec` are required, and so is `metadata.name`.
Every `spec` field is optional in the schema, so a document that declares only `parameters` and `impl` is valid.
To load an executable tool through `spec.tools[].ref`, `spec.impl.module_path` is required.
The loader rejects a referenced Tool without it.

## Shape

This example shows the common fields:

```yaml
apiVersion: mas/v1
kind: Tool
metadata:
  name: web-search          # call name (required)
  description: Catalogue one-liner
spec:
  description: >            # LLM / tool-server description
    Search the web. Call this when the user needs current information.
  parameters:
    - name: query
      type: string
      required: true
  returns:                  # short daily-ops return note
    type: object
    description: Dict with query, results, cached
  impl:
    kind: python
    module_path: ./web_search_tool.py
    class_name: WebSearchTool
```

[`schemas/examples/tools/annotated.tool.yaml`](../schemas/examples/tools/annotated.tool.yaml) shows every optional advertise field.

## Required vs optional

| Field | Required | Notes |
|-------|----------|--------|
| `metadata.name` | yes | Call name (`call_tool` first argument). Lowercase letters, digits, `_`, `-`; starts with a letter or digit; unique within an agent |
| `metadata.description` | no (default `""`) | Catalogue one-liner; not sent to the LLM |
| `metadata.version` | no (default `"0.1.0"`) | Semver string |
| `metadata.tags` | no (default `[]`) | Free-form labels |
| `spec.description` | no (default `""`) | Text the LLM reads |
| `spec.parameters[]` | no (default `[]`) | Rendered as JSON Schema `properties`; see [Parameters](#parameters) |
| `spec.returns` | no | Short `{type, description}`, not a full JSON Schema. `type` is a JSON type or `any` (default) |
| `spec.idempotent` | no (default `false`) | Safe to re-issue with the same arguments. Infra tool retries with `require_idempotent: true` retry only idempotent tools ([reliability.md](../references/reliability.md)) |
| `spec.timeout_seconds` | no (default `30`, range 1-3600) | Advertised wall-clock limit. The local tool provider does not enforce it in this release |
| `spec.title` | no | Display name distinct from `metadata.name` |
| `spec.impl.kind` | no (default `python`) | `python`, `remote_tool`, or `openapi` |
| `spec.impl.module_path` | yes when `impl` is present | Python module or file path; hidden from the LLM |
| `spec.impl.class_name` | no (default `null`) | Implementation class; may be auto-discovered |
| `spec.impl.params` | no (default `{}`) | Constructor arguments for the implementation; an agent's `spec.tools[].params` merge on top |
| `spec.output_schema` | no | Full JSON Schema for the result (MCP `outputSchema`) |
| `spec.read_only` | no | Omit = unspecified, not `false` |
| `spec.destructive` | no | Omit = unspecified |
| `spec.open_world` | no | Omit = unspecified |
| `spec.icons[]` | no | `{src, mime_type, sizes, theme}` |
| `spec.task_support` | no | `forbidden` \| `optional` \| `required` |
| `spec.meta` | no | Advertise `_meta` for protocol adapters |
| `spec.semantics` | no | Trace and metrics interpretation (`concept`, `op`, `subject_arg`). Composite providers set it per `list_tools()` entry instead |
| `spec.execution_mode` | no (default `sync`) | `sync` \| `async` \| `realtime`; advertise metadata |
| `spec.result_mode` | no (default `inline`) | `inline` \| `stream` \| `session`; advertise metadata |
| `spec.events` | no | Stream and session event descriptors |
| `spec.tool_category` | no | Catalogue grouping |

`read_only`, `destructive`, and `open_world` use three-state YAML.
Omit the key to leave the hint unspecified.
`false` is an explicit hint.

### Parameters

Each `spec.parameters[]` entry becomes one JSON Schema property of the tool's input:

| Field | Required | Notes |
|-------|----------|-------|
| `name` | yes | Python identifier (`^[a-zA-Z_][a-zA-Z0-9_]*$`) |
| `type` | yes | `string`, `integer`, `number`, `boolean`, `array`, or `object` |
| `description` | no (default `""`) | Shown to the LLM |
| `required` | no (default `false`) | `true` adds the name to JSON Schema `required[]` |
| `default` | no | Value used when the argument is absent |
| `enum` | no | Allowed values |
| `examples` | no | Representative values shown to the LLM |

## What does not belong here

Connection and list-transport policy are **infra**, not part of the tool.

| Concern | Manifest |
|---------|----------|
| URL, SSE vs stdio, HTTP headers, call timeout for a remote server | `infra/v1` `kind: ToolServerRegistry` |
| Remote endpoint and protocol | `infra/v1` `ToolServerRegistry` |
| Implementation class | `spec.impl` on this document |

A `ToolServerRegistry` is the infra manifest that lists remote tool servers.
See [infra.md: ToolServerRegistry](infra.md#toolserverregistry).

## Omitted keys

- A Tool YAML may omit every optional key. `additionalProperties: false` rejects only **unknown** keys.
- `ToolDocument.from_dict` defaults unset advertise fields to empty or `None`.
- `list_tools()` returns `{name, description, parameters}` when the optionals are unset. It merges YAML extras onto that dict when present.
- `semantics` is copied onto `ExchangeRecord` for traces and metrics. The runtime strips it from the LLM wire payload.

## Referencing a Tool from an agent

`spec.tools[].ref` accepts three forms:

- A path relative to the agent manifest, such as `./tools/calc.tool.yaml`.
- A `pkg://<package>/<resource>` path.
- A library ref `<library>:<path>`, such as `samples:tools/calc.tool.yaml`.

The loader resolves paths under the manifest's containment roots.

```yaml
spec:
  tools:
    - ref: ./tools/calc.tool.yaml
```

### Not used yet

`bundle://<bundle>/<tool>` refs pass validation, but the local tool provider does not resolve them in this release.
Point at the Tool file instead.
See [agent.md: Tool](agent.md#tool).

## See also

- Schema: [`tool.schema.yaml`](../schemas/runtime/tool.schema.yaml)
- Bundle entries: [`tool_bundle.schema.yaml`](../schemas/runtime/tool_bundle.schema.yaml)
- Contract: [tool-contract.md](../references/tool-contract.md)
- Infra: [tool-server-registry.md](../references/tool-server-registry.md)
- Related: [Agent](agent.md), [Infra `ToolServerRegistry`](infra.md#toolserverregistry), [ToolBundle](runtime.md#toolbundle)
- Tutorials: [Building an agent](../tutorials/01-building-an-agent/README.md), [MCP tools](../tutorials/04-mcp-tools/README.md)
