# A2A response delivery policies

An optional delivery policy approves a response before the native A2A server releases it. Agent execution and task storage remain native. A denied response does not undo work already performed by the agent. Use the existing governance plugins to authorize delegation and tool requests before execution.

## Configuration

Register a class implementing `library_ioa.plugins.a2a.delivery.DeliveryPolicy` in a library manifest under type `a2a_delivery_policy`. Then select it on each listening Application endpoint:

```yaml
protocol: a2a
usage: listen
url: http://127.0.0.1:9100
a2a:
  delivery_policy:
    type: example_release  # Supplied by your installed library, not a built-in policy.
    params: {}
  delivery_timeout: 30     # Positive finite seconds for each decision.
```

The binding uses the native plugin registry. Missing plugins fail startup. Embedders may instead pass a policy instance to `A2AExposure(delivery_policy=policy)`. Do not configure both forms. Each exposure owns its policy instance and closes it on shutdown.

Omitting the policy preserves existing behavior. With a policy, the AgentCard advertises streaming as disabled and the server rejects streaming sends and task subscriptions before execution. The shared exposure path applies to CLI serving and MAS listeners, with the same handler for JSON-RPC, REST and gRPC.

## Policy contract

```python
from library_ioa.plugins.a2a.delivery import DeliveryCandidate, DeliveryDecision

class ExampleRelease:
    async def authorize(self, candidate: DeliveryCandidate) -> DeliveryDecision:
        return DeliveryDecision.ALLOW  # Replace with your asynchronous decision.

    async def aclose(self) -> None:
        pass
```

`DeliveryCandidate` contains the producing `agent_id`, authenticated `recipient_id`, push `destination`, `task_id`, `context_id`, delivery `kind`, and immutable JSON bytes in `payload` and `request_metadata`. The candidate is a snapshot of the native response. ALLOW releases that snapshot unchanged. DENY, invalid decisions, exceptions and timeouts prevent release. Cancellation propagates and cancels the pending decision. Shutdown cancels outstanding decisions before closing the policy.

The recipient is the SDK's authenticated user identity, or an empty string for anonymous callers. Push delivery supplies the configured callback URL. Request metadata is correlation evidence and must not be treated as authenticated identity. Callback credentials are not supplied to the policy. Deployments requiring authenticated recipients must configure authentication and reject anonymous candidates in their policy.

The hook runs for immediate replies (`send`), polling (`get_task`), each task in a list (`list_tasks`), cancellation results (`cancel_task`) and push callbacks (`push`). A denied task prevents release of the whole list. Native cancellation still occurs before its result is authorized. Push authorization occurs before the HTTP callback. The policy receives each delivery attempt, including reads of previously completed tasks. No decision cache, response rewriting, task resumption or cross-restart persistence is added.

A policy that caches decisions must bind them to the candidate and recipient, including callback destination. Correlation metadata on a polling request may differ from the initial send. Tasks remain in the native store even when their delivery is denied, so retrieval must always remain mediated.

## Release outcomes

A policy may implement the optional synchronous `on_release(outcome: DeliveryOutcome)` callback. It receives the controller's final authorization outcome, including timeout and cancellation. Only an allowed outcome contains response bytes. This is an authorization notification, not a network-delivery receipt. Callback failures cannot change the decision. Consumers that display governed output can use these outcomes instead of raw execution events, which may contain unapproved content. Subscription management and presentation remain outside the protocol implementation.

The controller enforces its own monotonic deadline and shutdown state, even if a policy suppresses cancellation and eventually returns ALLOW. Waiting and cleanup are bounded by `delivery_timeout`. Late results cannot authorize release. This does not forcibly terminate arbitrary in-process plugin code. Policies must cooperate with cancellation and release their own resources.
