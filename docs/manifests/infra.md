<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Infrastructure manifests (`apiVersion: infra/v1`)

**Package:** `ctl/` | **Model:** `mas.ctl.infra.models.InfraManifest` | **Resolver:** `mas.ctl.infra.resolve.resolve_infra_refs`

An **infra** manifest declares *where* deployment resources live.
It covers LLM endpoints (proxy URL, API-key env var name, model mappings), tool servers, application and A2A agent endpoints, and middleware wrapped around LLM calls.
Write one when you point a MAS at a different provider, proxy, tool server, or cache without editing any agent.

Terms used on this page:

- **Infra bundle** (`kind: InfraBundle`): one reference that composes several infra files.
- **LLM proxy** (`kind: LLMProxy`): an OpenAI-compatible HTTP endpoint that serves models.
- **Tool server**: a process that serves tools to agents, usually over MCP (Model Context Protocol, a standard for serving tools over a connection).
- **A2A** (Agent2Agent): a protocol for calling agents that run elsewhere.

```yaml
apiVersion: infra/v1
kind: LLMProxy
metadata:
  name: openai-direct
spec:
  protocol: openai
  proxy:
    api_base: env:OPENAI_BASE_URL|https://api.openai.com/v1
    api_key_env: OPENAI_API_KEY
```

Put the env var *name* in the file so several providers can run at once, each with its own variable. `mas-lab init` writes that form for the OpenAI default URL. A custom URL at init uses `<infra-name>_API_BASE`. If `proxy.api_base` is empty, the engine uses `OPENAI_BASE_URL`, then `OPENAI_API_BASE`. The right place for the name is still the file. When the address comes from the shell, the engine logs one warning naming the host, because the API key is sent there.

The runtime resolves infra from deployment configuration, in this order of source:

- workspace `config.yaml` `infra_refs`
- the `MAS_INFRA_REFS` env var
- the user `default_infra`
- CLI `--infra-ref`

`infra_refs` is not a field on Agent, MAS, or Overlay manifests.

Terms are in the [glossary](../glossary.md).
Back to the [manifest hub](README.md).
Schema: [`infra.schema.yaml`](../schemas/runtime/infra.schema.yaml).
Remote tools: [ToolServerRegistry](../references/tool-server-registry.md).

## Contents

- [Ref merge order](#ref-merge-order)
- [Several LLMProxy refs](#several-llmproxy-refs)
- [Kinds](#kinds)
- [LLMProxy models](#llmproxy-models)
- [Bundles](#bundles)
- [Relative refs and anchor](#relative-refs-and-anchor)
- [A2A agent endpoints](#a2a-agent-endpoints)
- [Rows a Flavour binds](#rows-a-flavour-binds)
- [Separation from Flavour](#separation-from-flavour)
- [LLM cache middleware](#llm-cache-middleware)
- [LLM scheduler middleware](#llm-scheduler-middleware)
- [Fault-injection middleware](#fault-injection-middleware)
- [CLI overrides](#cli-overrides)
- [LLM output-token policy](#llm-output-token-policy)
- [Local tool source](#local-tool-source)
- [ToolServerRegistry](#toolserverregistry)

## Ref merge order

Refs merge left to right:

1. Workspace `infra_refs`. The `MAS_INFRA_REFS` env var replaces this list when set.
2. The user `default_infra`, only when the workspace lists no refs.
3. Each CLI `--infra-ref`.

The merge drops duplicates.
When nothing is configured, the runtime uses `standard:production` (direct OpenAI).
`standard:local-tools` is always merged first (see [Local tool source](#local-tool-source)).

Middleware, tool servers, and endpoints merge as follows:

- `tool_servers` merge by `id`.
- `endpoints` merge by name.
- Middleware steps append in order.
- `models.generation` on an `LLMProxy` that omits `proxy.api_base` overlays output-token policy onto the resolved view.

Each `LLMProxy` / `LLMLocal` that declares `proxy.api_base` is a **leaf**, not a last-wins overlay of `api_base` / `api_key_env`. See [Several LLMProxy refs](#several-llmproxy-refs).

## Several LLMProxy refs

List as many provider manifests as you need, the same way you list tool servers.
The agent (and eval) names a **model** — optionally an alias in `models.mappings`.
A routing layer picks the leaf whose `models.allowed` (and mapping keys) claim that name, matching tool-name routing and A2A endpoint names.

```yaml
# workspace config.yaml
infra_refs:
  - standard:openai          # allowed: gpt-4o, gpt-4o-mini, …
  - ./infra/judge-proxy.yaml # allowed: [judge-special]
```

```yaml
experiment:
  models:
    main: gpt-4o-mini
    judge: judge-special     # eval_mce uses the judge-proxy leaf
```

An empty `models.allowed` is the default (`*`) owner, like a local tool provider. Two stars, or two leaves claiming the same name, is an error.
Two files with the same `metadata.name` do not error: the later ref **replaces** the earlier leaf, including `api_base`, `api_key_env`, `models.allowed`, mappings, and that leaf's steps.
Pinning a call to a leaf by infra name is not in this release.

Each leaf owns its credentials. The engine does not forward the first leaf's `api_key_env` value; the routed provider reads `os.environ[that leaf's api_key_env]`. `mas-ctl` checks every live leaf at engine build, including a leaf the run never calls. A named model that no leaf claims is an error.

The middleware cache key includes the leaf name, canonical model, and `api_base`. A cache written for one endpoint does not hit on another. See [Cache key](../references/llm-cache.md#cache-key).

A **model router** (LiteLLM, an internal gateway) is still one `LLMProxy` whose `api_base` is the router URL. Put it in `infra_refs` like any other leaf.

An `LLMProxy` that only sets `models.generation` (no `api_base`) remains a policy overlay, not a second connection.

### Pipeline vs several leaves

The engine runs the pipeline. Callers always hold `LiveLlmEngine`. Each middleware plugin declares `for` (`llm` or `tool`) and `per` (`all` = one shared copy, `destination` = one copy per LLM leaf or tool server). Cache sets `for: llm` and `per: all`; scheduler and fault injection set `for: llm` and `per: destination`. A step with no `for` wraps `llm` only. `PipedLLMProvider` is the LLM leaf call; `PipedToolCall` is the tool-server wrap.

```text
LLM_CALL
  LiveLlmEngine
    → llm_cache          (common step on this engine)
      → assemble
        → router         (model name → LLMProxy leaf)
          → that leaf's pipeline   (llm_scheduler, fault_inject, typed entry.pipeline)
            → that leaf's provider

TOOL_CALL
  LiveLlmEngine
    → per: all tool steps
      → PipedToolCall for the server that claims this tool (destination steps)
        → execute_engine_tool
```

A `tools: "*"` (or omitted / empty-string `tools`) destination wraps every name no other server lists, except runtime system tools (`request_human_input`, `inform_user`, `create_subagent`). Those stay on the local dispatch unless a server lists them. After the tool-provider registry has initialized, the dest router sees that star server's advertised names and wraps only those; leftover unknown names stay on the raw dispatch.

How steps land there:

| Source | Where it runs |
| --- | --- |
| `InfraMiddleware` / workspace `middleware` with `middleware: llm_cache` | Common step on the engine |
| Other `InfraMiddleware` refs (scheduler, fault inject, …) | Cloned in front of **each** leaf |
| `LLMProxy.spec.pipeline` | That leaf only, except `llm_cache` (`per: all`), which is lifted to the shared wrap and applies to every connection |
| Typed bundle `entries[].pipeline` on an `llm_provider` slot | That leaf only |

`standard:llm-proxy-paced` is `[llm_cache, llm_scheduler, openai]`: the cache is common; the scheduler wraps the OpenAI leaf. Two LLMProxy refs plus that bundle clone the scheduler in front of each connection.

## Kinds

| `kind` | Purpose | Main `spec` fields |
| -------- | --------- | --------- |
| `InfraBundle` | Compose other infra files; recursive merge | `entries[]` (typed slots) or `includes[]` + `interceptors[]` |
| `InfraMiddleware` | Pipeline middleware (`llm_cache`, `fault_inject`) wrapped around LLM or tool calls | `middleware`, `for`, `per`, `params` |
| `InfraInterceptor` | Same shape as `InfraMiddleware`; listed under workspace `middleware` | `middleware`, `for`, `per`, `params` |
| `LLMProxy` | OpenAI-compatible endpoint, model allow-list, mappings, defaults | `protocol`, `proxy`, `models` |
| `LLMLocal` | Local inference (e.g. Ollama); `server` and `models.available` are aliases of `proxy` / `models.allowed` | `server`, `models` |
| `ToolServerRegistry` | MCP endpoint to consume or deploy, or local tool/skill paths (`protocol: local`) | `tool_servers[]` |
| `Application` | Named service and A2A agent endpoints | `endpoints` |
| `Datastore` | Named data-store connections (read by `mas-lab` pipeline steps) | `stores` |
| `ServiceBundle` | Processes or compose stacks started by `service_start` / `service_stop` | `services` |
| `ToolRegistry` | Logical tool-set id → JSON tool index path | `tools[]` |
| `ToolProvider` | Semantic tool name → in-process implementation | `tools{}` |
| `PersonalSecrets` | Logical token id → env var name (keep the file gitignored) | `token_refs[]` |
| `SecretsProvider` | Secrets backend id and parameters | `provider`, `params` |
| `RuntimeEngine` | Kernel/engine tuning; **rejected in `infra_refs`**, use `runtime_refs` ([runtime-engine.md](runtime-engine.md)) | `engine`, `cache`, `stream`, `parallel` |
| `Infrastructure` | Legacy alias of `LLMProxy` | as `LLMProxy` |

### Not used yet

The schema validates `ToolRegistry`, `ToolProvider`, `PersonalSecrets`, and `SecretsProvider` documents, and the resolver accepts them.
`mas-ctl` does not read their fields in this release.

### Typed bundle entries

`spec.entries[]` is the typed form of a bundle.
Each entry has these fields:

- `id`: the slot name.
- `type`: the leaf kind the slot expects. One of `llm_provider`, `llm_local`, `tool_provider`, `tool_registry`, `tool_server_registry`, `otel_collector`, `datastore`, `secrets_provider`, `application`, `infra_middleware`.
- `ref`: the leaf reference.
- `pipeline`: optional, a serial list of middleware steps. On an `llm_provider` / `llm_local` entry this wraps **that** leaf only. On an `infra_middleware` entry it stays in the shared merge (cache still hoists to the common wrap).

When `entries` is present, the loader ignores `includes` and `interceptors`.
This is `standard:llm-proxy-cached`:

```yaml
apiVersion: infra/v1
kind: InfraBundle
metadata:
  name: llm-proxy-cached
spec:
  entries:
    - id: cache
      type: infra_middleware
      ref: standard:llm-cache
      wraps: [llm]
    - id: llm
      type: llm_provider
      ref: standard:openai
```

### `for` and `per`

`InfraMiddleware.spec.for` is `llm` or `tool` (or a list). The plugin default is used when the field is omitted: cache, scheduler, and fault injection wrap `llm`.
`per` is `all` or `destination`. Cache is `all`; scheduler and fault injection are `destination`.
`applies_to` is a read-only alias of `for` on middleware and of `wraps` on bundle entries. Mixing the old name with the new name in one mapping is an error.

`mas-ctl infra list` prints installed refs (`--type llm` / `tool` / `bundle` / `middleware` / `runtime`, or a YAML kind). `mas-ctl infra show REF` prints one of those refs: connection fields and its pipeline, with params (`max_attempts`). A middleware ref prints its listed steps (no destinations, and no injected default retry). A RuntimeEngine ref prints the kernel fields it applies (`queue_depth`, `stream`), not a call pipeline. Missing destinations are omitted, not invented. `--connections` or `--pipeline` prints only that part. A `retry` step the runtime inserts when the pipeline omits one is marked `(default)`; an explicit retry is listed without that mark.

`mas-ctl infra resolved` prints the pipeline a session wraps (workspace refs, `--infra-ref`, Flavour bind, leftover `spec.control`) — the same wrap as `chat` / `run-mas`, including the default retry when the pipeline omits one. Each LLM and tool chain, and each agent's comm row when a MAS or Agent file is passed. `--type llm` / `tool` / `agents` hides the other sections. The comm column is how the agent is reached: the layers of the agent's Flavour [chain](#rows-a-flavour-binds) (`bind.agents.<id>.agent_comm`, else `bind.agent_comm`), bottom first, for an agent on a chain; the `protocol` of an agent reached at an A2A server; the row `protocol` for an agent at its own `url`; else the library default `agent_comm` plugin (`local`). An agent linked on chains also shows its name on the bottom layer of each one it joins. `--flavour NAME|PATH` selects the Flavour, as on `run-mas`. `api_base` and tool `url` print scheme, host, and path only:

```bash
mas-ctl infra show standard:openai --pipeline
mas-ctl infra resolved --infra-ref arm.yaml --infra-ref standard:llm-cache
mas-ctl infra resolved --json
```

## LLMProxy models

```yaml
apiVersion: infra/v1
kind: LLMProxy
metadata:
  name: openai-direct
spec:
  protocol: openai
  proxy:
    api_base: env:OPENAI_BASE_URL|https://api.openai.com/v1
    api_key_env: OPENAI_API_KEY
  models:
    mappings:
      default: gpt-4o-mini     # agents may write model: default
    allowed: [gpt-4o, gpt-4o-mini]
    defaults:
      embed: text-embedding-3-small
```

| Key | Meaning |
|-----|---------|
| `models.mappings` | Logical name → provider model id. The resolver follows chains (`default → mini → gpt-4o-mini`); a cycle is an error |
| `models.allowed` | Model ids this leaf serves (`available` on `LLMLocal`). With one leaf the list is informational. With several leaves it is the claim list the router uses |
| `models.defaults.llm` / `.embed` | Default chat and embedding model ids |
| `models.generation` | Output-token, context-window, and pricing policy; see [LLM output-token policy](#llm-output-token-policy) |

Other `spec` fields:

- `spec.proxy.timeout` (seconds) sets the HTTP timeout for LLM calls. The provider default is 120 s.
- `spec.proxy.api_key_env` defaults to `OPENAI_API_KEY`. Like every field that ends in `_env`, it holds the *name* of the env var that contains the secret, never the secret itself.
- `spec.protocol` selects the wire-protocol plugin and defaults to `openai`. Ollama and LiteLLM proxies also use `openai`.

String values may use `env:VAR|default`, so an environment variable can override a manifest default.
For example: `api_base: env:LLM_PROXY_API_BASE|https://api.openai.com/v1`.
`env:VAR` without a default is for secrets.
It resolves to an empty string when the variable is unset.

### LLM retry

Retry is a `retry` layer on the infra pipeline, not a field on `spec.proxy`
(`api_base`, `api_key_env`, `timeout`). Default steps load when the pipeline
omits retry. Prefer listing the layer. See [reliability.md](../references/reliability.md).

## Bundles

An infra bundle lists other infra files:

```yaml
apiVersion: infra/v1
kind: InfraBundle
metadata:
  name: dev-stack
spec:
  includes:
    - llm-proxy.yaml
    - otel-collector.yaml
    - tool-registry.yaml
```

Reference a bundle from workspace `config.yaml` or CLI `--infra-ref`, not from Agent or MAS manifests.
`includes` entries are paths (relative to the bundle file) or library refs such as `standard:openai`.
A bundle that includes itself, directly or indirectly, is an error.
`spec.pipeline[]` lists middleware refs, or inline `{middleware, params}` objects, that wrap the merged result, outermost first. `interceptors` is a read-only alias.

## Relative refs and anchor

Filesystem infra refs (for example `infra/llm-cache-write.yaml`) resolve **only** relative to one of two places:

1. The explicit **`anchor=`** passed to `resolve_infra_refs`. This is typically the MAS or agent app root, the parent of `mas.yaml` or `agent.yaml`.
2. The discovered **workspace root**, when `anchor` is omitted and `config.yaml` was found.

If the file is not found there, the resolver searches these places in order:

1. the workspace root
2. `<workspace>/infra/`
3. `<workspace>/config/infra/`
4. the anchor
5. the anchor's parent
6. `$XDG_CONFIG_HOME/mas/infra/` (default `~/.config/mas/infra/`)

Library refs (`standard:openai`) resolve from installed manifest libraries.

The process **working directory is not used** to locate infra YAML files.
Relative infra paths therefore stay tied to the application anchor when `mas-ctl`, benchmarks, or tests run from another directory.

Relative `cache_path` and `path` values on `InfraMiddleware` `spec.params` become absolute at load time.
They resolve against the **directory that contains that infra YAML file**, not the working directory.
See the [LLM cache reference](../references/llm-cache.md).

## A2A agent endpoints

An `Application` infra manifest can name external agent endpoints, so the MAS topology holds no deployment URLs:

```yaml
apiVersion: infra/v1
kind: Application
metadata:
  name: a2a-agents-local
spec:
  endpoints:
    weather-oracle:
      protocol: a2a
      url: http://127.0.0.1:9005
```

Each agent-keyed endpoint needs `url` and `protocol`.
A Flavour can also link an agent to an endpoint row instead; see [Rows a Flavour binds](#rows-a-flavour-binds).
Optional fields:

- `usage`: `connect` (default), `listen`, or `[connect, listen]`. `use`, `deploy` and `use-and-deploy` are deprecated aliases of `connect`, `listen` and `[connect, listen]`: still read, and `mas-ctl validate` and compose warn about them.
- `headers`.
- `timeout` (seconds).

The legacy `expose: true` is read as `usage: [connect, listen]`.
Canonical sample: [`library-samples/infra/a2a-agents.yaml`](https://github.com/ioc-project/mas-lab/blob/main/library-samples/infra/a2a-agents.yaml).

Reference the endpoint from an agency entry in the **MAS** manifest:

```yaml
# mas.yaml (fragment)
spec:
  agency:
    agents:
      - id: weather-oracle
        # The agency only names weather-oracle; infra matches by that name.
        # ref is omitted because this is an external dependency.
```

`usage: listen` on an in-process `run-mas` agent binds that URL for interrogation.
See [A2A internals: Exposure topology](../a2a/developer.md#exposure-topology).

### Not used yet

The current resolver is static and manifest-based.
Directory discovery and search or composition plugins are not shipped.

## Rows a Flavour binds

Experimental. An `Application` row can also describe a server or endpoint that
agents talk *over*: a SLIM node, an in-process channel, an HTTP url, an A2A server, or
a framing such as A2A. Name the row by what it is, never by an agent. The row's
`usage` is its capability: `connect`, a server we can dial that serves agents;
`listen`, infrastructure on which we can host the agents we run. Default `connect`,
as on every Application row. Infra only declares the row; a Flavour's
[`spec.bind`](flavour.md#bind) links agents to it and says what this deployment does
for each one, within that capability. A row is a layer only because the Flavour
names it.

```yaml
apiVersion: infra/v1
kind: Application
metadata: {name: desk}
spec:
  endpoints:
    desk-node: {protocol: slim, url: "http://127.0.0.1:46357", identity: acme/lab/desk, secret_env: SLIM_SECRET,
                usage: [connect, listen]}
    desk-a2a: {protocol: a2a-framing, usage: [connect, listen]}
    outage-server: {protocol: a2a, url: "http://127.0.0.1:9003", usage: [connect, listen]}
    agent-hub: {protocol: http, url: "https://hub.example.test/a2a/{agent}", usage: connect}
```

`protocol` is the layer plugin (`slim`, `local-group`, `http`, `a2a`, `a2a-framing`); every other
field but `usage` and the legacy `expose` is a param of that layer, checked by the plugin. A string param may hold
`{agent}`, replaced by each agent's id, so one row serves many agents (`agent-hub`
above); it is the only placeholder, and a Flavour can override a row's params per
agent. The agent id goes in as it is, never encoded, so an agent whose id holds
anything but letters, digits, `.`, `_` and `-` (a `/`, `?`, `#`, a space) cannot fill
`{agent}`: ctl refuses that row for it. That is the url grammar. A layer that names
agents has its own, stricter or looser: `slim` also refuses a `.` in the service of
`identity` and in the agent id, and `local-group` takes any name part
([Flavour](flavour.md#bind)). ctl checks every layer of a chain for every agent it
carries, whether or not the row uses `{agent}`. A transport that only connects (`http`) refuses a row that declares `listen`.
Layer params that a naming transport reads:

| Param | Meaning |
| --- | --- |
| `identity` | Three-part `org/ns/service` (`slim`, `local-group`). Each agent is `<identity>/<agent_id>`. |
| `url` | The node or server (`slim`, `http`, an A2A server). Plain HTTP only on `127.0.0.1`, `localhost`, `::1`. |
| `secret_env` | The *name* of the env var that holds the secret. ctl reads it and passes the value to the layer as `secret`; no layer reads the environment. |

The schema requires `protocol` on every row and cannot require `url`: an agent-keyed
row and an endpoint row differ only by their key (an agent id of the MAS, or an
endpoint name), and an infra document is validated without its MAS. A framing row
(`a2a-framing`) and an in-process channel have no address. ctl refuses an
agent-keyed row without a `url` when it applies the Flavour, where it knows the agent
ids, and the plugin of an endpoint row that needs a `url` refuses the row without one.

A key named `secret`, `password`, `passwd`, `passphrase`, `token`, `api_key`,
`authorization`, `cookie`, `credential(s)` or `private_key`, in any case, alone or after
`_` or `-` (`access_token`, `X-Api-Key`), or capitalized after a lower-case letter
(`accessToken`, `clientSecret`), is refused: by `mas-ctl validate` for a row's own keys
and its `headers`, and by ctl at any depth of a bound row when it applies the Flavour, and so is an agent-keyed row that names no protocol. A row
resolves to an `agent_comm` link first, then a `layer`
([the rule](../references/comm-stack.md#stack)). So `a2a` (a one-row chain on an A2A
server) is the A2A SDK client at its url, the same as an agent-keyed `a2a` row, and
`a2a-framing` is the A2A framing layer above a transport. A missing secret stops the
run when the chain starts.

Every local agent on a chain gets its own instance of the layers and its own
receive; what the layers share (the SLIM node connection, the in-process fabric) is
created once per chain and session and closed after the last agent's layers stop.
Each distinct chain gets one handle. An agent that delegates gets a link on the chain
of each agent it delegates to that the Flavour dials (`connect`).
`mas-ctl serve` refuses an agent on a stack chain; run it with `run-mas`.

Walkthrough: [Tutorial 7](../tutorials/07-slim-link/README.md).
Plugin cards: [`slim`](https://github.com/ioc-project/mas-lab/blob/main/library-ioa/src/library_ioa/plugins/slim/slim.md),
[`http`](https://github.com/ioc-project/mas-lab/blob/main/library-ioa/src/library_ioa/plugins/http_layer/http.md),
[`a2a-framing`](https://github.com/ioc-project/mas-lab/blob/main/library-ioa/src/library_ioa/plugins/a2a/a2a-framing.md).

## Separation from Flavour

| Infra | Flavour |
|-------|---------|
| **Where** (URLs, keys env names, registry paths) | **How** (protocol, OTel backend choice, tool-server policy) |
| Shared across flavours | Selected per deployment profile |
| Declares the rows that exist | `bind` names the rows a session rides |

## LLM cache middleware

Record and replay LLM responses with `kind: InfraMiddleware` and `middleware: llm_cache`.
Attach it with `--infra-ref` or workspace `infra_refs`.
The guide is [llm-cache.md](llm-cache.md) (record and replay walkthrough, manifests, pipeline order).
The reference is [llm-cache.md](../references/llm-cache.md).

Only `InfraMiddleware` and `InfraInterceptor` refs add pipeline steps.
Provider refs (`LLMProxy`, `standard:openai`, `standard:ollama`) do not.
With one cache middleware, the order of provider and cache `--infra-ref` is equivalent.
With several middleware refs, the **first merged ref is outermost**.
Refs listed under workspace `middleware` (alias `infra_interceptors`) append after the `infra_refs` steps.
See [llm-cache.md: Pipeline order](llm-cache.md#pipeline-order).

## LLM scheduler middleware

Pace live `LLM_CALL` after cache and routing with `middleware: llm_scheduler`.
Stack it **inside** `llm_cache` so replay never waits on a slot.
`spec.params` is that plugin's constructor kwargs (`max_in_flight`, `state_path`),
not a shared Infra field — same shape as `llm_cache` and `fault_inject`.
The guide is [llm-scheduler.md](llm-scheduler.md).

## Fault-injection middleware

`middleware: fault_inject` fails a fraction of `LLM_CALL`s with a synthetic HTTP error, for reliability experiments.
The bundled ref is `standard:chaos-lite`.

| Param | Default | Meaning |
|-------|---------|---------|
| `rate` (alias `failure_rate`) | `0` | Probability (0-1) that a call fails |
| `status_codes` (alias `errors`) | `[503]` | Status code reported; only the first entry is used |
| `message` | `injected fault` | Error text |

```yaml
apiVersion: infra/v1
kind: InfraMiddleware
metadata:
  name: chaos-lite
spec:
  middleware: fault_inject
  for: llm
  per: destination
  params:
    rate: 0.05
    status_codes: [503, 429]
    message: chaos-lite injected fault
```

## CLI overrides

Address resolved infrastructure with the same root-qualified syntax:

```bash
mas-ctl chat agent.yaml \
  --override 'infra:spec.tool_servers[id=mcp].port=9100'
```

This patches the effective in-memory infrastructure.
It does not modify the referenced YAML file.

Middleware parameters are under `infra:spec.interceptors[middleware=llm_cache].params` (the leftover `interceptors` address; `pipeline` is the YAML name), and runtime profile values under `infra:spec.runtime_engine`. All resolved resources survive these patches. To patch a standalone infra leaf, use [`mas-ctl compile`](../cli/compile.md#other-manifest-kinds) with `manifest:`.

For reusable infra changes, use an Overlay with `target.kind: Infra`.
Put root-qualified assignments under `spec.overrides`; see the [Overlay manifest reference](overlay.md).

`--connect NAME=a2a://...` and `--connect NAME=mcp://...` are a **CLI shortcut** for the Application `usage: connect` and ToolServerRegistry `usage: connect` fields.
They expand to `--override infra:...` and merge into the effective infra document.
They are not a second resolver.
Flavour stays `local` until an Application endpoint, from YAML or `--connect`, names a remote collaborator.
A SLIM node is a [row a Flavour binds](#rows-a-flavour-binds), not a `--connect` scheme.

## LLM output-token policy

`LLMProxy` and `LLMLocal` can declare `spec.models.generation`.
It is the operator policy for output tokens on every model that the endpoint serves.
Without it, the runtime sends no limit unless an agent sets one, and the server default applies.

| Key | Effect |
|-----|--------|
| `max_tokens` | Default budget when `spec.models[]` sets none |
| `max_completion_tokens` | Default answer + reasoning cap; wins over `max_tokens` |
| `max_output_tokens` | Ceiling. The runtime lowers every value to it with a warning, including `--max-tokens` and `MAS_LLM_MAX_TOKENS`. When the [model catalog](llm-model-catalog.md) also lists a `max_output_tokens`, the lower of the two applies |
| `on_truncation` | Default truncation policy: `ignore`, `warn`, `error`, `escalate`, or an object `{action, factor, max_tokens, retries}` |
| `context_window` | Input-window override for models on this endpoint. A `spec.models[].context_window` on the agent wins; otherwise the model catalog, then a `128000` fallback with a warning |
| `pricing` | Cost-estimate override in USD per million tokens: `input_per_million_tokens`, `cached_input_per_million_tokens`, `output_per_million_tokens`. Replaces catalog rates |

This example fits a shared vLLM server started with `--override-generation-config '{"max_new_tokens": 8000}'`.
The ceiling matches the server, so no request asks for tokens the server would not give:

```yaml
apiVersion: infra/v1
kind: LLMProxy
metadata:
  name: shared-vllm
spec:
  proxy:
    api_base: env:LLM_PROXY_API_BASE|http://vllm.example.com:8000/v1
    api_key_env: VLLM_API_KEY
  models:
    generation:
      max_tokens: 4096
      max_output_tokens: 8000
      on_truncation:
        action: escalate
        retries: 1
```

When several refs merge, later refs override earlier ones key by key.
For a single run, patch the effective infra instead of editing the file:

```bash
mas-ctl chat agent.yaml \
  --override 'infra:spec.models.generation.max_output_tokens=4000'
```

[agent.md: Output-token limits](agent.md#output-token-limits) covers agent fields, precedence, and how to spot a budget that is too small or too large.

## Local tool source

`standard:local-tools` is implicitly merged into infra for every run.
It provides `tools_dir: tools` and `skills_dir: skills`, relative to the app root.
Override either path with another `ToolServerRegistry` entry that uses `protocol: local`.
A consumed non-local `usage: connect` server replaces the implicit local
provider. A usable server whose protocol has no provider is an error.
A `usage: listen` server is not a client.
Add an explicit local `tools` claim to keep both.

```yaml
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

## ToolServerRegistry

A `ToolServerRegistry` says **where** a remote tool process lives.
It does not hold the tool's advertise contract ([tool.md](tool.md)).
The agent manifest does not declare tool providers.

Full field reference: [tool-server-registry.md](../references/tool-server-registry.md).
Schema fragment: [`infra-tool-server.schema.yaml`](../schemas/runtime/fragments/infra-tool-server.schema.yaml).

```yaml
apiVersion: infra/v1
kind: ToolServerRegistry
metadata:
  name: mcp-localhost
spec:
  tool_servers:
    - id: localhost-mcp-tools
      protocol: mcp
      usage: connect
      transport: streamable-http
      url: http://127.0.0.1:9001/mcp
      timeout: 30
      follow_pagination: true
      cache_scope: private
      # headers:
      #   Authorization: "env:MCP_AUTH_HEADER"
```

Canonical sample: [`library-samples/infra/mcp-localhost.yaml`](https://github.com/ioc-project/mas-lab/blob/main/library-samples/infra/mcp-localhost.yaml).
It sets only the required connection fields, and runtime defaults supply the optional policy.

| Field | Default | Meaning |
| ------- | --------- | --------- |
| `id` | required | Stable infra key for the remote tool server |
| `protocol` | required | `mcp` for an MCP server, `local` for app-local tool/skill directories |
| `name` / `description` | `id` / none | Display name and free text |
| `usage` | `connect` | `connect`, `listen`, or `[connect, listen]` (`use`, `deploy`, `use-and-deploy`: deprecated aliases) |
| `transport` | `streamable-http` | `stdio` \| `streamable-http` \| `sse` \| `http` |
| `url` / `endpoint` | none | HTTP/SSE URL (manifest). Optional override `env:VAR\|default` |
| `host` / `port` | `127.0.0.1:9001` | Listener address for `usage: listen` |
| `tools_dir` | `tools` | Tool manifest directory (`protocol: local`, or served when deploying) |
| `skills_dir` | `skills` | Skill directory (`protocol: local`) |
| `command` / `args` / `env` / `cwd` | none | stdio process |
| `headers` | `{}` | Omit unless auth is needed. Secrets: `env:VAR` (unset omits the header) |
| `timeout` | `30` | Per-call timeout (seconds) |
| `follow_pagination` | `true` | Walk MCP `nextCursor` and flatten |
| `cache_ttl_ms` | omit | Client list-cache TTL in ms (`0` disables; omit caches until invalidate) |
| `cache_scope` | omit | `public` \| `private` (private keys the list cache by user) |
| `tools` | `"*"` | Discover all names, or declare an explicit list in infra |

`--infra-ref` loads this document into `ResolvedInfra.tool_server_registry`.
The `usage` values behave as follows:

- `connect` connects to a remote endpoint (CLI `--connect`).
- `listen` starts the local server (CLI `--listen`).
- `[connect, listen]` does both.

`use`, `deploy` and `use-and-deploy` are deprecated aliases of `connect`, `listen` and `[connect, listen]`: still read, and `mas-ctl validate` and compose warn about them.

The runtime discovers the names that MCP advertises unless `tools` is set.

[`library-samples/infra/tool-providers.yaml`](https://github.com/ioc-project/mas-lab/blob/main/library-samples/infra/tool-providers.yaml) is a `ToolProvider` sample (semantic name → Python class).
`mas-ctl` does not read it in this release; see [Kinds](#kinds).

## See also

- [LLM cache](llm-cache.md): `llm_cache` middleware guide
- [LLM scheduler](llm-scheduler.md): live-call in-flight and RPM caps
- [LLM cache reference](../references/llm-cache.md): parameters, pipeline model, implementation
- [ToolServerRegistry reference](../references/tool-server-registry.md)
- [Flavour manifest](flavour.md)
- [user-config.md](../user-config.md): workspace and `infra_refs`
- [Runtime engine manifests](runtime-engine.md): `RuntimeEngine` and `runtime_refs`
- [LLM model catalog](llm-model-catalog.md): per-model windows, ceilings, pricing
- Source: `ctl/src/mas/ctl/infra/models.py` and `ctl/src/mas/ctl/infra/resolve.py`
- Tutorials that use infra: [MCP tools](../tutorials/04-mcp-tools/README.md) and [A2A agents](../tutorials/05-a2a-agents/README.md)
