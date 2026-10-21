# Tutorial 01: MCP tools for MAS agents

This tutorial moves a tool from in-process Python to a Model Context Protocol (MCP) server without changing the agent manifest. It is for users who already ran [Tutorial 1](../../../../docs/tutorials/01-building-an-agent/README.md) and want to host a tool remotely.

The agent still calls a logical tool by name and arguments. Only the way the runtime reaches the tool changes, and that choice lives in infra.

Smallest example (terminal 1 serves, terminal 2 chats; details in sections 4 and 5):

```bash
mas-mcp serve --tool-manifest library-samples/tools/web-search.tool.yaml \
  --transport streamable-http --host 127.0.0.1 --port 9001

mas-ctl chat docs/tutorials/01-building-an-agent/agent.yaml \
  -o docs/tutorials/01-building-an-agent/overlays/tools.yaml \
  --infra-ref ../../../library-samples/infra/mcp-localhost.yaml \
  -q "What is the current price of Apple stock?" --trace
```

## 1. Keep the agent spec stable

The agent carries no `spec.providers[]` for a remote MCP service. The agent and overlay schemas reject `providers[]` and infra connection fields, and there is no MCP provider overlay. Connection policy belongs in infra, as A2A endpoints live in an `Application` infra manifest.

The model:

- Logical tool contract: `kind: Tool` plus `call_tool(name, arguments)`.
- Local tools: materialized by default from the implicit `tools/` and `skills/` locations of `standard:local-tools` (`protocol: local`).
- Remote tools: an infra `ToolServerRegistry` entry with `protocol: mcp`.
- An MCP `usage: use` dependency replaces the implicit local provider. An explicit local infra claim re-adds local tools beside MCP.
- Flavours control how tools are exposed, not which third-party endpoints are consumed.

The Tutorial 1 overlays stay unchanged. `overlays/tools.yaml` adds the tool contracts and `overlays/skills.yaml` adds the skill:

```yaml
# docs/tutorials/01-building-an-agent/overlays/tools.yaml
apiVersion: mas/v1
kind: Overlay
metadata:
  name: tools
spec:
  target:
    kind: Agent
  patch:
    context:
      tool_usage: |
        When you need to perform calculations, use the calc tool.
        When you need current information from the web, use the web-search tool.
    tools:
      $op:
        add:
          - ref: samples:tools/web-search.tool.yaml
          - ref: samples:tools/calc.tool.yaml
```

```yaml
# docs/tutorials/01-building-an-agent/overlays/skills.yaml
apiVersion: mas/v1
kind: Overlay
metadata:
  name: skills
spec:
  target:
    kind: Agent
  patch:
    skills:
      $op:
        add:
          - answer-formatting
```

The only new file needed to switch web-search to MCP is this infra manifest:

```yaml
# library-samples/infra/mcp-localhost.yaml
apiVersion: infra/v1
kind: ToolServerRegistry
metadata:
  name: mcp-localhost
spec:
  tool_servers:
    - id: localhost-mcp-tools
      protocol: mcp
      usage: use
      url: http://127.0.0.1:9001/mcp
```

The runtime reads this entry and initializes its MCP provider from it. `protocol` and `id` are required. `usage: use` (the consumer default) means the runtime consumes the endpoint. `url` is the HTTP endpoint; `transport` defaults to `streamable-http`. Leave `tools` unset to discover names from MCP `tools/list`, or set an explicit list.

The implicit local entry has no `tools` claim. To keep local tools when MCP is also configured, add this infra dependency; id-based merge keeps the implicit directory paths and adds the claim:

```yaml
# library-samples/infra/local-tools.yaml
apiVersion: infra/v1
kind: ToolServerRegistry
metadata:
  name: local-tools
spec:
  tool_servers:
    - id: local
      protocol: local
      tools: "*"
```

The implicit manifest that every run loads, with no ref needed for the conventional directories:

```yaml
# library-standard/.../libs/standard/local-tools.yaml
apiVersion: infra/v1
kind: ToolServerRegistry
metadata:
  name: local-tools
spec:
  tool_servers:
    - id: local
      protocol: local
      tools_dir: tools
      skills_dir: skills
```

For the server side, use a separate `usage: deploy` entry. Its `host`, `port`, `tools_dir` and optional `tools` list configure what `mas-mcp serve` exposes:

```yaml
# library-samples/infra/mcp-localhost-deploy.yaml
apiVersion: infra/v1
kind: ToolServerRegistry
metadata:
  name: mcp-localhost-deploy
spec:
  tool_servers:
    - id: mcp-localhost-deploy
      protocol: mcp
      usage: deploy
      transport: streamable-http
      host: 127.0.0.1
      port: 9001
      tools_dir: ../tools
      tools: [web-search]
```

## 2. Install dependencies

`library-ioa` depends on the PyPI package `mcp`. Do not commit vendored SDK checkouts.

```bash
# from the mas-lab root
uv sync
uv sync --extra cli   # SDK CLI (`mcp version`, `mcp dev`); or install mas-library-ioa[all]
```

Official protocol conformance runs on Node, not a Python clone. To reproduce the MAS Lab fixture, install dependencies and run both pinned requirement sets with the [MCP compliance workflow](../../README.md):

```bash
task --dir library-ioa/compliance/mcp install
task --dir library-ioa/compliance/mcp validate-config
MCP_CONFORMANCE_RESULTS="$PWD/library-ioa/compliance/mcp/results" \
  task --dir library-ioa/compliance/mcp run
```

## 3. Start the tool as a localhost MCP server

From the mas-lab repo root (terminal 1), expose one existing Tool manifest without an infra manifest:

```bash
mas-mcp serve \
  --tool-manifest library-samples/tools/web-search.tool.yaml \
  --transport streamable-http \
  --host 127.0.0.1 --port 9001
```

Or let infra own the deployment settings and discover manifests from `tools_dir`:

```bash
mas-mcp serve \
  --infra-ref library-samples/infra/mcp-localhost-deploy.yaml
```

The agent manifest needs no patch to reach this server.

## 4. Run the agent through MCP

MCP-only replaces the default local provider, so `calc` is not exposed:

```bash
mas-ctl chat docs/tutorials/01-building-an-agent/agent.yaml \
  -o docs/tutorials/01-building-an-agent/overlays/tools.yaml \
  --infra-ref ../../../library-samples/infra/mcp-localhost.yaml \
  -q "What is the current price of Apple stock?" \
  --trace
```

To keep local `calc` and the local skill tools, add the explicit local infra claim:

```bash
mas-ctl chat docs/tutorials/01-building-an-agent/agent.yaml \
  -o docs/tutorials/01-building-an-agent/overlays/tools.yaml \
  -o docs/tutorials/01-building-an-agent/overlays/skills.yaml \
  --infra-ref ../../../library-samples/infra/mcp-localhost.yaml \
  --infra-ref ../../../library-samples/infra/local-tools.yaml \
  -q "What is the current price of Apple stock?" \
  --trace
```

The relative infra path resolves from the agent manifest's directory.

## Why this design

- Tool manifests describe the logical interface; invocation stays `call_tool(name, arguments)`.
- Endpoint policy, protocol, headers and transit details live in infra.
- The agent spec stays portable. Switching between implicit local tools and an MCP dependency means changing infra refs.
- `protocol: mcp` is explicit in the infra manifest, matching the A2A pattern for protocol-aware infra declarations.

## References

- [library-ioa README](../../../README.md)
- [MCP protocol compliance](../../mcp/protocol-compliance.md)
- [ToolContract](../../../../docs/references/tool-contract.md)
- [kind: Tool](../../../../docs/manifests/tool.md)
- [ToolServerRegistry](../../../../docs/references/tool-server-registry.md)
- [Tutorial 1 qa-agent](../../../../docs/tutorials/01-building-an-agent/README.md)
