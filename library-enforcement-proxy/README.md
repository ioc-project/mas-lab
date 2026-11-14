# Native MasLab integration

This experimental package is intended for controlled agent testing. It authorizes delegation and MCP requests through MasLab's governance API and authorizes A2A replies through its delivery-policy extension. The Go adapter retains CFN communication and audit ownership. Proposed activities go to SCOPE through OTLP before the adapter receives their references. There is no local policy engine or automatic retry.

## Compatibility and installation

Use Python 3.11 or newer and MasLab 0.2.0 with the native A2A delivery-policy extension and configurable A2A webserver binding. The integration checks those capabilities at startup. It does not patch installed packages. This library depends on [native response release (#242)](https://github.com/ioc-project/mas-lab/pull/242), including its final `on_release` callback, and [webserver transport validation (#245)](https://github.com/ioc-project/mas-lab/pull/245). It is not compatible with earlier 0.2.0 builds. Install reviewed MasLab wheels first, then `pip install ./library-enforcement-proxy`. For execution-wide telemetry also install `mas-lab-library-telemetry[convert]` from the same reviewed source.

## Configuration

The native library entry point registers `proxy_requests`, `proxy_release`, and `proxy_authenticated_http`. Attach request governance through a Control document:

```yaml
apiVersion: control/v1
kind: Control
metadata: {name: authorization}
spec:
  agents: planner
  governance:
    - proxy_requests:
        config_file: ./client.json
        agent: planner
```

Attach that Control through `Flavour.spec.control`. Configure the A2A Application endpoint separately:

```yaml
a2a:
  delivery_policy:
    type: proxy_release
    params: {config_file: /protected/client.json, agent: planner}
  delivery_timeout: 30
  webserver:
    type: proxy_authenticated_http
    params: {config_file: /protected/client.json}
```

`client.json` contains a registered `mas_id`, HTTPS `adapter_url`, `otlp_url`, `ca_file`, and `identities` keyed by local aliases. Each producing agent needs its canonical `id`, `certificate_file`, `key_file`, and exact `tools` mapping. A mapping selects `destination`, `kind` (`delegation` or `tool`), the configured operation `transport`, and, for MCP, the exact `tool` name. The integration admits only declared `http` operation routes. Stdio declarations are not supported by this contribution. The authorization endpoint itself uses HTTPS/mTLS independently of the protected operation. Relative credential paths resolve against the configuration file. Connection credentials never go into activity evidence.

For example, an entry in an agent's `tools` mapping can be:

```json
{"records_write": {"destination": "records", "kind": "tool", "tool": "records_write", "transport": "http"}}
```

The MasLab library validates the declared recipient and operation transport from trusted integration configuration. The enforcement-proxy adapter checks those declarations against its configured authorization routes and authenticates the caller through mTLS.

The integration does not independently verify or pin the connection used to execute the operation. It assumes the test setup routes operations to the declared recipient over the declared transport. Test results demonstrate authorization and governance-hook behavior within that configured setup.

Unknown routes and unsupported transport declarations are blocked.

The example shows how to bind an existing adapter configuration. Its standard ASGI authentication plugin resolves bearer credentials from each identity's `a2a_credential_env`. Credentials must be distinct and at least 32 characters. AgentCards remain publicly discoverable. Protocol methods require authentication. Production deployments may use their own native webserver authentication plugin. Anonymous response recipients are denied. Push callbacks additionally require an exact protected `push_destinations` URL-to-recipient mapping.

Enable native MasLab `otel` observability separately, with `realtime: true`, `extensions: true`, `governance: true`, the registered MAS ID and `OTEL_EXPORTER_OTLP_ENDPOINT`. The integration publishes only proposed-activity and authorization correlation evidence. Native telemetry retains execution events independently of whether a gate was reached. Concurrent export requires the native telemetry ownership fix proposed in #236. Complete assembled prompts require #235. These are separate telemetry improvements and do not change authorization policy.

## Failure and lifecycle behavior

The default authorization deadline is 20 seconds and covers publication, mediation and receipt validation. Missing evidence, invalid receipts, deadline expiry, cancellation and closed integrations block release. The adapter authenticates each source with mTLS and admits only configured routes. ALLOW applies to the frozen candidate and recipient. Denials never rewrite responses or replay writes. Blocking a reply does not undo an operation already executed.

Each operation owns its HTTP clients. Native exposure cleanup closes delivery policies. Embedders close request policies when shutting down their runtimes. In-process adoption requires trusted runtime code and configuration. It provides no external isolation guarantee.

The compatible reference-based adapter belongs to enforcement-proxy. This HTTP-only contribution does not depend on the separate [stdio contract proposal](https://github.com/ioc-project/enforcement-proxy/issues/386). Full CFN/SCOPE deployment is separate from this library. See [the minimal configuration example](examples/README.md). Package tests exercise the native runtime and delivery controller within the configured test setup. They do not establish independent connection verification, deployed CFN/SCOPE compatibility or semantic-policy accuracy.

## Optional release notifications

`ReleasePolicy.subscribe(callback)` receives the native controller's final `DeliveryOutcome`. Only an allowed outcome includes response content. `unsubscribe(callback)` and policy shutdown remove listeners. Notifications are separate from raw execution telemetry, and callback failures cannot authorize or alter a response. Consumers choose their own presentation and duplicate handling.

Authenticated serving is HTTP-only. Native exposure rejects both `a2a.grpc_port` and `a2a.grpc_url` before constructing AgentCards, clients or listeners. The package requires native webserver selection and transport validation in addition to the delivery-policy extension. Authentication middleware protects HTTP requests only and does not provide gRPC authentication.
