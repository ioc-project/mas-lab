# IOA Library

Client and server bridge between MAS Lab tools and the Model Context Protocol (MCP), plus Agent2Agent (A2A) exposure of agents. Use it to call tools served by an MCP server, or to publish a MAS tool manifest as an MCP server. The Quickstart below shows both.

Agents keep the same tool names and arguments. MCP endpoints come from infra `ToolServerRegistry` entries; the agent manifest does not declare providers. An MCP `usage: use` dependency replaces the implicit local provider. To keep in-process tools beside MCP, add an explicit `protocol: local` infra claim.

## Layout

- `src/library_ioa/plugins/mcp/spec.py` — infra server entry → client config
- `src/library_ioa/plugins/mcp/contract.py` — MCP Tool/CallToolResult ↔ MAS list_tools/call_tool
- `src/library_ioa/plugins/mcp/client` — SDK-backed client (one session per list/call)
- `src/library_ioa/plugins/mcp/provider.py` — `kind: mcp` tool-provider plugin
- `src/library_ioa/plugins/mcp/server` — wrap a MAS tool manifest as an MCP server (`mas-mcp`)
- `src/library_ioa/utils` — result/error helpers
- `docs/` — tutorial and protocol-compliance notes (`docs/mcp/`, `docs/a2a/`)

Reusable MCP YAML lives in `library-samples/infra/mcp-localhost.yaml`.

## Dependencies

```toml
dependencies = ["mcp>=2.2"]           # PyPI; not a git clone of mcp-python-sdk
optional-dependencies.cli = ["mcp[cli]"]
optional-dependencies.all = ["mcp[cli]"]
```

Protocol conformance is Node `@modelcontextprotocol/conformance` (`npx`). Do not
commit vendored `mcp-python-sdk` / `mcp-conformance` trees.

## Quickstart

From the mas-lab repo root (after `uv sync`):

```bash
# terminal 1
mas-mcp serve \
  --tool-manifest library-samples/tools/web-search.tool.yaml \
  --tool web-search \
  --host 127.0.0.1 \
  --port 9001 \
  --transport streamable-http
```

```bash
# terminal 2: official SDK CLI + mas-mcp client
mcp version
mas-mcp tools list --url http://127.0.0.1:9001/mcp
mas-mcp tools call --url http://127.0.0.1:9001/mcp \
  --tool web-search --arguments '{"query":"Apple stock price"}'

mas-ctl chat docs/tutorials/01-building-an-agent/agent.yaml \
  -o docs/tutorials/01-building-an-agent/overlays/tools.yaml \
  -o docs/tutorials/01-building-an-agent/overlays/skills.yaml \
  --infra-ref ../../../library-samples/infra/mcp-localhost.yaml \
  --infra-ref ../../../library-samples/infra/local-tools.yaml \
  -q "What is the current price of Apple stock?" \
  --trace
```

The serve terminal logs `MCP tool call name=web-search` for both `mas-mcp tools call` and the agent.

## Runtime wiring

- `MCPClient` opens and closes the streamable-HTTP session in the same asyncio task (`run_sync` from the provider)
- `LocalToolProvider`: in-process Python tools (`local` flavour or implicit `standard:local-tools` infra)
- `MCPToolProvider`: `kind: mcp` (`library_ioa.plugins.mcp.provider`)

## A2A exposure

The generic runtime server command starts the registered `agent_expose`
plugin from the agent's Application endpoint (`usage: deploy`):

```bash
mas-ctl serve docs/tutorials/01-building-an-agent/agent.yaml \
  --infra-ref path/to/a2a-qa.yaml
```

Use the official A2A CLI for discovery and messaging:

```bash
a2a card get http://127.0.0.1:9005
a2a send -a http://127.0.0.1:9005 "What is the capital of France?"
```

For a MAS, declare remote dependencies as named endpoints in an `infra/v1` Application manifest, and keep `workflow.nodes[].delegates_to` as the topology.
See the A2A feature page at [docs/a2a/protocol-compliance.md](docs/a2a/protocol-compliance.md).

## Development notes

- The bridge lives in the `library_ioa.plugins.mcp` package.
- The runtime provider plugin registers `kind: mcp` manifests through `MCPProviderPlugin`.
- The SDK-backed client connects using `mcp.client.stdio.stdio_client` and `ClientSession`.
- A separate MCP server process can expose the same tool contract without changing the agent manifest.

## Related docs

- [docs/README.md](docs/README.md)
- [docs/tutorials/01-mcp-tools/README.md](docs/tutorials/01-mcp-tools/README.md)
- [ToolContract](../docs/references/tool-contract.md)
- [kind: Tool](../docs/manifests/tool.md)
- [ToolServerRegistry](../docs/references/tool-server-registry.md)
