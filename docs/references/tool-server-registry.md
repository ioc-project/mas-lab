<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# ToolServerRegistry (`infra/v1`)

**Kind:** `ToolServerRegistry` · **Schema:** [`infra-tool-server.schema.yaml`](../schemas/runtime/fragments/infra-tool-server.schema.yaml) · **Guide:** [infra.md](../manifests/infra.md#toolserverregistry)

An *infra bundle* is a set of `apiVersion: infra/v1` files that say where things are: the LLM endpoint, tool servers, and so on. A `ToolServerRegistry` is the part of that bundle that describes remote tools.

It is the **WHERE** of remote tools: protocol, URL, transport, headers, timeouts, stdio process, list pagination, and list-cache policy. It is **not** the tool advertise contract (`kind: Tool`). It is **not** a provider patch on the agent spec.

Invocation stays `ToolContract.call_tool(name, arguments)`. Connection policy belongs to infra, not to the agent declaration.

## Split of concerns

| Document | Owns |
|----------|------|
| `kind: Tool` | What the tool is: name, parameters, optional title, hints, `output_schema`. See [tool.md](../manifests/tool.md). |
| Agent spec | Logical tool requirements and app-level intent. Stays stable across transport changes. |
| `infra/v1` `ToolServerRegistry` | The remote endpoint, protocol, and transport details. |

The infra manifest keys servers by `tool_servers[].id`. The runtime resolves MCP servers from the active infra bundle and discovers the names they advertise.

## Example

```yaml
apiVersion: infra/v1
kind: ToolServerRegistry
metadata:
  name: mcp-localhost
spec:
  tool_servers:
    - id: localhost-mcp-tools          # required; matched by the active infra bundle
      name: localhost-mcp-tools        # display; defaults to id
      protocol: mcp                    # explicit protocol marker
      usage: use                       # use | deploy | use-and-deploy
      description: Local mas-mcp HTTP
      transport: streamable-http       # default
      url: http://127.0.0.1:9001/mcp
      timeout: 30                      # default
      follow_pagination: true          # default
      cache_scope: private
      # headers:
      #   Authorization: "env:MCP_AUTH_HEADER"
```

Canonical sample: [`library-samples/infra/mcp-localhost.yaml`](https://github.com/outshift-open/mas-lab/blob/main/library-samples/infra/mcp-localhost.yaml).

Attach it with `--infra-ref` (or workspace `infra_refs`). For the tutorial agent under `docs/tutorials/01-building-an-agent/`, the relative ref is `../../../library-samples/infra/mcp-localhost.yaml`. The preferred demo needs no MCP provider overlay on the agent.

## Field reference

`id` and `protocol` are required. Other fields are optional. Omitted keys take the defaults below.

### Identity

| Field | Default | Notes |
|-------|---------|--------|
| `id` | required | Stable infra key for this server. |
| `protocol` | required | `mcp` for an MCP server. `local` for an in-process source of app-local Tool manifests and skills. |
| `name` | `id` | Display name. Also accepted as a lookup alias. |
| `description` | omit | Human note. Not sent on the wire. |
| `usage` | `use` | Consume the MCP server, deploy it locally, or both. |

### HTTP / SSE

| Field | Default | Notes |
|-------|---------|--------|
| `transport` | `streamable-http` | `stdio` \| `streamable-http` \| `sse` \| `http` |
| `url` | none | HTTP/SSE endpoint, such as `http://127.0.0.1:9001/mcp`. Put the URL in the manifest. Optional override: `env:VAR\|http://127.0.0.1:9001/mcp`. |
| `host` / `port` | `127.0.0.1` / `9001` | Bind address when deploying the server. |
| `tools_dir` | implicit local `tools/` | Directory of Tool manifests (`protocol: local`, or served on deployment). |
| `skills_dir` | implicit local `skills/` | Skill directory (`protocol: local`). |
| `endpoint` | none | Alias for `url`. |
| `headers` | `{}` | HTTP headers. Omit when the server needs no auth. Secrets use `env:VAR` (an unset variable omits the header). Never hardcode tokens. |
| `timeout` | `30` | Per-call timeout in seconds (`read_timeout_seconds` on MCP). |

### stdio process

These fields apply when `transport` is `stdio`, or when `command` is set and `url` is not.

| Field | Default | Notes |
|-------|---------|--------|
| `command` | none | Executable. |
| `args` | `[]` | Argument list. |
| `env` | `{}` | Extra environment for the child. Secrets may use `env:VAR`. Other values belong in the manifest. |
| `cwd` | none | Working directory. |

```yaml
- id: local-stdio-mcp
  protocol: mcp
  transport: stdio
  command: mas-mcp
  args: [serve, --tool-manifest, library-samples/tools/web-search.tool.yaml, --transport, stdio]
```

### List transport

| Field | Default | Notes |
|-------|---------|--------|
| `follow_pagination` | `true` | Walk MCP `tools/list` `nextCursor` pages and flatten them. `false` reads the first page only. |
| `cache_ttl_ms` | omit | Client `tools/list` cache TTL in milliseconds. Omit to cache until `invalidate`. `0` disables the cache. |
| `cache_scope` | omit | `public` (one list for all users) or `private` (list cache keyed by user). |
| `tools` | `"*"` | Discover all names, or declare an explicit list in infra. |

These are **not** ToolContract fields. A tool's advertise document does not know how the client paginates or caches `tools/list`.

## Infra-owned routing

The agent does not carry provider connection details. For a remote tool, `ToolServerRegistry` owns protocol, URL, transport, headers, timeouts, and pagination. With `usage: deploy`, the same manifest describes the listener and the local Tool manifests it exposes.

The registry requires `id` and `protocol`. URL and other connection fields depend on the transport. MCP tool names are discovered by default. An infra `tools` list can pin the exposed names.

## What does not belong here

| Concern | Place |
|---------|--------|
| Tool name, parameters, title, hints, `output_schema` | `kind: Tool` |
| Remote connection policy (`protocol`, URL, headers, timeout, pagination) | `infra/v1` `ToolServerRegistry` |
| Local tool and skill paths | implicit `ToolServerRegistry` with `protocol: local` (`tools/`, `skills/`) |
| Per-tool wall-clock cap advertised to the LLM | `kind: Tool` `timeout_seconds` (optional) |
| Secrets | `headers: { Authorization: env:VAR }` (omit the block when unused) or `PersonalSecrets` |
| API / URL override | `url: env:VAR\|http://127.0.0.1:9001/mcp`. The manifest value is the default. The env var wins when set. |

## See also

- [infra.md](../manifests/infra.md#toolserverregistry): kinds table and sample.
- [tool.md](../manifests/tool.md): the advertise contract.
- [tool-contract.md](tool-contract.md): `call_tool`.
- [Tutorial 4: MCP tools](../tutorials/04-mcp-tools/README.md)
- Schema: [`infra.schema.yaml`](../schemas/runtime/infra.schema.yaml)
