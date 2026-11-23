# MCP protocol compliance

MCP and A2A are separate protocol surfaces. MCP exposes tools, resources,
prompts, sampling, roots, notifications, and transport bindings; A2A exposes
agents, messages, tasks, and task lifecycle.

## External test command

The official MCP conformance framework is installed and invoked with npm:

```bash
npx @modelcontextprotocol/conformance server \
  --url http://127.0.0.1:9001/mcp
```

Pin a released specification revision with `--requirements`, for example:

```bash
npx @modelcontextprotocol/conformance server \
  --url http://127.0.0.1:9001/mcp \
  --requirements 2026-07-28
```

## Current scope

The AGNTCON MCP scenario proves the streamable-HTTP tool path. A complete
conformance run still needs to be recorded for resources, prompts, roots,
notifications, authentication, and each supported specification revision.
Results belong under the separate `compliance/` workflow, not in runtime unit
tests.

## Tool errors and secret headers

A tool server's own JSON-RPC error (an `error` answer to `tools/call`, such as
"file not found", at any HTTP status) is the tool's output: its message reaches
the model as the tool result. A transport failure does not carry the server's
words: an HTTP error whose body is not a JSON-RPC error is the SDK's fixed
"Server returned an error response", and a connection or TLS error or an
`httpx` status error is shown as `<Type>: HTTP <status>` (an error with no
response keeps its text, with urls cut to scheme, host, port and path).

Accepted limit: a server that writes the credential it received into its own
JSON-RPC error message hands that credential to the model. The operator's
configuration already sent the secret to that server; MAS Lab does not scrub
the text of a tool's answer.

Secret headers follow the [network rules](../../../docs/references/secrets.md#network-rules).
