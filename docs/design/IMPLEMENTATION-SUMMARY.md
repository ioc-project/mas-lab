Status: Historical (implementation summary for agent-driven HITL; the current design is in [agent-driven-hitl.md](agent-driven-hitl.md)).

# Agent-Driven HITL Implementation Summary

This page lists what was built for agent-initiated HITL. HITL means human in the loop: a person answers a question the agent raises. For the design and what is not implemented, see [agent-driven-hitl.md](agent-driven-hitl.md). For tool arguments and manifest parameters, see the [system tools reference](../references/system-tools.md#request_human_input).

## Components

| Component | Location | Role |
| --- | --- | --- |
| `RequestHumanInputTool` | `library-standard/src/mas/library/standard/plugins/system_tools/request_human_input.py` | System tool `request_human_input`; validates arguments and raises `RequestHitlSignal`. |
| `InformUserTool` | `library-standard/src/mas/library/standard/plugins/system_tools/inform_user.py` | Non-blocking `inform_user` progress update (raises `InformUserSignal`). |
| `RequestHitlSignal`, `InformUserSignal` | `runtime/src/mas/runtime/system_tools/signal.py` | Control-flow signals. |
| `_SystemToolHitlWrapper`, `_SystemToolUserUpdateWrapper` | `library-standard/src/mas/library/standard/plugins/tools/local.py` | Catch the signals and call the `HITLContract` / `UserIOContract`. |
| `HITLContract`, `UserIOContract` | `runtime/src/mas/runtime/contracts/user_communication_contract.py` | Protocols. |
| `RegistryHitlContract`, `AutoResolveHitlContract`, `RegistryUserIOContract` | `library-standard/src/mas/library/standard/plugins/hitl/contracts.py` | Default and unattended contracts. |
| `InteractiveHitlContract` | `ctl/src/mas/ctl/session/interactive_hitl_contract.py` | Terminal prompt for `mas-ctl chat -i`. |
| `HitlResolverRegistry` | `runtime/src/mas/runtime/boundary/hitl/registry.py` | Thread-safe, in-process table of pending requests and user updates. |
| Delegation tracking | `ctl/src/mas/ctl/executor/mas_session.py` | Records delegates with pending requests in `state["pending_hitl_agents"]`. |

## Example flow (default registry contract)

An agent asks a question. The tool blocks until a host answers it.

```python
# 1. The agent calls the tool.
request_human_input(
    question="Approve 20% discount for Acme renewal?",
    question_type="CONFIRM",
    choices=["approve", "reject"],
    context_data={"account": "Acme Corp", "discount": "20%", "ceiling": "15%"},
)

# 2. The tool raises RequestHitlSignal. The wrapper calls
#    RegistryHitlContract.request_approval(...), which registers the request
#    and waits.

# 3. A host surfaces and answers it.
from mas.runtime.boundary.hitl.registry import get_hitl_resolver_registry

registry = get_hitl_resolver_registry()
pending = registry.get_pending_for_session("sess-123")
# -> {"finance-agent": [PendingHitlRequest(question=..., correlation_id=456, ...)]}
registry.resolve("sess-123", "finance-agent", 456, choice="approve", steering="")

# 4. The waiting tool call returns
#    {"choice": "approve", "steering": "", "question": ..., "resolved": True}
#    and the agent's turn continues.
```

## See also

- [Agent-driven HITL design](agent-driven-hitl.md)
- [System tools reference](../references/system-tools.md)
- [Tutorial 14: governance and HITL](../tutorials/14-governance-hitl/README.md)
