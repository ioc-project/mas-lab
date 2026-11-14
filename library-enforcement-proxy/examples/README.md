# Minimal configuration example

These files show native request and response policy bindings around a worker and two MCP tools. Install the optional library with `uv pip install ./library-enforcement-proxy`, using a MasLab build containing the response-release extension. The root meta-package does not install this library automatically.

Supply `client.json` from a compatible, provisioned enforcement-proxy deployment. It must register the worker, its caller and the records service. Map `records_read` and `records_write` to that service with `kind: tool`, the exact tool name and `transport: http`, and supply the mTLS file paths and caller credential environment variables. No sample credentials or permissive fallback are supplied here. The adapter, CFN and SCOPE deployment remain a separate concern.

Start the example MCP service in a separate terminal with `python records.py records.json 9200`. The HTTP provider in `mcp.yaml` connects to `http://127.0.0.1:9200/mcp`; it does not start the service.

Attach `control.yaml` through `flavour.yaml` when loading `worker.yaml`. Load `application.yaml` and `mcp.yaml` through the usual `--infra-ref` options, and select your model through the normal model configuration. Resolve relative configuration and MCP paths from this example directory, or use absolute paths in a copied configuration. Request governance applies before tool dispatch. Release policy applies independently before the worker's reply is delivered.

With a deterministic decision service, allow a write followed by a read, then deny another write and verify that the saved record is unchanged. Denying reply delivery must hide its content without undoing an already allowed write. The package tests exercise these allow/deny contracts with controlled services and execution counters, without a model or external deployment.

Routes and runtime configuration are trusted. The provider, authorization mapping and matching Go adapter routes must all describe HTTP. Stdio is not supported by this contribution.

For execution-wide evidence, configure native `otel` observability separately. The integration adds proposed activities and decision correlation only. No telemetry subscription wrapper is required by this package.
