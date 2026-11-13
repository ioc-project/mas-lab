# Resolved connections at governance boundaries

A request policy can opt into resolved connection metadata by setting `requires_connection_metadata = True`. For each tool call, the native live engine then prepares an operation before evaluating governance. `EgressIntentView.connection` contains its protocol, normalized carrier, binding reference, opaque binding identity and generation. Policies that do not opt in retain existing behavior. The required `ToolProvider` protocol is unchanged.

Providers implement the separate optional `PreparedOperationProvider.prepare_call()` capability. `ConnectionBinding` owns a private target selector and an invocation factory. Preparation captures the arguments, target and invocation callbacks without executing the operation. A policy can compare protected configuration with `ConnectionInfo.matches_target(ConnectionTarget(...))`; targets, subprocess environment and credentials must never be serialized as telemetry or model input. An opaque binding identity alone does not authenticate a destination.

After authorization, the engine invokes the prepared operation rather than resolving the tool again. Replacement and invocation admission share the binding's short lock. Replacement invalidates pending operations, including when the normalized carrier stays unchanged. A call admitted before replacement can finish against its captured original target. The lock does not span network or policy work. Registry removal and delegation route replacement retire their pending bindings.

Arguments cannot change between authorization and dispatch. Existing retries retain the captured operation and existing idempotence requirements. Denial, policy failure, tool completion, cancellation and engine shutdown close prepared handles. A provider lacking this capability fails closed when metadata is required; it continues working with ordinary policies.

Preparation can perform connection discovery. It must not execute the held tool or agent task. MCP initialization and `tools/list` may start a stdio subprocess. A denied tool call therefore promises zero tool execution, rather than zero subprocess startup. Use independent target state to check that a denied write did not execute.

The capability is an in-process contract for trusted providers and configuration. It does not isolate arbitrary Python code or establish a remote service's identity by itself.
