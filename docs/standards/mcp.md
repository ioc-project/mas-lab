<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# MCP

<p class="eyebrow">Open standards</p>

[Model Context Protocol](https://modelcontextprotocol.io/) (MCP) lets an agent
call tools hosted by a separate server. The agent still calls
`ToolContract.call_tool(name, arguments)`. Infra decides whether a local
function or an MCP server answers.

## How MAS-Lab implements it

MCP client and server plugins ship in `mas-library-ioa` and use the official
Python SDK (`mcp>=2.2`). The console script is `mas-mcp`.

A remote tool is a `ToolServerRegistry` entry:

```yaml
apiVersion: infra/v1
kind: ToolServerRegistry
metadata:
  name: mcp-localhost
spec:
  tool_servers:
    - name: mcp-web-search
      protocol: mcp
      usage: connect
      url: http://127.0.0.1:9001/mcp
      tools: [web-search]
```

`--connect NAME=mcp://host:port/mcp#tool` is a shortcut for a `usage: connect` row. `--bind` is the hidden old name.
Serve a tool with `mas-mcp serve --tool-manifest … --transport streamable-http`.

## Dependencies

| Piece | Package |
| --- | --- |
| Client, server, `mas-mcp` | `mas-library-ioa` |
| Official Python SDK | `mcp>=2.2` (`mcp[cli]` extra for `mcp version` / `mcp dev`) |
| Infra kind | [ToolServerRegistry](../references/tool-server-registry.md) |

## Compliance

Conformance uses the official npm runner, not a vendored checkout:

```bash
npx @modelcontextprotocol/conformance server \
  --url http://127.0.0.1:9001/mcp \
  --requirements 2026-07-28
```

Pinned revisions are MCP `2025-11-25` and `2026-07-28`. The runner version is
`@modelcontextprotocol/conformance@0.2.0-alpha.11` (`MCP_CONFORMANCE_VERSION`).
The v0.2 release notes record 98.3% of required checks on the 2026-07-28
stateless revision. Workflow, reports, and what is not scored:
[library-ioa MCP compliance](https://github.com/ioc-project/mas-lab/blob/main/library-ioa/docs/mcp/protocol-compliance.md)
and [RESULTS.md](https://github.com/ioc-project/mas-lab/blob/main/library-ioa/compliance/mcp/RESULTS.md).

## Related

- [Tutorial 4 — MCP tools](../tutorials/04-mcp-tools/README.md)
- [User guide: running tools through MCP](../user-guide.md#running-tools-through-mcp)
- [kind: Tool](../manifests/tool.md)
- [ToolContract](../references/tool-contract.md)
- [Infra manifest](../manifests/infra.md)
