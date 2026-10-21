<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# ToolContract

**Module:** `mas.runtime.contracts.tool_contract`

A *contract* is the declared inputs, outputs, and rules of a boundary that the runtime checks. `ToolContract` is the boundary for **listing** and **calling** tools. The engine, the local plugin, and the MCP plugin all dispatch through `call_tool(tool_name, arguments)`. MCP (Model Context Protocol) is the standard protocol for remote tool servers.

## The problem

An agent calls tools that live in different places: in-process Python classes, skills, and remote MCP servers. The caller needs one way to list and invoke them all.

```python
provider.call_tool("web-search", {"query": "Apple stock price"})
```

## How a call flows

1. The LLM (or a test) emits a tool name and a JSON object of arguments.
2. The runtime registry looks up the name and forwards the call to the owning plugin.
3. For local tools, `ToolContract.call_tool` validates the arguments and calls `execute(**arguments)`.
4. For MCP, `MCPToolProvider.call_tool` sends MCP `tools/call` with the same name and arguments.

`execute(**kwargs)` is the in-process helper for a **single** tool class. Composite providers (local registry, MCP, skill tools) override `call_tool` and skip `execute`.

MCP `tools/call` **is** this method. Extra MCP SDK parameters and result parts are optional kwargs or optional envelope keys on the same call. Otherwise they are connection policy on infra. They are not a second invocation API.

## Required surface

| Method | Required payload |
|--------|------------------|
| `list_tools()` | `[{name, description, parameters}]` |
| `call_tool(tool_name, arguments)` | dict or envelope with `result` (and `is_error` on failure) |
| `execute(**kwargs)` | local single-tool helper only |

`invoke_call_tool` forwards optional kwargs only when the callee declares them or takes `**kwargs`.

## Optional advertise keys (`list_tools` / `kind: Tool`)

A key appears only when set. Schema: [`tool.schema.yaml`](../schemas/runtime/tool.schema.yaml). Guide: [tool.md](../manifests/tool.md).

| Contract getter / YAML | `list_tools` key |
|------------------------|------------------|
| `get_title()` / `spec.title` | `title` |
| `get_output_schema()` / `spec.output_schema` | `output_schema` |
| `is_idempotent()` / `spec.idempotent` | `idempotent` |
| `is_read_only()` / `spec.read_only` | `read_only` (`None` = omit) |
| `is_destructive()` / `spec.destructive` | `destructive` |
| `is_open_world()` / `spec.open_world` | `open_world` |
| `get_icons()` / `spec.icons` | `icons` |
| `get_task_support()` / `spec.task_support` | `task_support` |
| `get_meta()` / `spec.meta` | `meta` |
| `get_semantics()` / `spec.semantics` | `semantics` |
| `get_timeout_seconds()` / `spec.timeout_seconds` | `timeout_seconds` |

When `idempotent`, `read_only`, `destructive`, or `open_world` is set, the entry also carries MCP-style `annotations`: `idempotentHint`, `readOnlyHint`, `destructiveHint`, `openWorldHint`.

## Optional `call_tool` kwargs

```python
call_tool(
    tool_name,
    arguments,
    timeout_seconds=None,
    progress_callback=None,
    cancel_event=None,          # accepted; no MCP SDK mapping
    meta=None,
    input_responses=None,
    request_state=None,
    allow_input_required=None,
    allow_claimed=None,
    ctx=None,
    user="",
)
```

The default `ToolContract.call_tool` ignores these kwargs and runs `execute(**arguments)`. The MCP client forwards the ones the SDK supports: `read_timeout_seconds`, `progress_callback`, `meta`, and the elicitation flags.

## Optional result keys (`ToolResultEnvelope`)

The usual payload is `{status, result, is_error}` plus `result_mode` (`inline`, `stream`, or `session`) and `execution_mode` (`sync` by default). `ToolResultEnvelope` lives in the same module.

These keys appear only when set: `events`, `session_id`, `session_status`, `metadata`, `content`, `structured_content`, `result_type` (omitted when `complete`), `meta`.

## Not on ToolContract: infra `ToolServerRegistry`

These fields say **where** the server lives. They do not say **what** the tool is or how to invoke it.

| Field | Place |
|-------|--------|
| `url` / `endpoint`, `transport`, `command` / `args` / `env` / `cwd` | Infra tool server |
| HTTP `headers` (secrets: `env:VAR`; omit when unused) | Infra tool server |
| Connection `timeout` | Infra tool server |
| `follow_pagination` (flatten `nextCursor`; default `true`) | Infra tool server |
| `cache_ttl_ms` / `cache_scope` (list-result cache policy) | Infra tool server |

A caller that uses only the contract sees `list_tools()` and `call_tool(name, arguments)`. It does not see URL, headers, pagination, or cache policy.

The agent spec does not declare tool providers. The infra tool-server entry declares `protocol: mcp`. The runtime discovers the tool names from the server, unless infra supplies an explicit `tools` list.

Reference: [infra.md, ToolServerRegistry](../manifests/infra.md#toolserverregistry) and [tool-server-registry.md](tool-server-registry.md). Example: [`library-samples/infra/mcp-localhost.yaml`](https://github.com/outshift-open/mas-lab/blob/main/library-samples/infra/mcp-localhost.yaml).

## See also

- [tool.md](../manifests/tool.md)
- [agent.md](../manifests/agent.md)
- [tool-server-registry.md](tool-server-registry.md)
- [Tutorial 4: MCP tools](../tutorials/04-mcp-tools/README.md)
- MCP mapping: `library_ioa.plugins.mcp.contract` (package `mas-library-ioa`)
