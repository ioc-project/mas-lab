<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Changelog

All notable changes to MAS-Lab are documented here. This branch is the
v0.2 release-prep branch after rebasing onto the latest upstream main and
reconciling the release notes with the code and docs currently on that branch.

## Unreleased

### Added

- `mas-lab init` writes `api_base: env:OPENAI_BASE_URL|<url>` so the file names the env var. `standard:openai` and the sample workspace do the same. A custom URL at init uses `<infra-name>_API_BASE` so a leftover `OPENAI_BASE_URL` does not steal the call.
- Write a new tool as `async def on_execute_tool` (or `async def execute`). That one method is enough; the engine awaits it. A `def on_execute_tool` still works: the engine may run it in a worker thread. `on_aexecute_tool` still wins when present. The sync path finishes an async tool instead of returning a coroutine. Calling `call_tool` from a running loop raises; use `acall_tool`.
- `gov_no_undeclared_peer` BLOCKs leftover `delegate_to_*` to a dest not in this agent's `delegates_to`. A user tool named `send` or a declared `delegate_to_<x>` tool is left alone. `with-hardened` names it. Send itself does not refuse.

### Changed

- `--cache-read` / `--cache-write` help now says the cache is off until those flags insert the `llm_cache` step. The default pipeline is retry only.
- The cache docs describe one LLM cache, the `llm_cache` step. The "built-in engine cache" sections are gone; `MAS_LLM_CACHE_READ` / `_WRITE` and `RuntimeEngine` `cache.read` / `write` insert that step.
- `behavior.delegation_style: none` hides the leftover `delegate_to_*` tools from the LLM. The peers and the send path stay, so a linear, parallel, or single pattern can still send to the next agent.
- Leftover `delegate_to_*` tools shown to the LLM are this agent's `delegates_to`. An empty list means none of those tools. An agent not listed in the workflow shows none. `workflow.entry` only names who receives the user message.

### Breaking

- A pattern's next hop is this agent's `delegates_to`. The old fallbacks (`workflow.participants`, every other node, the agency list) are gone, and an agent with no `delegates_to` has no next hop (the pattern logs a warning and ends the turn). `behavior.delegation_style` does not affect a pattern's next hop.
- `send` no longer refuses a target outside `delegates_to`; the model can only call peers that are offered, and `gov_no_undeclared_peer` (in `with-hardened`) blocks the rest. It does nothing when the kernel has no agent id.
- `standard:openai` now reads `OPENAI_BASE_URL` when that env var is set (`api_base: env:OPENAI_BASE_URL|https://api.openai.com/v1`). A hardcoded URL in that file used to ignore the env var. `openai.example.yaml` and the sample workspace use the same form; they used to read `OPENAI_API_BASE`. `OPENAI_API_BASE` is still used only when `api_base` is left empty.
- LLM answers are stored only by the `llm_cache` infra step. `--cache-read` /
  `--cache-write` / `--cache-read-only` turn that step on or change its flags.
  The old provider wrap on the chat path is gone. A cache file written by that
  wrap may not hit. Default read and write stay off until you turn them on.
- `default_pipeline` in the user config attaches middleware when the workspace
  lists none, the same way `default_infra` attaches an LLM endpoint.
- `with-hardened` is one Control, written once for every agent you deploy: pass it with
  `--control pkg://mas.library.standard/controls/with-hardened.yaml` (or attach it from a Flavour with `agents: $all`).
  The `with-hardened` overlay is gone. It had set `design_pattern.params.max_steps: 512` (set it in the agent or a
  RuntimeEngine manifest). Retry defaults to 4 LLM and 2 tool attempts as documented pipeline steps.
  The circuit breaker is `standard:llm-proxy-hardened` (and `standard:tool-server-hardened`), not this Control.
  `-o openclaw` no longer attaches governance: its governance is the Control of the same name.
  `observability-native.yaml` moved from the overlays directory to `controls/` and is passed with `--control`.
- Control `spec.observability` and `spec.governance` are sets of plugins. Matching
  Controls union by plugin id; a later Control's config for the same id wins.
  A Control that names the same plugin id twice is refused. Overlay `$op.add`
  replaces the same id. Governance plugins that attach to the same transition
  run in `priority` order (higher first, default 0). Attach does not copy those
  lists onto the agent.
  Overlay patches that set them target `kind: Control`. The shipped HITL Controls
  (Tutorials 1 and 14, `hitl-on-tool`) and `observability-native` are `kind: Control`
  files in `controls/` directories, passed with `--control` or attached by a Flavour.
  A plugin `priority` must be an integer; any other value is refused at load and by `mas-ctl validate`
  instead of counting as 0.
  `openclaw` keeps its other patches. A tool that loaded `with-hardened` or `openclaw` governance as an
  Overlay patch must read the Control instead.
- Call ceilings are the new `budget` governance plugin (`max_llm_calls`, `max_tool_calls`; it TERMINATEs the run at
  the cap), named in a Control `spec.governance`. A Control has no `budget` key. Agent `spec.budget` is deprecated
  and still enforced. The `with-hardened` ceilings are in its Control.
- A Control lists `observability` and `governance` plugins only. Every item in a Control list is looked up by
  its name among the plugin types that list takes, and is refused when it is unknown, ambiguous within those types,
  or of another type. Delay, drop, fail, corrupt and replay are not Control plugins: they belong on the infra
  pipelines with retry and the circuit breaker. `spec.impair`, `spec.plugins`,
  and `spec.control` on a Control are leftover and refused. Agent `spec.control.retry` and
  `.circuit_breaker` still apply (deprecated, one warning) and become pipeline steps.
- A Flavour `spec.control` row takes `settings`, merged over the named plugins of the Control it attaches, so one
  Control file gives different settings to different agents. The same file on the same agents with the same settings
  is one attachment.
- Plugin names: matching is exact (the registry no longer matches the end of a URN), a name has no `.`, and two
  plugins of one type cannot answer to the same name. Aliases that were only another way to say the same thing
  (`analyst`, `planner`, `linear`, `rate_limiter`, `chaos_lite`, ...) move to `deprecated_aliases` in `aliases.yaml`:
  they still resolve, with a one-time warning naming the replacement. `name@version` and the bare shortcut stay.
- The debugger is the `gdb` governance plugin, named in a Control; `debug_script` and `gov_debug_script` are
  deprecated aliases. `spec.debug`, the workspace `plugins:` list, `lab.enable_plugins`, `runtime_plugins.py` and
  `workspace_plugins.py` are removed; `mas-ctl chat --debug-script` writes a Control that names the plugin. A
  checkpoint store is chosen by `spec.checkpoint.storage`.
- Each leftover Agent list (`spec.observability`, `spec.governance`) is deprecated and still
  applied when no matching Control names that list; a Control that names only observability no longer
  drops the agent's governance. Leftover Flavour
  `observability` is unread; it was a working fallback in 0.2.0. Name the event
  plugin in a Control observability set the Flavour attaches, or use `--events*`.
  A run that sets none still records native events.
- The bundled `mcp` flavour is removed. It set only `tools.exposure_protocol`,
  a key nothing reads, so `--flavour mcp` never exposed tools; it now fails as
  an unknown flavour. There is no replacement: tool exposure is configured
  through infra.
- Every entry point refuses, before it runs, an agent-keyed Application row
  with no `protocol` or no `url`; such a row was ignored or failed when the
  session wired it.
- `agent_comm/stack` no longer forwards unknown attribute names to its
  layers. It reports `agent_id`, `plugin_id` and `endpoint` from its bottom
  link, and `fabric_id`; a `BoundLayer` above it sees `send`, `asend`,
  `capabilities` and that identity of the layer below.
- Infra middleware writes `for` (`llm` / `tool`) and `per` (`all` /
  `destination`). Bundle entries write `wraps` for the slots they cover.
  `applies_to`, `interceptors`, `pipeline` (on a bundle list), and
  workspace `infra_interceptors` still load and warn; mixing an old key
  with its new name in one mapping is an error.
- Two `LLMProxy` files with the same `metadata.name`: the later ref replaces
  the earlier leaf (connection, `models.allowed`, mappings, and that leaf's
  steps). A different `api_base` on the same name is no longer an error.
- With several LLMProxy leaves, a model none of them claims is an error.
  Engine build requires every live leaf's `api_key_env`, including a leaf
  the run never calls.
- The LLM cache key now includes the leaf name, model, and endpoint. A
  cache written on this branch will not hit after the leaf's `api_base`
  changes. Entries written before still hit when only one leaf is
  configured. `trace_cache_portable` does not cover this key.
- Infra middleware plugins must define
  `wrap(inner, params, *, call_kinds=)`. The old constructor fallback
  `plugin_cls(inner=..., **params)` is gone.
- `LiveLlmEngine.install_infra_pipeline` only wraps `per: all` steps.
  A destination step (scheduler, fault) passed there is an error.
- Native→KG default output is the SCOPE set: RAG / memory / skill kinds
  and `ThinkingCall` sit in `native_extensions` (off);
  `include_trajectory` defaults off (`ParallelGroup` / `Branch` /
  routing). `all_attributes=False` keeps only keys the installed SCOPE
  models emit (`KGNode` / `KGEdge.to_json`). `callsAgent` and `usesTool`
  are not emitted until A2A / MCP observability lands.
- KG ontology dependency is SCOPE (`ioc-scope-ontology` from
  PyPI, import `scope_ontology`). The OTel extra installs `norm` from PyPI
  as `ioc-scope-norm`.
- `compare_kg` `strict` defaults true: extra node types, edge types, and
  attribute keys on the candidate fail. `strict_nodes`, `strict_edges`,
  `strict_node_attributes`, and `strict_edge_attributes` override one
  axis. `element_level_diff` (raw ids) is reported and does not decide
  `passed`.

### Fixed

- `mas-lab init` never writes `OPENAI_API_BASE` for a custom URL (an infra called `openai` used to) and never a name that starts with a digit.
- The adversarial and trip-planner LLM judges follow `OPENAI_BASE_URL`, then `OPENAI_API_BASE`, like the engine.
- A tool whose `on_execute_tool` returns a coroutine through a `functools.wraps` decorator is awaited on the loop instead of raising under `engine.tool_worker: inline`.
- The sync tool path (`call_tool`) finishes an async tool on one shared helper loop, so a client kept between calls stays valid. It used a new loop per call.
- A `None` answer from `on_aexecute_tool` / an async `on_execute_tool` falls back to the legacy `execute` like the sync path does.
- `mas-lab init` gives a second infra name with the same slug its own variable (`MY_PROVIDER_API_BASE_2`).
- A LLM address taken from the shell (`OPENAI_BASE_URL`, `*_API_BASE`) logs one warning with its host: the API key is sent there.
- The v0.2 blog post links to `aaif.io`.
- An explicit pipeline retry keeps the params it named.
- Session close stops the llm and tool call layers.
- A withheld delegated turn includes the governance or boundary reason in the
  `[send]` failure text.
- An MCP client reuses one session for list and call after `connect`. The sync
  wrapper keeps that session on one event loop so initialize is not repeated,
  starts it once when parallel tool calls arrive together, reconnects after the
  server connection closes, and ends a stdio server at `close()` or process exit.
- Sessions on separate threads no longer disturb each other. Working memory is
  stored per session, so one session walking its entries cannot meet another
  session adding or dropping its own. The plugin registry and the shared LLM
  cache are created under a lock. Tests run eight sessions on threads, on one
  event loop, and on one loop per thread.
- A2A `message/send` gives each `contextId` its own runtime: kernel, context and
  engine, built again from the agent's manifest (`RuntimeInstance.conversation_factory`).
  Omit `contextId` and a new conversation starts. Conversations run in parallel
  and share nothing, and each one remembers its earlier messages: the turn is
  committed to its context, which the listener used to skip. A second message on
  the same id waits in a queue. At most 1024 conversations stay open; a further
  new `contextId` is refused with `TooManyConversations`. An agent that cannot be
  rebuilt (an injected engine) serves one conversation. A `run-mas` agent opens
  each conversation as a new copy of the whole MAS, wired like the run.
- `contract_call_*` has its own `call_id`. The inner `llm_call_*` / `tool_call_*`
  keeps its `call_id` and parent. A span store keyed only by `call_id` no longer
  overwrites the model tokens with the wrapper.
- When no LLMProxy `api_base` is set, `OPENAI_BASE_URL` (or `OPENAI_API_BASE`) is
  used. The missing-address error names the infra manifest and that env var. The address reaches the LLM
  provider too, not only the engine's own label.
- A denied tool is that call's result. The next model call is a later step, not
  started from inside the denial. In a parallel batch the allowed siblings still
  run, and the model sees every denial.
- A batch with more calls than `engine.queue_depth` runs in chunks of that size, in
  order. It no longer fails with "engine outbound queue full".
- Plugin tools on the async path overlap in a parallel batch. `on_aexecute_tool`
  or provider `acall_tool` runs on the event loop. A sync tool runs in a thread by
  default; `engine.tool_worker` (`thread` or `inline`) and `engine.tool_concurrency`
  in the RuntimeEngine manifest set where and how many. A plugin tool gets the same
  frozen `ToolCallContext` wherever it runs, so placement changes timing only. A tool
  that must change session state (skills) declares `kernel_state` and runs inline.
  Each sync tool class is logged once (a warning under `inline`, which blocks the
  event loop); the authoring guide explains how to write the async version.
- A parallel batch commits as a unit. Every call is decided first; a HITL pause,
  TERMINATE or plugin error stops the batch with no call left waiting and no
  envelope left open. Checked over every combination of decisions for 2 and 3 calls.
- With `parallel: false` every call of a reply runs, one at a time in order, and
  the model is asked once after the last result. Before, only the first ran and
  the model had to ask again for the rest. A denial is that call's result; a HITL
  pause keeps the rest queued.
- A governance plugin name that is not registered as a governance plugin is now an error. It was a logged warning
  and the plugin was skipped, so a typo in a Control disabled it silently. The runtime names no fallback plugin.
- The Agent deprecation warning printed a literal `{whom}`.
- `governance-off` is an Overlay that targets Control: empty governance and native events, so stacking it after a governance Control clears those policies.
- Applying the same Control twice does not attach the same plugins twice even when two selectors match.
- `--events-sink` and Flavour `bind.events` add a sink and keep native `events.jsonl` unless a Control named the observability list (including `[]`) or leftover agent observability named plugins.
- An MCP tool that declares a `ctx` parameter gets the SDK context on Python 3.14: the registered wrapper keeps its
  annotations, which `functools.wraps` no longer copies there.
- Native `llm_call_end` reads top-level `tokens_used`, `model`, and
  `finish_reason` (the shape the runtime actually writes). Native
  `AgentCall.success` follows an `ok` / `success` execution status.
- `library-lab` defined `DeterministicVerifierPlugin` twice in one file;
  only the first class ran.
- Native CallAnnotation kinds (`state_update_*`, `context_assembled`,
  `observability_*_execute_*`, `client_response`, …) sit in an
  `annotation` layer, off by default, matching observe-sdk. Parallel
  group events emit a `ParallelGroup` span when a run actually forks,
  not a CallAnnotation. `compare_kg` can restrict the check to a list
  of `node_types`.
- A design pattern's protocol lines are built for each LLM call from the
  pattern's current phase. `plan_execute` synthesis and replan calls and the
  `tree_of_thoughts` final call now see their own instructions instead of
  the first phase's. The lines no longer pile up in the system prompt, one
  copy per turn.
- Memory seeds appear once in the system prompt. They were added twice on
  the first turn and once more on every later turn.
- ctl rejects a bad checkpoint on save and load. Disk or hybrid
  storage with no directory is a ValueError.
- A leaf scheduler reads the in-flight model from the call wrapper, so
  two models on one connection get separate pacing buckets.
- A middleware step with no `for` wraps llm calls only, same as before
  this branch. An empty `for` list is llm, not every kind.
- `mas-ctl infra resolved` prints `api_base` / tool `url` without userinfo or
  query.
- Destination tool steps wrap the server that claims the tool name,
  with the same explicit-over-star rules as tool routing. A star
  destination does not wrap runtime system tools. Once advertised
  names are known, it wraps only those names.
- The env override (`MAS_CTL_MODEL` / `MAS_LLM_MODEL`) also sets the
  eval judge when `models.judge` is omitted, so scoring uses the same
  model as the agents. `models.judge` and `evaluation.model` still win.
- A `per_run` step under experiment-level `post:` fails at parse,
  including `--dry-run`.

### Added

- Move retry, the circuit breaker, cache, scheduler, fault inject, delay,
  drop, fail, corrupt, and replay off separate plugin types and onto
  steps around llm, tool, and send calls.
  `standard:llm-proxy-hardened` and `standard:tool-server-hardened`
  list retry outside the breaker (`failure_threshold` 5, `reset_timeout_s` 30,
  `on: [unavailable]`). `standard:retry-llm`, `standard:retry-tool`, and
  `standard:circuit-breaker` are the same steps as standalone refs. The breaker
  keeps its state in the running spec (`spec.layer_state`); after
  `reset_timeout_s` exactly one call is let through as the probe. A tool that
  declares a `probe` leaves the running spec when its circuit opens and
  returns when that probe succeeds.
- Stop retrying inside the engine; when a run omits retry, insert one
  (LLM 4 times, tool twice when the tool says it is safe to retry),
  marked `(default)`. `mas-ctl infra list`, `show`, and `resolved` print
  those steps and mark the default.
- `with-hardened` was an overlay that patched the agent spec; it is now a
  Control for call ceilings and error policy. The circuit breaker moves
  to `standard:llm-proxy-hardened` and `standard:tool-server-hardened`.
- Give each of those steps a name (`retry-llm`, `breaker-llm`, ...) and an
  off switch. A name the runtime does not know is an error at start.
- Agent `spec.system_plugins` grants runtime syscalls (`inform_user`,
  `request_human_input`, `send`, `pause_session`, `subagents`, `activate_skill`,
  `run_skill_script`). A plugin owns session state and may expose several tools.
  `subagents` advertises `create_subagent`, owns the spawn ledger and runs the call (the
  session executor binds the spawner that builds the child; sync and async); a session
  checkpoint or in-memory snapshot restores system plugin state, and a restore keeps the
  ceilings the spec sets and the spend the snapshot saved. `send` is the engine's delegation
  and claims only the `delegate_to_<id>` names the model was offered. An empty list loads none, except
  skill plugins implied by `spec.skills`. Unknown names, `enabled: false` of an
  unknown name, and two plugins exposing one tool name in one agent are load
  errors. Overlay `$op.add` / `$op.remove` patches `system_plugins` by plugin id
  and `mas-ctl validate` errors on an unknown name there too.
- Flavour `spec.control` attaches `kind: Control` documents (observability and
  governance sets) to agents by `$all`, `$entry`, `$not-entry`, `$delegates`, one
  id, a list of ids, or the same agent path as `--override` (`.mas.agent[id]` /
  `.mas.agent[*]`). Each id is looked up in the registry. Matching Controls union
  by plugin id. Governance plugins on the same transition run in `priority` order
  (higher first, default 0). A plugin's settings are a map. Attach does not copy
  lists onto the agent. A Control names plugins and uses paths relative to itself:
  an absolute path in a Control, a path that walks up with `..`, or an absolute
  `ref` in a Flavour's `spec.control`, is refused. A Flavour `ref` cannot walk
  out of that Flavour's directory with `..`. A plugin resolves its relative paths
  against the Control's own directory. A stacked lab Control overlay copies those
  lists onto the agent dicts the bench kernel reads, and warns once when an empty
  list replaces leftover YAML. `$entry` is `workflow.entry` or `spec.entry_agent`;
  omitted, it matches nobody. `$delegates` is that entry node's `delegates_to`
  list; omitted or empty, it matches nobody. `mas-ctl compile` does not copy
  Control lists onto the resolved spec. A Flavour with no Control, and a Control
  that names no plugin, add none. A Control file is plugin lists only. Retry and
  circuit_breaker stay on Agent `spec.control` (deprecated, still applied). They
  are not Control plugins. `spec.control`, `spec.plugins`, and `spec.impair` on a
  Control are refused. A relative path in a plugin setting must be listed in
  `path_params` (a file next to the Control) or `run_path_params` (a path the run
  writes); an unlisted one is refused.
- `--control REF[@SELECTOR]` (`chat`, `tui`, `serve`, `run-mas`, `compile`, `infra resolved`, `mas-lab benchmark`) attaches a
  Control to the session's Flavour from the command line; the run hash includes it. `--events-sink ROW` adds a row to `bind.events`.
  `serve` takes `--no-events` and `--events-format` the way `chat` does.
  A scenario's `overlays.control` attaches Control files the same way. The flags are another way to write
  the Flavour, not another mechanism. Shipped Controls live in `controls/`
  directories
  (`pkg://mas.library.standard/controls/…`, `library-samples/controls/`, the tutorials).
  Shipped constraint overlays (`observability-native`, `with-hardened` governance,
  tutorial HITL) are Control documents.
- Bundled flavour `prod` sets `require_usage`.
- Flavour `bind.events` (and `--events-sink ROW`) names infra Application rows to send events to. Each
  row adds the plugin its `protocol` names and gives it the row's settings.   Native `events.jsonl` still
  records unless a Control named the observability list (including `[]`) or leftover
  agent observability named plugins. `--no-events` and an explicit
  `--events-format` still replace the set.
- Flavour `spec.require_usage`: every agent of the session must have a
  bind chain or an agent-keyed Application row.
- PyPI names are `mas-lab-runtime`, `mas-lab-ctl`, `mas-lab-library-*`,
  and `mas-lab-component-*`. `mas-lab` is an empty meta-package that
  installs the rest. The `mas-lab`, `mas-ctl`, and `mas-runtime`
  console scripts are unchanged. Wheel names are read from each
  `pyproject.toml`; code, install hints, and human docs use directories
  and `mas._dist` instead of copying those strings. Schema registry
  `source` is a directory key (`runtime`, `bench`, `core`, `controller`).
- library-ioa adds a SLIM layer.
- Communication is a stack of layers.
- A2A framing runs over SLIM through that stack.
- Flavour bind names the infra rows of each agent's chain; usage defaults
  to connect.
- [Tutorial 7](docs/tutorials/07-slim-link/README.md) moves a desk onto a
  SLIM node with a Flavour, splits it across two processes, and mixes in
  an A2A server.
- `design_pattern: state_machine`: the entry agent's behaviour as a table of
  named states. Each state delegates to agents, runs any registered design
  pattern as its child (ReAct, plan-execute, another state machine...), runs
  regions in parallel, calls a tool, or ends the turn; guarded transitions pick
  the next state, and `signals` (a step budget, a timeout, an outcome, a steer) can
  move the machine from any state. Replies are read as JSON fields (checked
  with a pydantic model) or a leading word. The agent is told the format from
  the same spec, an unreadable reply is asked again with the reason, then the
  state fails closed, and a public transcript can be kept per state.
- `design_pattern: moderated_rounds`: a preset of `state_machine` for a group
  that must agree on one plan. Fixed-order rounds, a decider's JSON action
  after each, and a confirmation round per proposed plan.
- `mas-ctl infra list` prints installed refs (`--type` filters kind).
  `mas-ctl infra show REF` prints that catalog entry (connection and pipeline,
  with params). A middleware ref prints its listed steps. A RuntimeEngine ref
  prints the kernel fields it applies (`queue_depth`, `stream`), not a call
  pipeline.
  Destinations that are not on the ref or the merged run are omitted.
  `mas-ctl infra resolved` prints the steps a session actually runs around
  llm and tool calls (same as chat and run-mas). Each LLM and tool chain
  (`for` / `per` / `max_attempts` on every step) and each agent's comm stack.
  A default retry is listed and marked `(default)` when the run does not
  list one.
  `--connections` / `--pipeline` print one view.
  `--json` is a stable document. `--infra-ref`, `--runtime-ref`, `--override`,
  and `--middleware` on `resolved` match chat and run-mas.
- Several `LLMProxy` refs stay separate leaves. The model name (and
  `models.mappings` aliases) selects which leaf provides the call —
  same shape as tool-name routing. Eval uses `experiment.models.judge`.
  Middleware plugins declare `per` (`all` or `destination`): `llm_cache` is
  one step on the live engine; scheduler and fault injection run on each leaf
  (`PipedLLMProvider` is that leaf call). `for: tool` `per: all` wraps the
  engine's tool call; destination tool steps wrap the tool server that
  claims the name (`PipedToolCall`). Callers keep `LiveLlmEngine`. Each
  leaf uses its own `api_key_env`.
  Cache lookup tries the routed key, then the pre-routing preview hash when
  a single leaf is configured; several leaves never reuse an old-key hit.
  Writes use the routed key only. See
  [Several LLMProxy refs](docs/manifests/infra.md#several-llmproxy-refs).
- `AgentCommContract.send` / `asend` take optional `headers`. Implementers
  must accept the argument. Only `traceparent`, `tracestate`, and `baggage`
  copy onto a later send; other keys stay on this send. A bad trace id
  starts a new trace. A too-long `tracestate` or `baggage` value is dropped
  so the send still goes through. `TraceContextLayer` writes a correlation
  id (not an OpenTelemetry span). A2A accepts `headers` and does not put
  them on HTTP. See [Communication headers](docs/references/comm-headers.md).
- You can stack `agent_comm` plugins: a link at the bottom, decorator
  layers above it. A stack of one is that layer. Starting the stack
  starts every layer once, bottom first. Plugins declare capability
  flags; a plugin that declares nothing is unknown and cannot claim
  isolation, end-to-end encryption, reliable delivery, or persistent
  groups. The stack turns flags on, never off. `SendContext` is
  immutable. The caller may set headers and intent; the link sets
  sender from its `agent_id`. See
  [Communication stack](docs/references/comm-stack.md).
- In-process group link: send reaches one peer, any instance of a
  service, or a room. The shared group is passed in from Python; session
  and config do not share one yet, so two links given only a name cannot
  talk. send keeps the three call ids; async send uses the async handler
  when it exists. stop removes the agent, deletes rooms it created, and
  refuses later sends. A callback takes the message; otherwise receive
  and drain empty the queue. A send with no handler raises. Creating a
  room someone else already made raises. If one queue is full, nobody
  gets the message. See
  [Communication stack](docs/references/comm-stack.md).
- Send tool: the model still sees only `delegate_to_<id>`, with the same
  `task` and `context_id` arguments. A Python `send` call uses the same
  path. The target of `delegate_to_b` comes from the tool name; extra
  `target` or `intent` arguments are rejected. With no peer list, nobody
  is reached. Unknown intent is an error. A user tool named `send` is
  left to that tool. Intent is `SendContext.intent`. `send` passes
  `headers=` and `context=` only when the plugin's `send` takes them.
  A session's `agent_comm` and `agent_comm_routes` replace the ones
  given at construction; routes replace whole. Wiring the entry engine
  passes `allowed_peers=` to the registered delegation plugin. Traces
  and the knowledge graph treat a handoff as `concept=agent_comm` or a
  `delegate_to_` name, not a user tool named `send` or a tool with a
  `target` argument. See
  [Communication stack](docs/references/comm-stack.md).
- Send intents: `inform` and `ask` still run a turn and the caller
  waits. `inform` then returns `delivered` and drops the reply; a peer
  error still raises. The in-process links label the text as an
  untrusted peer message. That label is a hint to the model only;
  sender and intent also sit on the `inbound_send()` seam and on
  local-group handler kwargs. A channel message is intent `publish`.
  An empty intent stays empty. A link that does not honour `inform`
  or `ask` (A2A is one) refuses them. `task` and `delegate_to_*` stay
  the same. See
  [Communication stack](docs/references/comm-stack.md).
- Send steer: `send(..., intent="steer")` calls `ControlContract.scoped`
  then `steer` on the session the link maps for that target, not the
  send `context_id`. The scoped handle is the intersection of the
  passed control and a peer token (actor is the sender, surface
  `peer`, only that session); it cannot gain a steer right the parent
  lacked. `steer()` does not take `sender` or `source`; those come
  from the capability. Mode defaults to `after` at the tail so a peer
  does not jump a human's pending input. Peer entries queue at the
  tail in every branch. `after` on a paused session waits in the queue;
  `preempt` and `replace` are refused. The kernel turns
  `OperatorSteerReceived` into context assembly for every pattern.
  `ControlContract.steer_modes` lists what the pattern accepts.
  Deterministic patterns accept `after` only; `preempt` and `replace`
  raise `ValueError`. If the pattern plugin cannot be read, only
  `after` is allowed. Idle `preempt` and `replace` from a peer queue
  at the tail. Who may be steered is a list; empty means nobody. The
  steer text is labelled as an untrusted peer message; sender and
  intent ride the symbol. A send through the send tool also needs the
  target on `allowed_peers`. A2A still refuses `steer`. See
  [Communication stack](docs/references/comm-stack.md).
- `mas-lab telemetry errors`, `conversation` and `calls` read a converted span
  file: count spans by status, print the conversation in order, and list the
  calls to named tools with their inputs.
- `mas-ctl chat --cache-read-only` (and `mas-ctl serve --cache-read-only`) replays recorded answers only: no LLM call, no cache write,
  no API key, and an immediate error on an unrecorded prompt. Without it, a live run still
  requires the key up front.
- `mas_ctl.cache: read-only` in `config.yaml` makes `--cache-read-only` the default for `mas-ctl chat`
  and `serve` in that workspace (an explicit `--cache-write` or `--no-cache-read` wins).
- `mas-ctl serve --listen NAME=a2a://host:port` serves an agent without an infra file, the
  mirror of `--connect` (`usage: deploy` instead of `usage: use`).
- Scope-based controller API (`/api/scopes`, `/api/kinds`,
  `/api/scopes/{scope_id}/items`, `/api/choices`, `/api/runs`,
  `/api/artifacts`, `/api/uploads`, `/api/info`)
  so the UI never sees a server path. Labs and libraries are distinct scopes;
  applications are versioned (`name@vN`).
- `mas-lab serve --cors-origin`, `--token-file`, `--state-dir`, `--workspace`, `--host`.
  `--workspace` (and `MAS_WORKSPACE_ROOT`) is the project folder for
  `config.yaml` and the writable Workspace library (`<workspace>/library`).
  An explicit `mas_lab.labs_search_paths` (including `[]`) is the exclusive
  catalog; without it, installed packages and cwd-walk stay in the list.
  Benchmark trees under `paths.labs_dir` appear on the matching experiment
  page.
- UI connection screen, Vitest/MSW/Playwright, and `yarn verify:api`.
- Default UI scope from `window.__MAS_LAB_DEFAULT_SCOPE__` (alias
  `__MAS_LAB_DEFAULT_LIBRARY__`), else the first scope that has items.
- Labs catalog at `/` and `/labs` lists every lab the controller discovered.
  `POST /api/scopes` creates a lab (optional local library) on the first
  `mas_lab.labs_search_paths` entry. New experiments are created inside the
  selected lab.
- Session conversation archive: every turn is a leaf; summaries point at
  children (nested ok). Assembly projects the covering set into chat
  `messages[]` — the model never sees chunk ids. Each call records
  `archive_view_id` on telemetry (`context.assembled`, processing-call
  spans). Inspect with `inspect_archive(session_id)`. Persist only when
  `mas_ctl.archive.enabled` is true (default false); a path or events file
  does not turn it on. Off disk forces deep checkpoints.
  `spec.checkpoint.portability` is `self_contained`/`deep` or
  `reference`/`shallow`. Reviewed history knobs:
  `pkg://mas.library.standard/overlays/production-memory.yaml`.
- Output-token limits are configurable through model and infra manifests.
  `on_truncation` supports `ignore`, `warn`, `error`, and bounded `escalate`;
  `--max-tokens N` aliases the agent models `--override` path. Requests omit
  both output-token fields by default, never send both together, and fit a
  configured budget to `context_window`. See
  [Output-token limits](docs/manifests/agent.md#output-token-limits).

### Changed

- Stop retrying inside the engine. Retry is a step around the call. When a
  run omits one, insert it (LLM 4 times, tool twice when the tool says it
  is safe to retry; `retry_on: [transient, unavailable]`).
- Syscalls are granted in `spec.system_plugins`, not `spec.tools`. A plugin loads
  when named, when its library row is `default_enabled`, or when its own rule over
  the spec grants it; `enabled: false` turns off all three.
- `--events*` is a shortcut on the run. `--no-events` and
  `--events-format` `native` / `otel` / `both` replace the plugin set.
  `--events` only forces recording; `--events-file` and `--events-stdout`
  name native's file and stream, so they add `native` when it is not
  selected. `bind.events` adds a sink and keeps native when no Control named
  the observability list (including `[]`) and no leftover agent named plugins.
  Precedence: those flags, a Control's
  `observability`, the agent's, Flavour `bind.events` rows, then native.
- `--no-control-dir` skips the live control endpoint (`mas-ctl control attach`).
  It is the pair of `--control-dir`, not of `--control`. `--no-control` is the same flag.
- Bench records native events without replacing the agent's plugins.
- The run hash includes Control bodies and `agents` selectors, resolved
  when the Flavour loads. Control observability sinks and leftover Flavour
  `observability` stay out; the `native` `categories` filter is in.
  Cache keys that include Control, or that used leftover bundled `local`
  / `local-benchmark` keys, change.
- Bind is one path for every entry point; it refuses secrets and ids a
  layer cannot name.
- A SLIM link reconnects after five read failures.
- A local-group send waits at most `reply_timeout_s`.
- A2A REST uses `application/a2a+json`; an unsupported type is 400.
- `mas-ctl infra resolved` prints each agent's comm column from its Flavour
  chain, the `protocol` of the row that reaches it, or the library default
  `agent_comm` plugin.
- Application rows: the schema requires `protocol` but no longer `url`,
  because an endpoint row with no address (A2A framing above a transport,
  an in-process channel) is valid; ctl still requires `url` on an
  agent-keyed row.
- `agent_comm/stack` runs on the generic `Stack`: every send carries a
  child W3C `traceparent`, and a layer error is an `AgentCommError`
  (`StackSendError`).
- The lab run hash leaves a Flavour's `bind` out.
- The interactive prompt (`You: `) is written only to a terminal, so piped
  input no longer prints `You: You: ...`. A request another agent sends to
  the entry agent is shown under the agent's label, not as the user's.
- Lint tasks and CI run `ruff format --check` next to `ruff check` on the
  Python a branch changes.
- load_yaml_file reuses a parse when the content hash matches; a
  same-size rewrite with a frozen mtime still reloads. ctl inlines
  overlay schema fragments once. validate_data on a sample mas.yaml
  went from 0.18 s to 0.006 s.
- A usable tool server whose protocol has no provider is an error. It
  used to bind the implicit local provider. A deploy-only server is
  not a client. The last explicit local tool server wins.
- A `delegate_to_` name with a blank target is not a handoff. The helper
  strips whitespace and returns nothing.
- `mas-lab benchmark publish-hf` and `import-hf` take `--token` or
  `--token-file`. If neither is set, ctl reads `HF_TOKEN`. `import-hf
  --dry-run` still uses `HF_TOKEN` when it is set, and does not require
  a token when it is not.

- Public GitHub and docs URLs moved from `outshift-open/mas-lab` to
  `ioc-project/mas-lab` (`https://github.com/ioc-project/mas-lab`,
  `https://ioc-project.github.io/mas-lab/`). On an existing clone:
  `git remote set-url origin git@github.com:ioc-project/mas-lab.git`.
- Docs name the former Observe and eXplain Platform as SCOPE
  (`https://ioc-project.github.io/scope/`). The ontology package is
  `ioc-scope-ontology` (`scope_ontology`); `norm` still comes from git.
- Schema `$id` values are vocabulary ids (`mas/v1#…`, `mas-lab/…`), not
  hosting URLs.
- Docs: [Open standards](docs/standards/index.md) covers OpenAI Chat
  Completions, MCP, A2A, Agent Skills, and OpenTelemetry (AGNTCY
  `ioa-observe-sdk`). ADRs are ordinary pages (terminology in
  [repo structure](docs/what-is/repo.md), the event stream in
  [event model](docs/references/event-model.md), UI identity in
  [Controller API](docs/cli/serve.md)).

- UI routes are `/libraries/:scope/…` and `/labs/:scope/…`. Create/edit flows
  use `POST/PUT /api/scopes/{id}/items`. Picker lists come from `/api/choices`.
  Opening the UI without a scope stays on the labs catalog instead of jumping
  into the first library. Create/copy from a lab prefers that lab and its
  local library; other writable libraries stay available.
- Canvas layout binds through Overlay `spec.target` and an empty `spec.patch`.
  The lab registry indexes layout overlays by that target and GET attaches
  them when both the app and the overlay exist, in any order.
  Canvas `x-*` keys live only on that layout overlay, never on spec overlays.
  Sample MAS snapshots keep canvas coordinates on those overlays.

### Deprecated

- `mas-ctl infra show` as a merged run: use `infra resolved`. `infra show REF` is
  the catalog entry. The old invocation still works and warns.
- Deprecate agent `spec.control.retry` and `.circuit_breaker`: they still
  apply, warn once, and become steps around the call. Prefer a `retry` /
  `circuit_breaker` step.
- Deprecate plugin types `infra_middleware`, `stack_layer`, and
  `circuit_breaker`, and the pipeline key `middleware:`: each still loads,
  warns once, and names `layer`.
- `{kind: system, name, params}` in `spec.tools`: list the plugin in `spec.system_plugins`.
  It grants the same plugin (`params` become its settings, `enabled: false` turns it off) and
  warns once; `mas-ctl validate` warns per entry. When both name one plugin, `spec.system_plugins`
  wins. The Agent schema marks the entry deprecated.
- The `spawn_subagent` plugin name (use `subagents`; its tool is `create_subagent`), the
  `system_tools=` argument of the host tool-provider loaders (use `system_plugins=`), and the
  `system_tool` plugin type (use `system_plugin`). Each still loads and warns once.
  Overlay `$op.remove` of `spawn_subagent` removes `subagents`. `mas-ctl validate` on an Overlay
  errors on an unknown plugin name in `$op.add` / `$op.remove`. A leftover `system_tool` row
  still receives its named settings and still wraps `hitl` / `user_io`.
- Passing a Control with `-o`, and listing Controls under an Overlay's `spec.control`, still work and warn
  once per run: attach Controls with `--control` or a Flavour. A scenario's `overlays.control` attaches
  Control files the same way `--control` does.
- The `trace_context` agent_comm layer: every stack now stamps the
  `traceparent`. It still works.
- `BoundLayer` decorators: write a `Layer` instead. A `BoundLayer` still
  works in `agent_comm/stack`: `adapt_legacy` makes it a `Layer`, as it does
  for an `agent_comm` link.
- `workflow.nodes[].delegates_to` is deprecated, still supported, no removal
  date. `mas-ctl validate`, `compose`, and `run-mas` print the warning once
  per compose. A second compose prints it again. `--no-validate` is silent.
  Empty lists do not warn. Overlay patches that set a non-empty list warn
  too.
- `workflow.nodes[].dispatch` (`parallel`, `all`) is deprecated, still
  accepted, and ignored: it never fanned out to a node's peers. Fan-out is
  the model's own choice of parallel tool calls. Validation and compose warn
  once, listing the nodes that set it. The two samples that set it no longer
  do.
- Infra `usage` on Application endpoints and ToolServerRegistry servers is
  now `connect`, `listen`, or `[connect, listen]`, the same words as the
  `--connect` and `--listen` flags. `use`, `deploy`, and `use-and-deploy` are
  deprecated aliases: still read, no removal date. `mas-ctl validate` warns
  per row and compose prints the warning on stderr. `--connect` and
  `--listen` now write the new values.
- Agent and MAS `spec.observability`, `spec.governance` and `spec.control` are
  deprecated and still applied: they constrain a run rather than define the agent, so
  they belong in a `kind: Control` a Flavour attaches. `mas-ctl validate` warns once per
  file. The shipped Tutorial 13 and governance examples moved to Control.
- Flavour leftover keys `agent_comm`, `tools`, `telemetry` and `observability`, and a
  leftover `control` mapping, are deprecated and ignored: the schema still accepts them
  and nothing reads them. Validation warns once, listing the keys that are set, for a
  Flavour file passed with `--flavour PATH` (`mas-ctl` and `mas-lab`), and in
  `mas-ctl validate` for a Flavour file or overlay; the bundled flavours stay
  quiet. `--no-validate` is silent.
- `mas-ctl flavour list` is deprecated: it still lists the bundled files and prints a
  warning on stderr. Pass `--flavour PATH` to a Flavour file, or a bundled name
  (`local`). The unknown-flavour error names those two routes.
- `--bind NAME=URI` is a hidden alias of `--connect`. It still works and prints
  a warning. It remains for one release.
- `/api/libraries/**`, `/api/jobs/**`, and `/api/experiments/**` (results tree).
  They remain for one release as adapters. `__MAS_LAB_DEFAULT_LIBRARY__` is an
  alias of `__MAS_LAB_DEFAULT_SCOPE__`.

### Removed

- Drop LLMProxy `spec.proxy.retry`. Retry is a step around the call. The
  schema accepted the field; the resolver never applied it.
- Drop `request_with_retries` and `arequest_with_retries` in
  `mas.library.standard.plugins.llm.http`, and the `plugins.reliability`
  package with `ThresholdCircuitBreaker`. A provider calls `request_once`;
  retry and the breaker are steps around the call.
- `node_dispatch` and `uses_parallel_dispatch` in
  `mas.runtime.boundary.delegation.policy`, and ctl's
  `fanout_workflow_delegates`: they read `workflow.nodes[].dispatch` and
  nothing outside their own tests called them. `dispatch` is deprecated and
  ignored.
- The `_make_runtime_handler` alias in `mas.ctl.cli.commands.serve`; use
  `mas.ctl.session.exposure.make_runtime_handler`.

### Breaking

- LLM calls no longer send `max_tokens: 2000` implicitly. Without a model,
  infra, or environment value, the server default applies. Set
  `spec.models[].max_tokens` to keep the previous cap. Provider-level cache
  entries recorded with the implicit 2000 no longer match.

### Changed

- `--connect NAME=URI` is the shortcut for a remote peer or tool. A bad
  `NAME=URI` value is a usage error (exit 2), not an uncaught exception.
- `working_memory_messages` is accepted and ignored. Live tool rows are kept
  until the token trimmer (model window minus completion reserve) needs space.
  The old default of 20 rows could drop an `activate_skill` body mid-protocol.
  Tools attach generic `chunk` metadata (`pin_policy`, `semantic`,
  `section_id`); the archive never sniffs `<skill_content>`. Runs that never
  hit the window assemble the same `messages[]` as before.

### Fixed

- After a dropped LLM connection, the next try opens a new HTTP client
  instead of reusing the dead keep-alive pool.
- A streamed LLM response is not retried once a chunk has already been shown.
- A2A still closes a finished task when the installed SDK takes the task
  object instead of the id.
- A non-ASCII API key (for example a pasted `…` placeholder) is rejected up front with a clear message
  instead of failing inside the HTTP client.
- Tutorial 1 no longer repeats the tools' descriptions in a `tool_usage` prompt (they are
  sent to the model with every request), and the sample `calc` tool describes itself
  generically instead of as a travel-fare helper.

- Activated skill content is no longer summarized or dropped by history
  compaction (agentskills.io Step 5). `summarising`, `sliding_window`, and
  `stack` keep each `pin_policy: never` instruction as its own `system`
  row; the summarizer sees a stub instead of the body, and the assembly
  trimmer never removes the row. This holds with `pin_activated: false`,
  which used to lose the body after the first compaction. With the default
  pin, the history copy is dropped from the request so the body is sent
  once. See
  [Skill content is never compacted](docs/manifests/summarization.md#skill-content-is-never-compacted).
- Overlay patches that target one agent of a MAS (`patch.agents.<id>`,
  `$entry`, `$not-entry`, `$all`, `$delegates`) are merged onto the loaded
  Agent YAML. An agency row stays `{id, ref}`. The MAS document is
  not a parking lot for Agent fields (no in-memory `_agent_patches` map,
  no leftover skills/tools/context on the row). Resolving `$op.add`
  against an empty row used to replace the agent's own skills; that drop
  is gone. A base MAS still cannot declare skills (or tools, context, …)
  on an agency row. `spec.models` remains the MAS-level agent-inherited
  attribute.
- `model: any` (or an omitted pin) no longer falls through to package
  `defaults.yaml` (`gpt-4o-mini`). Engine construction raises if nothing
  explicit remains: `spec.models`, MAS default, `experiment.models.main`,
  workspace `config.yaml` `defaults.model`, `--model`, or `MAS_CTL_MODEL`.
  A `scripted_response` agent does not need a pin; it uses SimulatedEngine.

## [0.2.0] - 2026-09-30 (release candidate)

### Added

#### IOA, MCP, and A2A first

- `library-ioa` is now the home for protocol-first integration work, with
  explicit MCP client/server bridge support, typed tool adaptation, and the
  `mas-mcp` CLI for serving and invoking tools.
- MCP support is treated as an infra concern rather than a rewrite of agent
  logic: the same agent manifest can be paired with distinct infra adapters for
  local execution or remote MCP tool access.
- A2A agent exposure and delegation are supported through the runtime and CLI,
  with `mas-ctl serve --protocol a2a` and the `a2a` client commands for card
  lookup and direct peer messaging.
- `library-skills` provides the agentskills.io-compatible skill layer, with
  swappable execution backends and progressive skill disclosure.

#### HITL and user communication

- First-class human approval gates and user communication contracts are now part
  of the runtime boundary, enabling structured prompts and requests without
  coupling agent logic to a specific UI implementation.
- Runtime and session plumbing now preserve explicit user-facing signal handling
  across tool calls and delegation boundaries, which is a key requirement for
  production-style conversational workflows.

#### Manifest and governance improvements

- `mas-ctl compile` can emit the fully-resolved agent or MAS spec after overlays
  and library substitutions are applied.
- Semantic validation and tighter overlay validation improve manifest safety and
  help catch invalid cross-field references before execution begins.
- Core governance and plugin wiring continue to harden around explicit registry
  declarations rather than implicit environment behavior.

#### Runtime and observability

- LLM traces and execution metadata remain central to the framework and are
  increasingly used to debug delegation, tool routing, and provider-level
  edge cases.
- The runtime continues to harden around provider compatibility, malformed tool
  arguments, and trace fidelity for long-running multi-agent runs.

#### Protocol compliance results

- Official MCP conformance results show `92.9%` of required checks passing for
  revision `2025-11-25` and `98.3%` for the newer, stricter stateless revision
  `2026-07-28`.
- Deterministic A2A testing across JSON-RPC, HTTP+JSON, and gRPC passed `99/99`
  executed requirements, for a `100%` executed-test pass rate. The official TCK
  maps `79.8%` of the specification; `25` requirements were not exercised
  because no corresponding TCK tests currently exist. This is a test coverage
  limitation, not a reported failure of those implementation areas.

#### Docker image and release artifacts

- The GHCR-backed release pipeline now builds and publishes both the UI and
  backend container images for tagged releases via
  `.github/workflows/docker.yml` and `.github/workflows/build-push-ghcr.yaml`.
- Release images are published under `ghcr.io/ioc-project/mas-lab/ui` and
  `ghcr.io/ioc-project/mas-lab/backend`, using a tag scheme derived from the
  release version and commit SHA. This keeps the Docker image in lockstep with
  the package release.
- The release documentation and image references were refreshed after the branch
  rebase so the release notes match the current upstream Docker workflow.

### Changed

- Protocol integration is treated as an infra-layer concern instead of a change
  to agent business logic.
- Runtime control flow continues to separate model selection, tool access, and
  governance policy so the same workflow can run locally or through a protocol
  adapter without rewriting the agent.
- Project tooling and validation now expect explicit registry and manifest
  declarations for optional capabilities, which makes the environment more
  deterministic and easier to audit.
- The release branch was rebased onto the current upstream `main`, and the
  release documentation and Docker release notes were reconciled to match the
  state of the branch at the time of the cut.

### Fixed

- Tool-call recovery, malformed tool handling, empty-result preservation, and
  history-trimming edge cases continue to be corrected in the runtime.
- Manifest resolution and relative-path safety around library refs and overlays
  are validated before execution.
- Runtime traces are now more reliable for delegations and session-scoped
  execution flows.

### Deprecated / Breaking

- `spec.llm` is no longer the preferred configuration surface; model config is
  expected to live in the explicit model-slot structure instead of the legacy
  top-level field.
- Implicit plugin availability is reduced in favor of explicit registry-driven
  registration, so the environment must declare what is required for a run.

## [0.1.0] - 2026-06-15

Initial public release.

### Added

- Declarative YAML agent and MAS specifications with overlays and runtime
  contracts.
- A stateful runtime with session/task tracking, observability, and governance
  hooks.
- `mas-ctl` commands for chat, validation, and MAS execution.
- Reproducible lab and benchmarking workflows, plus the standard library and
  sample assets.

### Changed

- Established the core contract-driven execution model and the multi-package
  architecture used by the project today.

### Fixed

- Early runtime issues and compatibility gaps were resolved as the initial OSS
  cut moved from prototype to reusable framework primitives.

---

[0.2.0]: https://github.com/ioc-project/mas-lab/releases/tag/v0.2.0
[0.1.0]: https://github.com/ioc-project/mas-lab/releases/tag/v0.1.0
