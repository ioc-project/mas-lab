<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Plugin and tool authoring guide

This guide shows how to write a plugin or a tool for `mas-runtime`.
It is for developers who extend the runtime.

A plugin is a Python class that the runtime discovers from a library manifest (`library.yaml`) and selects by name, alias, or URN.
A tool is a plugin (or a `kind: Tool` manifest backed by a class) that an agent's model can call.

Sections: 1 Quick start, 2 The `@plugin` decorator, 3 Tool authoring, 4 Sample tools, 5 Contract taxonomy, 6 Observability, 7 Governance plugins, 8 Packaging.

---

## 1. Quick start

```python
from mas.runtime.contracts import ToolContract
from mas.runtime.contracts.base import plugin


@plugin
class GreeterTool(ToolContract):
    def get_name(self):
        return "greet"

    def get_description(self):
        return "Greet someone by name."

    def get_parameters_schema(self):
        return {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Name to greet."}},
            "required": ["name"],
        }

    def execute(self, **kwargs):
        return {"message": f"Hello, {kwargs['name']}!"}
```

The decorator infers the plugin metadata:

```python
>>> GreeterTool.plugin_id
'greeter@v1'
>>> GreeterTool.implements
['tool']
```

The decorator does not register the class. To make it available to agents, do one of the following:

- Wrap it in a `kind: Tool` manifest whose `spec.impl.module_path` and `class_name` point at the class, and
  reference that manifest from the agent's `spec.tools[].ref`
  (see [dev/schemas/tool-definition.md](dev/schemas/tool-definition.md)).
- Declare it in a library's `library.yaml` (`plugins:` entry with `type`, `name`, `urn`, `module`, `class`);
  see [plugin-registry-manifests.md](plugin-registry-manifests.md).


## 2. The @plugin decorator

| Field | Auto-inferred from | Override |
|-------|-------------------|----------|
| `plugin_id` | Class name converted to `snake_case@v1` (strips a `Tool`/`Plugin` suffix) | `@plugin(plugin_id="my_id@v2")` or a `plugin_id = "..."` class attribute |
| `implements` | MRO walk, collecting `contract_id` from parent contracts | `implements = [...]` class attribute |
| `requires` | not inferred | `@plugin(requires=["recorder"])` |
| `governed_by` | not inferred | `@plugin(governed_by=["budget"])` |

| Class name | Auto `plugin_id` |
|-----------|---------------|
| `DateTimeTool` | `date_time@v1` |
| `MyHTTPClientPlugin` | `my_http_client@v1` |
| `BudgetGovernance` | `budget_governance@v1` |
| `SimplePlugin` | `simple@v1` |

Use bare `@plugin`, `@plugin(requires=[...], governed_by=[...])`, or a class attribute. Class attributes take precedence over inferred values. A class that inherits several contracts gets all their ids in `implements`.

`requires` and `governed_by` are informational metadata. Governance plugins run on their own through the chain.

The `plugin_id` (`name@v1`) is the decorator's local identity. The URN that manifests and the registry use (for example `mas.dp.react`) comes from the library manifest entry; see [plugin-aliases.md](plugin-aliases.md).


## 3. Tool authoring

All tools inherit from `ToolContract`. Two styles are supported.

### Style A: Pydantic Input (recommended)

```python
from pydantic import BaseModel, Field
from mas.runtime.contracts import ToolContract


class WeatherTool(ToolContract):
    class Input(BaseModel):
        city: str = Field(..., description="City name")
        units: str = Field("metric", description="metric or imperial")

    def get_name(self):
        return "weather"

    def get_description(self):
        return "Get current weather for a city."

    # get_parameters_schema() is derived from Input.model_json_schema()

    def execute(self, **kwargs):
        args = self.Input(**kwargs)  # Pydantic validation
        return {"city": args.city, "temp": 22, "units": args.units}
```

### Style B: manual JSON Schema

Override `get_parameters_schema()` and return a JSON Schema object (see the quick start).

### Call path

```text
ToolContract.list_tools()                 -> [{name, description, parameters, ...}]
ToolContract.call_tool(name, arguments)   -> validates arguments, then
    ToolContract.execute(**arguments)     -> dict result
```

#### Sync tools and async tools

Write **one** method, and write it async: `async def on_execute_tool` (or `async def execute` on `ToolContract`). That is the way to write a new tool. You do not need `on_aexecute_tool`. The async path awaits it on the event loop; there is no worker thread. The sync path (`call_tool`) runs it to completion on one shared helper loop (a daemon thread started on first use, so a loop-bound client kept between calls stays valid). Call `acall_tool` from async code. A sync `on_execute_tool` / `execute` still works for existing plugins: the engine may run that call in a worker thread (`engine.tool_worker`). `on_aexecute_tool` still wins when present.

A tool is **async** when that one method is a coroutine, or when it still implements `on_aexecute_tool` / provider `acall_tool`. It is **sync** when it only has a blocking `execute` / `on_execute_tool`.

| Kind | Where it runs | What a slow call does |
| --- | --- | --- |
| Async | Awaited on the event loop, no thread | Nothing. Other calls, tools and sessions keep running while it waits on I/O |
| Sync, `engine.tool_worker: thread` (default) | One worker thread per call | The loop stays free; the thread is held for the call |
| Sync, `engine.tool_worker: inline` | On the event loop | Blocks everything on that loop until it returns, including the other tools of the batch |
| `kernel_state = True` | Inline in the kernel, always | Blocks, by design. Reserved for tools that read or change session state (skills) |

A blocking call (HTTP, MCP, a subprocess, a file read) is only a problem in a sync tool. Written as an async tool it overlaps with no thread, so `inline` costs nothing. Moving sync tools to async is the planned way to make `inline` the default. The runtime flags each sync tool class once in the log: a warning under `inline`, a note under `thread`.

Every tool gets the same frozen `ToolCallContext`, never the kernel ctx, so placement cannot change its result. `engine.tool_concurrency` bounds how many sync tools run at once. Delegation and other engine-tool contracts already expose `acall`.

A tool that serves several call names overrides `list_tools()` and `call_tool()`. Optional hints (`is_idempotent()`, `is_read_only()`, `is_destructive()`, `get_timeout_seconds()`, ...) are advertised with the schema. See [dev/schemas/tool-definition.md](dev/schemas/tool-definition.md).


## 4. Sample tools

`mas-runtime` ships no built-in tools. Tutorial and benchmark tools live in `library-samples/tools/` as `kind: Tool` manifests (`*.tool.yaml`) next to their Python classes. Examples: `calc`, `web-search`, `memory-search`, and the trip-planner tools (`get_fares`, `get_attractions`, `lookup_schedule`, `query_graph_database`). Agents reference them through `spec.tools[].ref` (for example `samples:tools/memory-search.tool.yaml`).

`library-standard` provides the system plugins (human input, user notification, subagent spawning) and the in-process tool provider; see its `library.yaml`.

### System plugins

A system plugin is a syscall the model may call. Register it as type `system_plugin`
and derive from `mas.runtime.system_plugins.SystemPluginBase`. Implement `tools`
(what it adds to the model's tool list), `bind` (the state, once per session) and
`call`; `close`, `snapshot`, `restore`, `claims` and `should_load` have defaults.
`InstanceSystemPlugin` covers the common case of one tool object with
`on_collect_tools` / `on_execute_tool`: implement `make_instance`. Whether a plugin
loads unnamed is the `default_enabled` attribute of its library row, or its
`should_load` rule over the agent spec. Import `runtime/tests/system_plugins/conformance.py`
in the plugin's tests. Agents grant the plugin in `spec.system_plugins`. See
[system-plugins.md](../../docs/manifests/system-plugins.md).


## 5. Contract taxonomy

A contract is the interface a plugin implements. Contracts form three families: **capability** (what an agent can access), **orchestration** (how control flows across agents), and **governance** (what is permitted). The importable set is listed in
[dev/contracts/index.md](dev/contracts/index.md#shipped-contracts) and explained in
[dev/contracts/taxonomy.md](dev/contracts/taxonomy.md).


## 6. Observability

Every crossing of the kernel envelope emits events to the session's recorder. The native observability plugin writes them to `events.jsonl`.
`mas.runtime.schema.observability.ObsEventKind` defines the kernel-level kinds with dotted names. In `events.jsonl` the same kinds use underscores (`governance.decision` becomes `governance_decision`) and appear next to the interval events (`llm_call_start`, `tool_call_end`, ...).

| Kernel kind | Meaning |
|------|---------|
| `boundary.egress` / `boundary.ingress` | An outbound request / inbound result crossed the envelope |
| `governance.decision` | A governance plugin decided on an egress or ingress (fields include `hook`, `checkpoint`, `decision`, `policy_name`) |
| `hitl.request` / `hitl.resolve` | Human-in-the-loop question raised / answered |
| `engine.io` / `engine.io.return` | Model or tool engine call and its return |
| `client.response` | Text delivered to the client |
| `context.assembled`, `context.steer`, `context.mutation` | Prompt assembly and context changes |
| `cache.lookup` | LLM cache hit or miss |
| `envelope.activity` | Per-symbol envelope activity |
| `boundary.error` | Failure at a boundary |
| `control` | Pause, resume, abort, steer and similar control actions |

`context.assembled` lists the prompt segments. Each segment has a UUID `part_id`, `source`, `section_id`, `tokens`, `content_preview` and `retained`.

Field-level detail and the span format: [trajectory-schema.md](trajectory-schema.md) and [architecture-instrumentation.md](architecture-instrumentation.md).


## 7. Governance plugins

Governance runs at the egress and ingress chokepoints of the envelope (see [mealy-envelope.md](mealy-envelope.md) and [governance-policy-engine.md](governance-policy-engine.md)).
An egress governance plugin implements `evaluate_egress(intent, *, config)` and returns a `(GovDecision, plugin_id, reason)` tuple.
`GovDecision` is one of `ALLOW`, `BLOCK`, `TERMINATE`, `HITL`, `SKIP`, `RETRY`, `LOG`, `MODIFY`, `BLACKLIST`. A `BLOCK` ends the chain, and its reason returns to the model as a tool observation.

The reference implementation is `NoUndeclaredToolPlugin` in
`library-standard/src/mas/library/standard/plugins/governance/no_undeclared_tool.py`. A minimal plugin looks like this:

```python
from mas.runtime.boundary.gov.policy import EgressIntentView
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.coupling import GovDecision


class DenyShellPlugin:
    plugin_id = "deny_shell@v1"

    def __init__(self, **_raw):
        pass

    def evaluate_egress(self, intent: EgressIntentView, *, config: KernelConfig):
        if intent.op == "TOOL_CALL" and (intent.tool_name or "") == "shell":
            return GovDecision.BLOCK, "deny_shell", "The shell tool is not available."
        return GovDecision.ALLOW, "deny_shell", "not the shell tool"
```

Register it in a library manifest as a `type: governance` entry (see the `mas.gov.*` entries in `library-standard/library.yaml`), and select it from the agent manifest's `spec.governance`.


## 8. Packaging

`mas-runtime` has one optional dependency group besides `dev`:

| Extra | Packages |
|-------|----------|
| `grpc` | `grpcio`, `grpcio-tools` |

Install from a repository checkout:

```bash
uv sync --all-packages             # everything, including the libraries
```

Plugins ship in libraries (`library-standard`, `library-samples`, or your own package with a `library.yaml`). See [../../docs/library-discovery.md](../../docs/library-discovery.md).
