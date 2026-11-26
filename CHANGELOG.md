<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Changelog

All notable changes to MAS-Lab are documented here. This branch is the
v0.2 release-prep branch after rebasing onto the latest upstream main and
reconciling the release notes with the code and docs currently on that branch.

## Unreleased

### Changed

- The live-LLM slot scheduler (`llm_scheduler` infra middleware) now lives entirely in `library-standard`: its store and policy moved from `mas.runtime.engine.llm_scheduler` to `mas.library.standard.lib.llm_scheduler`, next to the plugin. The runtime holds no pacing code and no pacing names: the `MAS_LLM_RPM` family of variables is declared by the scheduler's library through `mas.runtime.envkeys.declare_lab_keys`, which `mas-ctl` and `mas-lab` call at start, and a stack without the middleware paces nothing. `mas.runtime.envkeys.LLM_SCHEDULER_KEYS` is now `LLM_KEYS` (model, cache and HTTP retry variables only). Update any import of the old module.

### Fixed

- A failed model call (`LLM request failed:`) or a failed send to a peer (`[send] agent … failed` / `not available on bus`) anywhere in a run's output fails the run, not only when that text is the whole output. A 401 or 403 stays non-retryable. A send that failed on an `llm_cache miss` does not.

### Added

- World events: a trigger's action can use `${experiment.id}`, `${scenario.id}`, `${item.id}`, `${run.id}`, `${run.attempt}` and `${run.key}`, so one action can name the run it fires for when runs share a service. See `docs/manifests/environment.md`.
- A run is complete only when the executor says so. An error, a timeout, a lost worker, or a hidden-in-output failure is incomplete, marked `error`, and scheduled again (auth, TLS, and a malformed spec are not). `Retry-After` lengthens the wait. A batch pipeline stage does not start on an incomplete run; a resumed batch re-runs the ones left in error. Live stages still receive whatever arrives. See `docs/manifests/completeness.md`.
- Pipeline stages declare `mode: batch`, `live`, or `mixed`. A live stage feeds a batch stage only through a completeness gate. See `docs/manifests/completeness.md`.
- The pipeline is a block next to generator, scheduler, executor, retrier and analyzer. A step may call `schedule_batch`, `batch_status`, `stop_batch`, and `next_runs`, bounded by `max_scheduled_batches` (one budget per pipeline run). A batch runs in the controller when one is up, otherwise as a child `mas-lab benchmark run`. Shipped steps: `schedule_experiment`, `await_batch`, `next_runs`, `propose_prompts`, and `inject_experiment` (model-chosen item ids are made safe and unique; the design stays whole and a run-plan file narrows the next batch). `catalog()` is the typed data a UI needs; there is no UI code in this tree. See `docs/manifests/pipeline-assembly.md`.
- Sample lab `labs/factorial-halving.lab`: `pipeline run` plans the experiment, halves it by scores and writes a run plan the next round runs with `benchmark run --run-plan`.
- `retries: 0` on the retry layer is one attempt. A refused call is a classified failure. `llm_call_end` records `retry_count` and `error_class`. The `llm_scheduler` layer lowers in-flight on a refusal and raises it while calls stay clean.
- Experiment `design.plugin` names a `scenario_generator` (builtins `cartesian`, `coupled`, `one_factor`, `factorial` in `library-lab`). A lab-local library can add another. `design.mode` and the old top-level generator keys still load and are rewritten to `plugin` + `params`. See `docs/manifests/experiment.md` and `docs/manifests/scenario-generator.md`.
- Experiment `schedule.plugin` names a `scheduler` (builtins `coverage`, `depth`, `as_generated`). Omit it and the generator's default is used. `schedule.ordering` and `schedule.strategy` still load and are rewritten to `plugin`. See `docs/manifests/experiment.md` and `docs/manifests/scheduler.md`.
- Experiment `executor.plugin` names an `executor` (builtin `native`): it runs a run once. Timeout, workers, and the application runner live there. The batch host still owns cache, resume, and `result.json`. Keys still on `schedule` load and are rewritten. See `docs/manifests/experiment.md` and `docs/manifests/executor.md`.
- Experiment `retry.plugin` names a `retrier` (builtin `native`): a pure `decide(history)` that says whether a run runs again and after how long, used between attempts in one process and between CI rounds. `max_attempts` and `retry_backoff_s` moved from `executor` / `schedule` / `execution` to `retry.params` (old places load with a deprecation warning). See `docs/manifests/retrier.md`.
- Experiment `analyzer.plugin` names an `analyzer` (builtin `native`, a completeness summary) that reads a results tree and never runs a run. See `docs/manifests/analyzer.md`.
- Generation, scheduling, execution, retry and analysis exchange two files: a run plan and a results tree (each run folder carries its own `result.json` with `attempts`). `mas-lab benchmark runs plan | retry | merge | summary` and `benchmark run --run-plan` run each step on its own, and `.github/workflows/lab-experiment.yml` runs the shards as a matrix on separate runners with automatic retry rounds. See `docs/manifests/ci-sharding.md`.
- `--strategy` on `benchmark run` is removed; use `--scheduler`.
- Identity contract (`identity-contract/0`, provisional) in `library-standard` (`lib/identity`): a record builds a wallet, a holder of identities, that lists them (`identities`), selects one for an audience and task (`select`) and shows it (`present`, phases `initiate`, `challenge` and `sign`). Optional `Verifier`, `Describer`, `Revoker`, `Handshaker` and `Delegator` (derive a narrower identity into the wallet; a `Grant` only narrows). A secret param is a `SecretRef` revealed only inside the call that uses it. The contract is types and Protocols only; `rules` checks what a scheme returns, the plugin type `identity_scheme` has the explicit `none` scheme, and `testing.identity_conformance.SchemeConformance` is the conformance kit with fakes and a scripted double. A harness is bound to it in a later change; absent, nothing changes. See `docs/references/identity.md`.
- The identity layer and its caller put the contract on the protocol edge. A `Caller` is the one door to a wallet: a scheme that fails is `unavailable` with no text, every scheme call is bounded (`unavailable:timeout`), output is checked, evidence past its end is `expired`, and a `revoked` from any wallet operation revokes the identity, drops its cache and closes the sessions opened with it. The `identity` layer (a `layer` plugin) presents on MCP, LLM and A2A calls, answers one 401 or A2A challenge, signs on `phase="sign"`, runs the session handshake, and verifies inbound callers before a handler runs; `scope: process` shares one wallet across layers. Events are a small closed set (`identity.present`, `.handshake`, `.verify`, `.challenge`, `.revoke`) with no value in them. Stack hooks: `SessionHook`, `AuthorizableTransport`, `PeerChallenge`, `Unauthenticated`, and `SendContext.metadata`, `session_hook` and `message_id`. See `docs/references/identity.md`.
- Identity is infra, bound by `mas-ctl`. An `Identity` infra document holds records (a scheme and its params; a secret param is a `{secret: NAME}` reference, never a value), and an `identity` layer on a tool-server stack, an LLM stack or an agent chain names one: the Flavour that binds the stack gives the layer the record's scheme and params and the agent's id. Agent, MAS and Overlay manifests never name an identity (`mas-ctl validate` refuses `identity`, `identities`, `credential`, `credentials` under `spec`). A record whose secret is not set stops the start with one `identity.startup` event (`refused:missing_secret`, no value). `mas-ctl identity list | check | describe` read the records as a session would (`check --require-gate` makes an agent that holds a record without an `identity-gate` an error). `identity-gate` is a governance plugin that refuses a tool call before it starts from the public view of the wallet the agent holds (`no_identity`, `credential_revoked`, `scope_uncovered:<name>`, `gate_stale`, `gate_error`; `interaction_required` holds), in a small requirement grammar (`parse_requirements`, `GateConfigError`), with `identity.gate` events and no fact in them. `signed_badge` is registered as a reference double for tests and tutorials. See `docs/references/identity.md`, `docs/manifests/infra.md` and Tutorial 16.
- The agent can manage its own identities. `Surface` (`lib/identity/surface.py`) is a plain facade over the wallets the identity layer registered for it: list, describe, delegate a narrower identity (the caller checks that it narrows) and revoke, all value-free. The `identity` system plugin, declared only, gives the model four tools over it (`identity_list`, `identity_describe`, `identity_delegate`, `identity_revoke`) that work on ids and never return a credential; an argument the schema does not name is refused. `identity-guard`, a governance plugin, blocks `identity_delegate` and `identity_revoke` unless its `allow` policy opens them (`true`, or `hold` for a human), and publishes an `identity.gate` event for each decision. Tutorial 16 step 5. See `docs/references/identity.md`.
- `mas-ctl secrets migrate [--write] FILE...` rewrites the removed `*_env` keys and `env:` strings of YAML files into SecretRefs and plain values, keeping a `.bak` copy and printing the `secrets set` command for each secret. `mas-lab init` finds files with those forms in its config and offers the rewrite, and the load error names the command.
- Messages may be a string or a list of text and media parts (image and PDF file, keyed by sha256). Bytes live in a host media store. Media is accepted on user turns only. Audio, video and non-PDF files are refused before a provider is called. One shared helper maps them on every protocol layer: chat-completions `image_url` / `file`, Open Responses `input_image` / `input_file`, Anthropic `image` / `document`, Gemini and Vertex `inline_data`, Bedrock Converse `image` / `document`. Dataset items accept `{parts:}` with `{text}`, `{image:{ref}}` and `{file:{ref}}` (pdf); a turn with only text loads as a string. The live engine and the HTTP protocol layer gate media against the catalog before a provider is called. A missing digest is `MediaMissing`. The directory store reads only a 64-character hex digest under its root. Text-only cache keys, run hashes and previews stay the same. See `docs/references/multimodal.md`.
- LLM model catalog entries may list `modalities.input` / `modalities.output` (`text`, `image`, `audio`, `video`, `pdf`). `ModelCatalog.accepts_modality` is true for an unknown model and for a known model that omits the field; otherwise it is membership in that list. Audio, video and non-PDF files are still refused. `scripts/sync_model_catalog.py` refreshes context, output, modalities and pricing from models.dev without rewriting hand-written settings; it unions modalities with the catalog list and does not write an empty list over a missing field. models.dev is listed as a third-party reference (not a protocol) on the Open standards page; `NOTICE` keeps the MIT notice. See `docs/manifests/llm-model-catalog.md`.
||||||| parent of 52edb50d (feat(lab): run-wide overrides for every run of an experiment)
- `experiment.overrides` lists `PATH=VALUE` assignments (`.mas`, `.agent`, `.infra`, the `mas-ctl run-mas --override` grammar) that every run of every scenario applies; `mas-lab benchmark run --override` with such a path adds to it, where it used to fail on the root. A lab can now carry an endpoint setting, a model or a request field for all its runs.
- A dataset item can list the `scenarios` it applies to (one id or a non-empty list; an undeclared id is an error); the planner skips it in the other scenarios (no run and no run folder), including a coupling that names the item in a scenario it does not list, and reports `n items skipped by scenario` in the dry run and at the start of a run. Omitted means every scenario. See `docs/manifests/dataset.md`.
- Native `transport.*` events for protocol-boundary telemetry (A2A, MCP, SLIM): typed hooks, optional SDK-span intake with per-protocol classifiers, native KG handlers for the facts SCOPE's norm already maps (`message.*`, agent card, tools discovered), and observe-sdk aliases on the otel sink so SCOPE sees the same A2A/MCP/SLIM records as other apps. Capture is `native.transport`; projection (`none`/`contract`) and aliases are `otel.transport`. Listing native turns capture on (hooks on, internals off); listing otel turns aliases on. `--transport-observability` flips a protocol. `propagate_metadata` (session id and sender name in A2A message metadata) is off unless turned on. See `docs/references/transport-observability.md`.
- A governance plugin can check a HITL answer before an ALLOW takes effect: the optional `evaluate_hitl_resolve(request, resolve)` may return `BLOCK` (an exception also blocks; first deny in the chain wins), so an approval that did not verify never starts the call; an approval covers only its own call unless `hitl_once_per_turn` is set. `spec.governance.hitl_responder` hands a mapping (SecretRef objects for secrets) to the HITL responder `hitl_mode` names. A new user input clears every turn-scoped field of the kernel state (HITL block, skip and turn-wide approval, governance retry count, CoT pass) for every design pattern; only some patterns did, and the retry count was never cleared. Each field of `QProduct` now declares its scope. A run with no operator now refuses a hold unless the spec sets `hitl_mode: auto-approve` (it used to approve). See `docs/manifests/governance.md`.
- An agent loop seam: `spec.loop` names the plugin that runs an agent's tasks. Absent, the kernel main loop runs them exactly as before (a recorded two-turn run is compared byte for byte in a test). Present, a task goes to an `agent_loop` plugin, so MAS Lab can front another team's agent harness or run it in a lab experiment, keeping the A2A listener, the Controls and the events at the boundary. Two loops ship: `stdio-harness` (an allow-listed absolute command, no shell, an explicit environment, deadlines and size caps, line or JSON-RPC framing) and `a2a-harness` (a remote A2A agent through an `agent_comm` link). Controls judge each task as a `loop:<id>` tool call at the boundary; a third-party harness has no per-tool control. Honoured by `mas-ctl serve` and the lab runner for a standalone agent. The native `execution_start` event carries `loop` when one other than the kernel loop took the turn. See `docs/references/agent-loop.md`.
- A production hardening profile. `spec.hardening.profile: production` in a Flavour makes a session refuse to start when a named check fails: a listener that verifies no caller, TLS checks off, plain http to a remote host, HITL auto-approve, a replaying LLM cache, tool retry that is not idempotent, a wildcard MCP tool list, a message cap over the shared limit, no ingress governance. `waive: [{check, reason}]` accepts one failure and the report lists it. Omitted, nothing runs and no session changes. `mas-ctl hardening list` and `mas-ctl hardening check MANIFEST [--profile production] [--json]` list and run the checks (advisory by default), `mas-ctl validate --profile production` runs them over files, and the `gov_hardening_profile` governance plugin records the report at session start and blocks a tool call to a host outside `allowed_hosts`. An A2A row can verify callers with `a2a.admission.callers`, and a conversation is then keyed by (caller, `contextId`): before, a second caller that knew a `contextId` joined the first caller's conversation. Text a peer or server sent is cleaned (control characters dropped, 256 characters at most) wherever an A2A, SLIM, HTTP or MCP error or log line quotes it. The bundled `prod` Flavour selects the production posture and attaches the `with-hardened` Control to every agent; `local` and the default are unchanged, and a deployment overrides single checks with `waive`, reported in the admission log. See `docs/references/hardening.md`.
- `FigureStep`, the base class of every plot step: shared `theme` (`dark` for live HTML, `light` for file formats), `palette` (`pastel` for live HTML, `okabe-ito` for print), `format`, `labels`, `title`, `subtitle`, `xlabel` and `ylabel`, and `caption`. Each step declares the keys it implements; setting another one logs a warning. Series names come from `legend:` — a `$path` binding into scenario or item metadata, or free text.
- Pipeline context: a step config value that is `$path` binds into experiment / scenario / test / run / artefact / row when the step runs (`title: $experiment.name`, `legend: $scenario.metadata.legend`). Anything else is free text; `$$foo` is a literal `$foo`. The same standalone pipeline file works across experiments. `gather_level` copies missing scope ids onto concatenated frames so later plots can still bind `$scenario.*`.
- Step-level `realtime:` on a pipeline step: `true` folds `on_batch` as records arrive; `false` waits until every input has arrived. It is not a figure `config` key.
- Environment around a run: `environment:` on the experiment, scenario or dataset item (clock, timers, triggers, fixtures). A dataset item overlays timers and triggers on that world; it cannot declare fixtures. Complements `memory_seeds` and `tool_fixtures`; it does not replace them. World actions `http_call`, `clock_advance`, `sleep`, `shell`, `emit`, `fixture_restart`, `fixture_stop`. Control actions `steer`, `inject`, `pause`, `resume`, `checkpoint`, `kill_switch`, `hitl_answer` (`hitl_answer` queues a user turn; it does not resolve a HITL hold). `kill_switch` cancels the in-flight decode and persists with `auto_stop`. Live `threshold_alarm` (quiet in a trigger `do:` list). Artefact `environment` (`traces/environment.jsonl`, stream `custom.environment`, plot `timeline`). Governance observer `environment@v1` always ALLOWs; with no bound port it does not subscribe to transitions. A leftover pidfile is reaped only when its digest still matches; stop, url and clock stay on this run's keys; `start_once` holds a ref. `clock.start` durations compile; unknown `do` types are refused; an empty session id is nobody. Fixture and shell processes drop `MYSQL_PWD` and password URLs without a user. Workers also refuse named actions and recorded replay. A fixture named `scripted-model` is bound as LLMProxy `openai-direct` for that run. Fixtures with the same name at different run keys are separate processes; a command interpolates this run's urls. A scenario can wait on another scenario's checkpoint (`depends_on`, `produce_checkpoints`, `resume_from`). See `docs/manifests/environment.md`.
- Demo UI timeline lane for environment events: wall-time fixture bars, trigger diamonds, and action spans on `custom.environment`. Layout `run-view-environment.yaml`.
- A2A over SLIM is the A2A SDK on the `slimrpc` protocol binding (`slima2a`): the `a2a-rpc` layer, a send layer over `slim`, replaces the `a2a-framing` layer, which sent MAS Lab's own JSON-RPC and so needed a MAS Lab peer on both ends. A peer that speaks A2A over slimrpc can now be reached and can reach a MAS Lab agent. An agent is served by the same SDK request handler as the `a2a` HTTP exposure. A row may name a stack top first: `protocol: a2a+slim` is the chain `[slim, a2a-rpc]`, with the row's node fields as the bottom layer's params and card fields on `a2a-rpc`. `protocol: a2a+grpc` stays the A2A plugin (the gRPC extra), not a stack. `a2a` over HTTP is the `a2a` link of one url, as before; `[http, a2a-framing]` is gone. The `slim` extra moves to `slim-bindings>=3.3.6,<4` and `slima2a==0.9.3`, which holds `a2a-sdk` at 1.2.3 on that extra. The slim link can hand its inbound sessions to a layer above it (`claim_inbound`) when that layer serves a turn; a connect-only agent starts no RPC server. Hop-safe headers ride A2A message metadata and bind on the served turn. A send after the node dropped waits for the reconnect, and the RPC server restarts on it; so it does when the server stops on its own. A send is never repeated after it may have reached the peer. A served turn's sender is the caller the node verified, and the server emits the same received, completed and error events as the slim inbound path.
- A2A over HTTP sends the per-call headers (`traceparent`, `tracestate`, `baggage`) on the JSON-RPC request through the SDK's per-call context, over the row's static `headers` and the hop-safe ambient headers of the sending turn; `delegate_to_*` carries those ambient keys. A framing header such as `host` raises `AgentCommError`. They were dropped before.
- Prompt cache fields on a model row: `prompt_cache_retention` (`in_memory`, `5m`, `24h`, `1h`) and `prompt_cache_breakpoints` (`false`, or `{system, tools, messages}`) next to `prompt_cache_key`. Anthropic and Bedrock mark the system prompt, the last tool and the last user turns (Anthropic by default; Bedrock when the catalog lists the model or the spec sets it); Gemini and Vertex send a `cachedContents/...` key as `cachedContent`; openai, azure, OpenRouter and LiteLLM forward key and retention while hosts that reject unknown fields drop them. Replies report `cached_tokens`, `cache_write_tokens` and `reasoning_tokens` in `usage` for every protocol. See `docs/manifests/llm-prompt-cache.md`.
- Anthropic, Bedrock and Gemini send `temperature` (Anthropic and Bedrock also `top_p` and `top_k`) only when the spec sets it and the model catalog does not mark it unsupported. The catalog gains `thinking_mode` (`none`, `budget`, `level`, `adaptive`, `always_on`), resolves hosted ids (`us.anthropic.claude-…-v1:0`, `…@20260101`) to their model, and has Claude and Gemini 3 entries with context, output limits, sampling support and pricing (figures are curated and to be re-verified).
- `protocol: openresponses` posts [Open Responses](https://www.openresponses.org/) `/responses` through http: typed input items, stateless (`store: false`), `instructions`, flat function tools, `text.format` for structured output, `reasoning` effort, mode and summary, and a reasoning item's `encrypted_content` kept in `provider_state` and replayed with its tool call. Prompt cache key and retention, `cached_tokens` and `reasoning_tokens` follow the same names. Bundled infra: `standard:openresponses`. See `docs/standards/openresponses.md`.
- Every LLM protocol layer lists its `features` in `library.yaml`, and `docs/manifests/llm-providers.md` gives the provider by feature matrix and the field mapping per provider.
- Anthropic, Gemini, Azure, Bedrock, and Vertex ride http the way openai does: `protocol: anthropic` plus a url is `[http, anthropic]`, and the same sugar applies to `gemini`, `azure`, `bedrock`, and `vertex`. Groq, OpenRouter, xAI, LiteLLM, Cerebras, Mistral, vLLM, and Hugging Face inherit the openai chat-completions layer and only change the host and key name (`protocol: groq` is `[http, groq]`). Databricks posts that same body at `/serving-endpoints/{name}/invocations`. Each protocol layer posts through the http transport (plain httpx, no vendor SDK). Azure classic posts `/openai/deployments/{name}/chat/completions` with `api-key`; a host that already ends in `/v1` keeps `/chat/completions`; `proxy.auth: bearer` sends an Entra access token instead. Bedrock posts Converse with a bearer token. Vertex posts generateContent at the project path with a bearer token. The layer reveals `api_key` (a SecretRef) at the request and does not read the environment. Bundled infra: `standard:groq`, `standard:openrouter`, `standard:xai`, `standard:azure`, `standard:bedrock`, `standard:vertex`, `standard:litellm`, `standard:cerebras`, `standard:mistral`, `standard:vllm`, `standard:huggingface`, `standard:databricks`.
- A `structured` wrap layer asks a model for schema-conforming JSON and returns a validated `parsed` object on the existing provider result path. A tool-call turn is left alone. Flavour `bind.llm` `{stack: NAME}` attaches it; omit it and the call is unchanged.
- Thinking on Anthropic, Bedrock, Gemini and Vertex: the `reasoning` block becomes the vendor's thinking request from the catalog `thinking_mode` (`thinking: adaptive` plus `output_config.effort`, a budgeted `thinking`, `thinkingBudget`, or `thinkingLevel`). Signed thinking blocks and Gemini thought signatures of a tool turn travel in a new opaque `provider_state` on the completion, the engine return, the log row (checkpoints keep it) and the assistant tool-call message, and the same layer sends them back with the tool results; a loop that lost them runs with thinking off instead of failing. `provider_state` stays out of observability events and token counts. A failed tool result carries `is_error`, sent as `is_error` (Anthropic), `status: error` (Bedrock) or `{error}` (Gemini). See `docs/manifests/llm-reasoning.md`.
- A server `Retry-After` (`retry-after-ms`, `retry-after`, or a Google `RetryInfo` delay) lengthens the wait before the next try; a hint above `max_retry_after_s` (default 60) ends the retries. `honor_retry_after` and `max_retry_after_s` join the retry policy. A prompt too long for the model fails once with code `CONTEXT_LENGTH_EXCEEDED` and is never retried.
- CI: `.github/python-support.json` lists the supported Pythons (3.11 to 3.14). Pull requests run one version, 3.14 (the default); the old daily `nightly.yml` is replaced by a weekly protocol-conformance run (`weekly.yml`); every version runs weekly and before a release (`python-matrix.yml`), and a failure blocks the PyPI upload. A free-threaded 3.14t job runs the components that install on it, with the GIL forced off, and reports without blocking. `setup-mas` pins and checks the interpreter. `library-kg` and `library-telemetry` now declare `requires-python >=3.11` like every other package. See `docs/python-support.md`.
- CI: `docker-check.yml` builds the UI and backend images from scratch, starts the stack and checks that the API and the UI answer (`task docker-smoke`). It runs weekly (with a scan for fixable critical vulnerabilities, and one tracking issue on failure), on a pull request that changes `docker/`, and before a release publishes the images, which it blocks on failure. It pushes nothing. It found two faults in the backend image: the build failed because the root package's `README.md` was not copied (since the meta-package, #215), and every new container ran `uv run`, which installed the dev dependency group again at start. The image now copies the file and starts with `uv run --no-sync`. It also installs from `uv.lock` (`uv sync --locked`), the UI image pins Yarn 4 in `ui/package.json` (`packageManager`) so Corepack no longer gives Yarn 1, which ignored the Yarn 4 lock file, a `.dockerignore` keeps environments, caches and `.env` files out of the build context, and `ui/yarn.lock` takes the one entry (the `resolve` compat patch hash) that Yarn 4.18.1 writes differently, so `yarn install --immutable` passes.
- KG: `categories` selects the opt-in classes of the native fold. The default output holds the core classes only (`Session`, `MASCall`, `AgentCall`, `LLMCall`, `ToolCall`, `ProcessingCall`, `State`, `Transition` and the `MAS` / `Agent` / `LLM` / `Tool` / `Processing` catalog). `capabilities` adds `Skill`, `SkillCall`, `Memory`, `MemoryCall` and `RAGQuery`; `annotations` adds `CallAnnotation`, `GovernanceEvent` and `ContextContribution` with their `annotates` edges; `worker` adds `Worker` and `runsOn`; `thinking` adds `ThinkingCall` and `hasThinkingCall`, mapped from `thinking_start` / `thinking_end` (`call_id` `<llm_call_id>#think`, `parent_call_id` the LLM call, States from `state_in` / `state_out`). `all` enables every category. A list or a comma-separated string; an unknown name is an error. Events of a disabled category are dropped by class, in the batch and the realtime fold alike, so the core graph is the same with or without them. `usesMemory` and `usesSkill` link an agent to the `Memory` and `Skill` it calls, as `norm` does. `norm` has no category gate: it maps every span it is given, so on the OTel path the gate is the observe-sdk's per-category switches.
- Telemetry: the `observe_sdk` profile copies the native explicit dataflow onto the spans as `ioa_observe.state.input.id`, `ioa_observe.state.input.content`, `ioa_observe.state.output.id` and `ioa_observe.state.output.content`, on agent, chat, tool and processing spans and on `session.end`. A join input id is the JSON list of its member ids. Nothing is inferred; a missing field gives no attribute.
- Thinking is emitted as it happens: the runtime reports the reasoning interval of an LLM call through `record_llm_thinking` (`ObsEventKind.LLM_THINKING`). The start is the first reasoning delta (`reasoning`, `reasoning_content`, `thinking`, `reasoning_details`, or a `<think>` block), the end the first answer or tool-call delta (or a closing tag, or the end of the stream), each with the time it arrived. A response that returns its reasoning only in the final message is a zero-length interval at the time it was received; a reasoning token count alone is no interval. Native `thinking_start` / `thinking_end`: `call_id` `<llm_call_id>#think`, `parent_call_id` the LLM call, `state_in` / `state_in_content` the LLM call's prompt State, `state_out` `<llm_call_id>#think#0`, `state_out_content` (end) the reasoning text. The converter maps the pair to a `{agent}.thinking` span under the `*.chat` span.
- `llm_call_start` carries `reasoning_effort`; the `*.chat` span carries `gen_ai.request.reasoning_effort` and `gen_ai.usage.reasoning_tokens` (from `completion_tokens_details.reasoning_tokens`).
- Telemetry realtime: the converter builds each runtime event with the ioa-observe SDK's own `RuntimeEvent` (validated against the SDK's required attributes, `snapshot.version` counted per session, `event.id` from `identify_runtime_event`, severity INFO) and needs `ioa-observe-sdk>=1.1.2`. `JSONLineFileLogExporter` implements `force_flush`, which current OpenTelemetry requires.
- Telemetry realtime: the runtime events are OTel LogRecords shaped as the ioa-observe SDK emits them (`topology.node.*`, `tool.*`, `llm.*`, with `event.id`, `agent.name`, `tool.name` / `tool.type`, `llm.name` / `llm.call.id`, `span.id`, `trace.id`, `ioa_observe.agent.span_id`, ...), written to `<spans stem>_runtime_events.jsonl` and to OTLP `/v1/logs`. A SCOPE norm that reads realtime events can fold them; the released one does not yet.
- Native memory calls name their store (`memory_name`, `memory_type`): a MEMORY_OP that names none uses the issuing agent's working memory, `<agent_id>.working_memory`.
- Native `events.jsonl` records carry explicit dataflow State identity: `state_in` / `state_out` ids (`{exec_call_id}#{n}`, per visit) and `state_in_content` / `state_out_content` on LLM, tool, memory and context-assembly calls, agent start and end, and parallel group joins. Additive; nothing reads them yet.
- A `health_probe` layer checks ahead of the model's next call that what the spec marks probeable still answers: a tool with a `probe`, a peer with `probe: true` in `spec.communication.peers` (an A2A link fetches the remote agent card afresh). A failing target is taken out of the running spec and put back when its probe passes. It acts where the circuit breaker only reacts.
- Write a new tool as `async def on_execute_tool` (or `async def execute`). That one method is enough; the engine awaits it. A `def on_execute_tool` still works: the engine may run it in a worker thread. `on_aexecute_tool` still wins when present. The sync path finishes an async tool instead of returning a coroutine. Calling `call_tool` from a running loop raises; use `acall_tool`.
- `gov_no_undeclared_peer` BLOCKs leftover `delegate_to_*` to a dest not in this agent's `delegates_to`. A user tool named `send` or a declared `delegate_to_<x>` tool is left alone. `with-hardened` names it. Send itself does not refuse.
- openai and mcp ride http: `protocol: openai` plus a url is `[http, openai]`;
  `protocol: mcp` plus a url is `[http, mcp]`. The openai plugin is the
  chat-completions layer; the MCP client is the tool layer. The provider the
  engine sees sits on top.
- A `kind: Stack` infra row names those layers. Flavour `bind.llm` as `{stack: NAME}`
  puts that stack on llm calls the way `bind.agent_comm` puts send on a chain.
  `bind.tools` is wrap layers (retry, impair) on tool calls; `http` and `mcp`
  belong on the tool-server row. `bind.agents.<id>.llm` /
  `bind.agents.<id>.tools` pick that agent's stack.
- Provider cache is the `llm_cache` layer around openai, not a second
  chat-keyed wrapper.
- `mas-ctl infra show` prints the sugar as the real steps. `--connect NAME=mcp://...`
  is unchanged.

- KG: the OTel path runs on the released SCOPE norm and ontology and still completes and aligns its graph (`otel_completion`, `align_graph`); the State synthesis it needs is `core/otel_state_synthesis.py`. The realtime runtime events are exported but not folded into the KG until SCOPE reads them.
- KG: the OTel pipelines are one `normalize_otel` step; `mas-lab kg otel-to-kg` keeps `--run-id`, `--output-dir` and `--dry-run`.
- KG: `CONTAINMENT_EDGE_TYPES` moves to `core/compare.py`.
- KG: both folds name calls alike: agent id, model name, tool name, processing name, skill name, memory name; an annotation is named by its kind.
- KG: `compare_trajectories(events, categories, realtime)` replaces `compare_native_and_otel`. With a SCOPE that has the new changes, native fold and norm are identical on the delegation, categories, trip-planner and (realtime) cut-off fixtures.
- Telemetry: governance and checkpoint brackets are ProcessingCalls with `parent_call_id`, `checked_call_id` and the State they read.
- Telemetry: `session.end` is written only on `mas_call_end` and carries `execution.success`; a cut-off session stays open.
- Telemetry realtime: `topology.session.started` / `.completed` for the session; every `*.started` record carries its span's attributes.
- Telemetry: an annotation is one point span whose `mas.annotation.content` is the record's payload; `user_response` is not exported; no defaulted memory type, durable backend or context source.

### Changed

- Trip-planner agents pin `model: any` instead of a hosted model id.
- `model: any` inherits infra `models.defaults.llm`, then `mappings.default`, before workspace `config.yaml`.
- `mas-ctl run-mas --model ID` remaps every agent for that run.
- A part list is shown and hashed as text or JSON, not as a Python repr.
- A SLIM create-app failure is `slim binding error` and does not quote the bindings.
- Keep one append handle per `events.jsonl` and stream log; emit or publish after close raises. See `docs/references/lab-throughput.md`.
- Serialize a stream event once; `follow_worker` fetches only new log bytes; `reset_channel` restores the previous log. See `docs/references/lab-throughput.md`.
- Cache parsed YAML by path and content hash for a lab batch, and memoize installed library roots. See `docs/references/lab-throughput.md`.
- Run each MAS in a spawn child (`executor.worker_processes`); a step must declare `worker_safe`; a dead worker or a duplicate `job_id` is one error row. See `docs/references/lab-throughput.md`.
- Put A2A gRPC in extra `a2a-grpc` of `mas-lab-library-ioa`; `a2a.grpc_port` is served only by `mas-ctl serve`; importing the runtime, the lab, and the default A2A plugin does not import `grpc`.
- `service_start` and `service_stop` share one process manager, so a started service actually stops. `keep_running: true` leaves it up after the level ends.
- Ingress TOOL_RESULT now carries `tool_name`, so an environment trigger can match the tool.

- The runtime is async end to end: the driver, engines, call layers, LLM providers, context assembly, tool dispatch, send, delegation, spawn, the inbound serve chain and the session turn are awaited on one event loop. Write new plugins as `async def` (`achat_completion`, `asend`, `ainvoke`, `acall`, `on_execute_tool`); see [Writing plugins](docs/references/async-plugins.md).
- Sync plugins keep working. A plugin that only has the sync method (`chat_completion`, `send`, `invoke`, `call`, a `def` tool or hook) runs on worker threads, and the runtime logs one warning per class naming the async method to write. `engine.sync_threads` in the RuntimeEngine manifest sets how many threads they share (default 1).
- A non-async caller uses `SessionController.run_turn`, `feed` or `submit_hitl`; each runs the async implementation on its own loop and raises inside a running loop. The helper loop thread of the sync tool path is gone.
- The HTTP layer and the A2A client keep one async client per event loop, so connections are reused across sends. The A2A client also keeps the agent card instead of fetching it on every send. `A2AClient.asend_message` and `aget_agent_card` replace the sync methods.
- A per-agent turn lock, `TurnGuard`, is taken with `async with guard.ahold()` and is re-entrant along one call chain. `ChainHandles.turn_lock` returns one.
- The test helpers `check_layer`, `check_transport` and `check_agent_comm` are coroutines; `check_agent_comm_async` is folded into `check_agent_comm`.
- An agent carries its own view of the MAS. mas-ctl compiles the MAS manifest into each agent's `spec.communication.peers` (its next hops). The runtime reads only the agent's spec and never the MAS workflow; the agent no longer receives `spec.workflow`, so an agent can run alone with only its next hops. An agent run alone (`chat`, `tui`, `serve`) reaches those peers through the Flavour's `bind.agents.<peer>` (a chain or an endpoint row), so it needs no MAS manifest.
- A peer is a running-spec entry like a tool. `disable_peer` / `enable_peer` (control methods, `RunningSpecPort`) mark it `enabled: false`; the offered `delegate_to_*` tools and a send then skip it, and the circuit breaker keeps one circuit per peer (`peer:<id>`), takes a failing peer away and offers it again after `reset_timeout_s`.
- `gov_no_undeclared_peer` and `gov_no_undeclared_tool` read the running spec as well as the declared one. A call is blocked as undeclared (never in the spec) or as declared but unavailable (switched off right now), and the model is told which.
- Native KG fold (`mas.library.kg`): the events-to-graph mapping is plain, stateless and order-agnostic. Each event maps to nodes and edges by itself with ontology names (`hasAgentCall`, `hasLLMCall`, `hasToolCall`, `hasProcessingCall`, `belongsToMASCall`, `executes*`, `hasInitialState`, `hasFinalState`, `hasState`, `inputTo`, `leadsTo`, `correspondsToTransition`, `representsExecution`); ids are deterministic from the event, so permuting the events gives the same graph and `realtime=True` equals the batch fold. States and Transitions come only from the `state_in` / `state_out` fields (a join lists several input States; the Session and the MASCall share the boundary States of `mas_call_end`). Containment is what the records name. `align_graph` is no longer applied to the native graph.
- Native KG fold: an `AgentCall` is contained by the MASCall of its session (`hasAgentCall` / `belongsToMASCall`), a delegated visit too; a `delegate_to_*` tool call is an ordinary `ToolCall` and the callee an ordinary `AgentCall`. A call that never ended keeps no `endTime` and no `success`. `process_event` returns the nodes and edges the event contributes.
- Native KG fold inference removed: timestamp-enclosure containment, the sibling State chain and `(no content recorded)` placeholder States, rendered agent outputs (`outputParts`, `outputContent`) and tool-use completions from sibling calls, resumed visits and `delegate_to_*` rewriting (`rewrite_tool_delegation`), routing-derived parents, the `parentCallId` backfill, the cut-off of unfinished calls at the last event, the `ThinkingCall` inferred from the call's duration (the `thinking` category maps an explicit `thinking_start` / `thinking_end` interval instead) and the legacy edge names `contains`, `hasCall`, `realizes`, `fromState`, `toState`.
- Telemetry: a `delegate_to_*` tool call is an ordinary `*.tool` span of the caller, and the callee's visit an ordinary `*.agent` span. The span tree no longer splits the caller's visit around a delegation or emits a resume visit. The agent's output is its final State, not the `user_response` text.
- Native `events.jsonl` has a delegate and a reply processing pair for each delegated visit, on the caller: `delegate` before the callee starts and `reply` after it ends, linked to the delegating tool call by `call_id` `{tool_call_id}:delegate` / `:reply`. A root visit gets neither. Parallel groups have one real `aggregate` processing pair at the group end. The fork and the wait / resume, parallel fork and aggregation ProcessingCalls are gone; their plain marker records stay. Context assembly ends at its start instant (no fabricated span, no `synthetic` flag).
- Telemetry (`observe_sdk`): span names follow norm's suffixes: every CallAnnotation is `{agent}.annotation` (the kind stays in `mas.annotation.kind`), `infrastructure_info` is `{agent}.worker`, a MemoryCall is `{agent}.memory`. Skill, memory and RAG spans carry `ioa_observe.agent.span_id`. `mas.memory.operation` uses the ontology vocabulary (`store`, `retrieve`; a RAG query is `retrieve`), and a memory write closes with its output (what it stored when it reports nothing back). A `*.context` span names the LLM span it was contributed to in `ioa_observe.annotates.span_id`. Governance points carry `mas.governance.decision_type` whenever the record states a decision (`decision_type`, `decision`, `outcome`, a HITL `resolution`) and `mas.governance.policy_name`; check intervals (`governance_authorize_*`, `governance_validate_*`, `obs_wrap_gov_*`, `checkpoint_*`) are `*.processing` spans, with a stated outcome as a `*.governance` point under them.
- Telemetry: a call that never reported its end is not exported (no `mas.status=unfinished`, no end time made up). `session.start` is the `mas_call_start` time, and spans already open when the session id is first learned get it.
- Native records that are not calls name the call running when they happened: boundary ingress/egress, client response, context steer, HITL and governance decisions get the innermost open call as `parent_call_id` (the call itself, never its contract-call wrapper; else the agent visit). Check and hook intervals (`governance_*`, `obs_wrap_*`, `observability_*`) have their own `call_id` and the call they bracket as `parent_call_id`. Context parts are owned by their LLM call. Working-memory mutations (`state_update_*`) are instants.
- `semconv` imports `OBSERVE_ENTITY_*`, `OBSERVE_AGENT_*`, `OBSERVE_SPAN_KIND`, `OBSERVE_WORKFLOW_NAME`, `OBSERVE_STATE_*` and `OBSERVE_PROCESSING_NAME` from `ioa_observe.sdk.utils.const` when the SDK has them, and is the one module holding these names.
- The qa-agent example graphs use the ioc-project scope-ontology URIs.
- `llm_call_start` carries the request: `model`, `messages`, offered tools, `max_tokens`, `temperature`. `llm_call_end` is the return: `output`, `finish_reason`, tokens, `tool_calls`, and `max_tokens` only when the sent cap differs. `model` on `llm_call_end` is no longer read.
- `--cache-read` / `--cache-write` help now says the cache is off until those flags insert the `llm_cache` step. The default pipeline is retry only.
- Samples, bundled libraries, docs and resolved steps write `layer:`, not `middleware:`. `infra show` lists `layer`, and the resolved infra document exposes `pipeline`, so `--override '.infra.spec.pipeline[layer=llm_cache]...'` addresses a step. `llm_cache` params are checked for a `layer:` step as for the old key.
- The `stack_layer` plugin type is gone: the A2A, SLIM and http layer rows register as `layer` (URNs `mas.layer.*`).
- The cache docs describe one LLM cache, the `llm_cache` step. The "built-in engine cache" sections are gone; `MAS_LLM_CACHE_READ` / `_WRITE` and `RuntimeEngine` `cache.read` / `write` insert that step.
- `behavior.delegation_style: none` hides the leftover `delegate_to_*` tools from the LLM. The peers and the send path stay, so a linear, parallel, or single pattern can still send to the next agent.
- Leftover `delegate_to_*` tools shown to the LLM are this agent's `delegates_to`. An empty list means none of those tools. An agent not listed in the workflow shows none. `workflow.entry` only names who receives the user message.

- Native KG fold: emits exactly the SCOPE vocabulary. `library-kg/ontology/extensions/` is deleted (SCOPE now declares `ThinkingCall`, `Skill`, `SkillCall`, `Memory`, `MemoryCall`, `RAGQuery`, `CallAnnotation`, `GovernanceEvent`, `ContextContribution` and `Worker`). A `RAGQuery` is a `MemoryCall` contained by `hasMemoryCall`; memory and RAG calls `executesMemory` a `Memory` named by `memory_name`, else `collection`, else `memory_type`, with `memoryOperation`, `memoryQuery`, `resultCount` and `topK`; a `SkillCall` `executesSkill` a `Skill`. A `ContextContribution` `annotates` its `LLMCall` (replaces `contributesTo`); annotations carry `eventKind`, `eventTime` and a JSON `payload`, governance events `decision`, `policyName`, `decisionReason`, contributions `partId`, `contextSource`, `contributionMechanism`, `tokenEstimate`, `retained`. `infrastructure_info` gives a `Worker` and a `runsOn` edge from its parent call. The fold writes `usesLLM` and `usesTool`, `processingType` on the `Processing` node, and no longer writes the generic `executes` edge (read through RDFS inference) or a `description` copied from the name.
- KG validation (`run_shacl_validation`) passes the SCOPE ontology as `ont_graph`, so shapes that target a superclass (every `ExecutionElement`) now fire: inbound containment, temporal enclosure (a warning) and the Session boundary States. The `test_validate_kg.py` cases for them run in SCOPE vocabulary; the five that encoded MAS Lab rules SCOPE does not have are deleted.
- Native vs OTel equivalence compares the native fold of the events with converter output through `norm.normalize`, with nothing run on either result: calls pair by call id (`mas.call.id`), States by the producer State id, Transitions through their call. `compare_trajectories` returns the remaining differences, and the tests pin exactly that set. The `delegation` and `cut-off` fixtures are regenerated in the explicit State-id format (`tests/fixtures/path_equivalence/build_fixtures.py`). The `categories` fixture (one visit that uses every opt-in category) is compared with the default categories (core classes only) and with `categories=all` against `norm`'s output for every span kind, each with its pinned differences.
- Native KG fold: the timing brackets of a check (`governance_authorize_*`, `governance_validate_*`, `obs_wrap_gov_*`, `checkpoint_*`) are core `ProcessingCall`s, as the converter exports them: the name and `processingType` are the kind without its suffix, the call named in `parent_call_id` contains them (`hasProcessingCall` from an agent visit or MAS call, `containsCall` from an LLM or tool call), and their States come from `state_in` / `state_out` when the records carry them. A `GovernanceEvent` point (`annotations`) exists only for an end record that states a decision (`decision_type`, `decision`, `outcome`, `resolution`) and annotates the bracket. The `call_id` of any other interval pair (`state_update_*`, `wait_state_*`) is its own key, no node: the annotation `annotates` its `parent_call_id`, so every node of the graph is linked in every category set.
- Native KG fold: `reasoningEffort` (from `llm_call_start.reasoning_effort`) and `reasoningTokenCount` (from the usage's `completion_tokens_details.reasoning_tokens`) appear on an `LLMCall` only when the events carry them; no `0` or `""` default is written. The Session id is the one the events carry, also under a hierarchical `a/b/c/d/e` run id (the run id still names the app); the run id is the Session id only when the events carry none.
- Native vs OTel equivalence runs on a live trace with the current emitter (`explicit-events.jsonl.gz`). The pinned differences are the call names, calls the converter does not export because they never ended, and on check brackets: `governance_*` and `checkpoint_*` intervals need the governance export layer, a bracket is contained by its parent call natively and by its owning agent call in `norm`, and `norm` gives a State-less ProcessingCall a State pair and a Transition.
- The tests have no skip: the opt-in dependencies the parity and bench tests need (`ioc-scope-norm`, `mas-lab-library-telemetry`, `mas-lab-component-bench`, the OTLP exporter) are part of the `dev` extras of `library-kg` and `library-telemetry`; a missing trace fixture fails instead of skipping. The reasoning-endpoint tests in `runtime` answer from a fake endpoint (`httpx.MockTransport`) and need no host or key.

### Breaking

#### Secret references

- A manifest holds `{secret: NAME}` or `{secret: {name, key, optional, prefix}}`, never a value. A literal in a secret field or a credential header, and credentials in a URL, are load errors that name the field, never the value.
- `SecretRef` names and keys match `[a-z0-9][a-z0-9._-]{0,127}`; `prefix` is one auth scheme word and a space, or empty.
- `env:` strings and `*_env` keys (`api_key_env`, `secret_env`, `password_env`, `embed_api_key_env`, `otlp_endpoint_env`) are a load error that names the replacement.
- Manifests refuse the `!!set`, `!!omap` and `!!pairs` tags and a document past 100 levels or 100,000 nodes with aliases expanded; a YAML error never quotes the document.
- `standard:openai`, `mas-lab init`, `openai.example.yaml` and the sample workspace write `api_key: {secret: openai}`. Set the key with `mas-ctl secrets set openai`.
- An LLM layer's default secret (`openai`, `anthropic`, ...) goes only to that provider's own endpoint. Any other `api_base` needs an explicit `api_key`; a keyless server (ollama, vllm, litellm) takes `{secret: {name: NAME, optional: true}}`.
- `OPENAI_BASE_URL` / `OPENAI_API_BASE` apply only when the infra `api_base` is empty, and the leaf then needs an explicit `api_key`.
- ClickHouse and Neo4j passwords are declared SecretRefs; none is assumed.
- An override that moves the endpoint of a row holding SecretRefs must restate the credentials.
- A2A `admission.callers` maps a caller name to a SecretRef. The tokens are read on every request, so a rotated token applies without a restart.
- `mas-ctl infra show` prints a SecretRef as name, presence and size. A missing required secret refuses the start.

#### Secret stores

- The default is a file store (`<workspace>/.mas/secrets`, files `0400`, directory `0700`, owner checked, 1 MiB cap). The `env` store (`MAS_SECRET_<NAME>`) is for development only. `--secrets-dir` and `--secret-store file|env` select the store.
- The file store refuses a symlink.
- `mas-ctl secrets init|set|list|import-env` never print a value. `secrets set` reads a hidden prompt or stdin and refuses a value on the command line. `import-env` copies the known provider variables (`HF_TOKEN` as `hf`, ...) into the store.
- Production hardening fails the env store (`secret-store-env`) and a file store with `allow_loose_permissions` (`secret-store-loose-permissions`).
- One secret resolver per process (`set_process_resolver`, `current_resolver`).

#### Host environment

- Library and runtime code no longer read `os.environ`. An entry point calls `mas.runtime.entrypoint.install_host_environment()` once; it keeps declared non-secret keys in `mas.runtime.hostenv` and scrubs the process. An embedding program must make that call first (see `docs/references/secrets.md`).
- Children (shell steps, skill scripts, `docker compose`, MCP stdio servers, lab spawn workers) get `child_env`: an allowlist plus declared keys, not the host environment.
- A proxy, OTLP endpoint or `NEO4J_URI` variable that holds `user:password@host` or a credential parameter stops the entry point.
- `.env` is no longer exported and holds non-secret `MAS_LLM_*` settings only; the shell wins over it. Move keys with `mas-ctl secrets import-env`.
- The `env` of a service, fixture or shell step accepts `{secret: NAME}`, revealed only into that child; a plain value under a provider key name, a `MAS_SECRET_*` name or an installer index variable is refused. A shell step copies service-exported variables with `passthrough`.

#### Network and transport

- `LLM_PROXY_API_BASE` is removed. `standard:llm-proxy` sets `proxy.api_base` itself; change it with an overlay.
- Plain http never carries a secret except to a loopback host; there is no setting to allow it. A remote A2A peer needs https.
- A secret header goes only to the origin of the row's `url` (an A2A row may list `allowed_origins`). No redirect is followed with a secret outside that origin.
- A request that carries a secret to a loopback host ignores the environment's proxy settings.
- A failure with an HTTP response says `<Type>: HTTP <status>`; the body, the provider's message and its request id are never shown. A failed A2A call names a fixed reason and the JSON-RPC code. A tool server's own JSON-RPC error stays the tool result.
- A ClickHouse connection or query error names the error class and the HTTP status, never the server's answer.

#### Lab and docker

- `mas-lab --secrets-dir` and `--secret-store`. One secret store per lab process; the controller daemon serves the store it started with and refuses a job for another store.
- The `mas-lab telemetry` ClickHouse commands no longer take `--password`; they read the `password` ref of the user connection.
- One ClickHouse client (`clickhouse_http`, stdlib HTTP): the `clickhouse` extra, `clickhouse-connect`, `collector.dump_spans`, `list_apps` and `run_dump_spans` are removed; read spans with `clickhouse_http.fetch_spans` or `OtelSpanSet.fetch_from_clickhouse(conn, key, ...)`. `mas-lab telemetry list-apps` is removed (use `apps list`); `dump` is core's (`--by run`, `--app-name`); `push` and `verify` are library-telemetry's.
- `mas-lab otel` is removed; use `mas-lab telemetry`. The `mas.lab.telemetry` re-export package is removed; import `mas.library.telemetry.collector` (`push_file`, `convert_file_to_otlp_jsonl`) or `mas.library.telemetry.verification`.
- A scripted-model fixture is keyless: its proxy row names the optional secret `lab`, so a fixture lab runs with an empty store.
- The controller HTTP app is a factory: run `uvicorn --factory mas.lab.controller.fastapi_app:served_app`; the module-level `app` is removed.
- Controller run requests (`run-mas`, `experiment/run`, `/api/commands`) no longer take `pre` shell steps, and the demo UI no longer sends a layout node's `pre`.
- Docker: compose no longer uses `env_file`, and containers get only the variables `compose.yaml` lists. The secret store lives on the mounted workspace, the user config mount moved from `/root/.config/mas` to `/config/mas`, and the container runs as the owner of the workspace. After upgrading from an older image that wrote `docker/data` as root, run `sudo chown -R $USER docker/data` once (or the folder `MAS_DATA_MOUNT` names).

#### Other

- `EngineToolContract.claims` takes the turn context: `claims(tool_name, *, ctx=None)`. A custom engine tool that defines `claims(tool_name)` must accept `ctx`.
- The runtime names no layer and ships no pipeline data: default steps, the `spec.control` bridge keys and the response-store layer come from attributes on library rows (`default_steps`, `control_key`, `retry_overlay`, `stores_llm_responses`). The fallback `openai` default protocol and the `mock` non-protocol name are gone; `protocol: mock` is no longer read as the default. `RetryPolicy` and the retry loop moved to `mas.library.standard.lib.retry`; `ReliabilitySettings` is gone (the `spec.control` keys are checked by the layer that answers to them). `--connect` / `--listen` schemes come from the protocol plugin's row (`protocol_name`, `url_schemes`, `connect_as`).
- `retry` and `circuit_breaker` with `per: all` on the shared engine call raise at install; they never saw a failure there. A non-idempotent tool is no longer retried on a connect error either.
- The `stack_layer` plugin type is gone (never used): register the layer as `layer`.
- The step key is `layer:`. Resolved steps no longer carry `middleware`, and the resolved infra document exposes `pipeline`; `--override` addresses written `spec.interceptors[middleware=X]` still resolve, with a warning.
- An agent's next hops come from `spec.communication.peers`, which mas-ctl compiles from the MAS workflow. An agent manifest that carries `spec.workflow` and is run without mas-ctl no longer gets peers from it: declare `spec.communication.peers`.
- KG: removed `ParallelGroup`, `Branch`, `TaskCall`, `Run` and `Application` nodes, the `application_node` option (and `--application-node`), and the edges `hasRAGQuery`, `contributesTo`, `derivedFrom`, `invokesSkill` and `hasSession`. `parallel_group_*` records map to nothing and `branch_*` is no longer a known kind; a task is a `MASCall` (`mas_call_*`).
- KG: the `fill_synthesized_processing_defaults` and `allow_heuristics` parameters (and `--fill-synthesized-processing-defaults`) are removed; they gated nothing.
- KG: the `include_infrastructure`, `include_trajectory`, `include_provenance`, `include_governance`, `include_annotation` and `include_native_extensions` options (and their `--include-*` flags, the `LAYER_KINDS` table and `layer_skip_kinds`) are replaced by one option, `categories` (`--categories` on the CLI, `categories:` on the `normalize_events` step). There is no alias of the old names.
- Telemetry: `rewrite_tool_delegation` is removed (the converter and replay parameter, `--rewrite-tool-delegation` on `convert`, the `otel` plugin option). The converter no longer writes `ioa_observe.agent.previous` or `ioa_observe.handoff.source.*`, and no longer writes placeholder or `input:<agent>:` / `output:<agent>:` values for an empty input or output.
- Telemetry: `MasOtelConverter.flush_open_spans`, `UNFINISHED_STATUS`, `SpanIdReuse` and the `id_generator` argument are removed; realtime takes an `event_logger` (an OTel Logger), and `realtime=True` without one is an error. Realtime signals are no longer spans. `ThinkTagStreamFilter` is replaced by `ThinkTagSplitter` (returns answer and reasoning pieces).
- Native: the `rag_query_*` pairs made from RAG context parts (`synthetic: true`) are gone; a RAGQuery comes only from a real retrieval boundary.
- Code names the ontology SCOPE (`scope_models`, `ensure_scope_topology`, `scope_default`, `SCOPE_ONTOLOGY_EDITABLE`).
- A pattern's next hop is this agent's `delegates_to`. The old fallbacks (`workflow.participants`, every other node, the agency list) are gone, and an agent with no `delegates_to` has no next hop (the pattern logs a warning and ends the turn). `behavior.delegation_style` does not affect a pattern's next hop.
- `send` no longer refuses a target outside `delegates_to`; the model can only call peers that are offered, and `gov_no_undeclared_peer` (in `with-hardened`) blocks the rest. It does nothing when the kernel has no agent id.
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
  Engine build requires every live leaf's `api_key`, including a leaf
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
- Native→KG default output is the SCOPE core set: RAG / memory / skill kinds
  and `ThinkingCall` are opt-in categories (`categories`, default none). `all_attributes=False` keeps only keys the installed SCOPE
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

### Deprecated

- The `llm_provider` kind `cache` (`CacheLLMProvider`) warns once. Use the `llm_cache` layer; [llm-cache.md](docs/manifests/llm-cache.md#deprecated-the-cache-provider-kind) shows the move. A file recorded with the old class must be recorded again. It goes in the next release.

### Fixed

- `mas-ctl run-mas` and the lab runner build each agent from the infra the Flavour bound (its `bind.llm` and `bind.tools` stacks and any identity step), not the infra as the compose step resolved it before the Flavour was applied. A protocol-role layer on a tool-server row's `pipeline` is applied as a tool layer, so the identity layer's MCP session hook is installed and a refusal on a tool call reaches the model as its closed class, not `Connection closed`.
- A delegation inside `mas-ctl run-mas` no longer stops with "a sync call cannot wait for async work inside a running event loop": the in-process bus delivers the transport message to the target agent with `await`, not with a sync call from the sender's running loop (`CommBus.asend`, `CommEndpoint.adeliver`).
- A remote A2A task that failed, was rejected or was canceled (also in a streamed status update) raises a value-free `AgentCommError` ("remote A2A task failed") instead of returning the task dump as the reply; the peer's own error text is not carried.
- The reply text of an A2A task is read from its artifacts, else from its status message; a streamed reply is the text of its artifact updates, not a dump of the closing status update.
- A refused MCP connection reaches the caller as its real error (`ConnectionRefusedError`, ...) instead of "unhandled errors in a TaskGroup".
- A lab run of an agent whose manifest (or a Control) lists a `native` observability entry writes the run's `traces/events.jsonl`: the entry used to build its own sink at its default path next to the manifest, so the run had no events file (seen with `native: transport: defaults: enabled: false`, and with it on). Turning transport observability off removes only the `transport.*` events; `docs/references/transport-observability.md` states it.
- A tool-provider registry pass asks each provider for its tools once however many names route to it: a provider serving N tools was listed N times per pass, and a refused listing was refused N times.
- MCP tool discovery is cached again for servers that send no `ttlMs`: the SDK result filled the absent field with `0`, which the client read as "do not cache", so every routed tool name listed the server again (about 28 `tools/list` requests per tool call in a lab run). Such a list is kept for 60 s (`DEFAULT_LIST_TTL_MS`); a `notifications/tools/list_changed`, a failed tool call or closing a held session drops it earlier, and a listing that was in flight when the list changed is not stored. A caller gets a copy of the cached list.
- A lab batch that raises or is interrupted (`Ctrl+C`) no longer leaves its services running or skips its `post` steps: the services started for the batch are stopped (including when writing the results file fails), the batch is marked failed (the `post` steps do not run over partial output), and the original exception is re-raised. A cancelled task still cancels after that mark is written.
- The first run of an arm no longer deadlocks on imports (`deadlock detected by _ModuleLock`, reproduced with threads importing the `library_ioa.plugins.mcp` modules, and with the A2A SDK against `grpc`): before any run starts, the batch imports every installed plugin module once, on one thread (`mas.runtime.registry.preload`); the controller startup does the same (a spawn worker runs one job at a time and is not preloaded), and both also import the `mas.runtime` and `mas.ctl` packages (`mas.ctl.preload`), whose modules import each other in cycles. The telemetry (`ioa_observe`) and KG handler modules are listed under `warm:` too. A plugin lists the modules it imports lazily under `warm:` in `library.yaml` (the A2A plugin lists its SDK and `grpc`) and they are imported too when installed. `library_ioa.plugins.mcp` no longer imports its submodules in its `__init__` (import `MCPToolProvider`, `MCPClientFactory`, `MCPProviderSpec`, `McpLayer` from `.provider`, `.spec`, `.layer`); that cycle was the cause. A plugin with missing requirements is skipped and fails with its own message when used.
- The application manifest an experiment names is the manifest that runs: a misspelt `kind`, or a file with no `kind` next to a `mas.yaml` or `mas.yml` (and not an OASF record), is refused, naming the file and saying the sibling is not used, instead of silently running the sibling or running a typo as an agent. Only the exact names `mas.yaml`, `mas.yml` and `mas.json` make a file a MAS. `mas-lab validate` prints `runs: <path>` (or fails when the named file is refused) and the dry run prints the resolved manifest.
- A human approval no longer skips a deny. It only answers the hold it was asked for: the egress chain still runs (each plugin's HITL hold is read as ALLOW for the approved call, so another plugin's `BLOCK`, `RETRY`, `MODIFY` or `LOG` applies in either order), and the ingress chain still runs for a tool result. The ingress chain is first-match (`chain: stop`) for a new hold. With `hitl_once_per_turn: true` an approved class is not asked again in the turn, but later plugins still run, so a later `BLOCK` still applies (it used to run, for example `delete_all` after an approved `read_file`, or a secret tool result after an approved earlier result). Behaviour changes to know: an approved call is evaluated twice by each plugin (the hold, then the approved pass; `EgressIntentView.hitl_approved` tells the second).
- A turn ends clean however it ends. Every flow-scoped field of the kernel state (waits, held approval, pending call, machine states) returns to its default when the kernel goes idle, so an ingress `BLOCK_ON_ERROR` or `RETRY_ON_ERROR` no longer leaves the failed call's wait open and the next turn unanswered. An ingress retry now targets the failed call by correlation id.
- A pause cancels every engine wait and pending call but keeps the pending human answers (holds). The hold survives the pause: an answer that arrives while paused is remembered and applied once on resume, so an approval runs the held call exactly once and a denial is honoured; an abort still drops every hold. Holds are a collection keyed by request id (`QProduct.hitl_holds`): the results of a parallel batch that each need approval coexist and each resolves on its own, and a denial ends the turn with its sibling holds. Model and tool machines in an error state are reset to idle at the end of a step that leaves the kernel idle.
- A steer from idle starts a clean turn like a user input; a steer while a human answer is pending is refused with a recoverable `STEER_REFUSED` boundary error instead of overwriting the hold.
- No egress while paused or stopped: the kernel ignores every event but lifecycle ones, and the egress chokepoint refuses unless the lifecycle is running, for every design pattern.
- The chokepoint coordinator no longer writes the governance and observability fields of the product state; a test bans `q.<field> =` writes outside the kernel, machines and design patterns. See `docs/references/kernel-invariants.md`.
- More kernel fixes, each with a test that fails on the old code. A checkpoint carries every field of the product state and the run ledger's id allocator and sequence counter (it lost five fields and reused ids after a restore). A BLACKLIST verdict covers the named tool for the session (`QProduct.tool_blacklist`), not every tool. A hold in a parallel batch no longer drops the calls already judged (they run) or the calls after it (they are queued and judged one at a time). An egress `RETRY` asks the chain again under the same call id, bounded by `max_gov_retries`, instead of stalling the turn. The model call after a tool result is judged without that tool's name and arguments. A design pattern that raises, or any exception inside a step, leaves the state and the ledger as they were before the step. Only a model call registers as the session's interruptible task, so a tool of a parallel batch can no longer replace it or be cancelled by a steer. The deterministic patterns read only the tool results of the current turn (`supervised` failed on its second turn), and that mark is restored with the checkpoint.
- A conformance test holds every registered governance plugin and HITL responder to the kernel's obligations: a verdict of the contract's shape, the same verdict for the same input, nothing handed over is written to, and a deny is never turned into an ALLOW by a chain.
- Behaviour changes to know. A call a pause interrupts is answered in the ledger by an `ERROR` row (a tool is not stopped by a pause, so its outcome is unknown: "it may have executed"); its real result, if it arrives later, is dropped with a log line. An approved or retried call is judged under the id of its first pass, so a plugin that counts calls (`budget`) counts it once and a human is not asked twice. `HitlApprove` with no request id answers the one pending request and is refused (`HITL_APPROVE_AMBIGUOUS`) when several are pending. The driver no longer registers a call's wait itself; the kernel does when it authorizes the call, which closes a race with a pause.
- Checkpoint format. A checkpoint now carries every field of the product state, the hold collection (`hitl_holds`, with the answer stored while paused), `call_slots` and `pending_call_id`, and the ledger's id allocator and sequence counter; a restored kernel with fields it does not know is refused (`CheckpointVersionError`). Checkpoints written by the earlier versions are migrated: the one-slot hold becomes one hold under the same request id. Two cases are refused with a message because they cannot be loaded safely: a session-wide blacklist (`tool_blacklisted`), and a hold on a tool result that the old version did not save.
- For consumers of checkpoint and trace JSON. Ledger rows now carry `call_id` (the declared tool call a result answers, for a batch), and a tool result row carries the `tool_name` and `tool_arguments` of its call; a placeholder row (skip, deny, interrupted call) is added for each such call. The `governance` state in observability payloads can differ from before: the chokepoint coordinator no longer writes it into the product state, and a governance ERROR is kept after its turn.
- `service_stop` stops process services started by `service_start` (shared ServiceManager, process group, pidfile). Missing `service_stop` still stops at the end of the level; `keep_running: true` leaves the service running. `post:` services stop on error and Ctrl+C.
- `service_start` and `service_stop` module docs show the `pre:` / `post:` level hooks, not a nonexistent `pipeline:` key.
- A tool a system plugin offers from the live turn context is claimed and run: the engine's tool dispatch passes the turn context to every engine tool's `claims(tool_name, *, ctx=None)`, so skill tools execute. Their result is formatted as text like any other tool result (a dict result used to fail with a validation error).
- An agent whose tools come only from `spec.system_plugins` gets the tool loop.
- `mas-ctl chat` wires the subagent spawner and passes `--model` to the child agents it starts.
- `mas-lab benchmark pipeline run` stops with the library's error when a lab's library fails to load, instead of failing later on an unknown step.
- Removed the unused `mas.library.telemetry.codecs` span codecs; `library-lab` alone provides the `otel_traces` codecs.
- The default test run is offline: model calls go to a fake server, SLIM tests to an in-process node. Tests that need a network, Docker or keys live in `tests/functional` (`task test-functional`) and are not run by CI.
- A lab whose library fails to register is dropped whole (none of its libraries stay registered), so it no longer breaks every lab loaded after it, even when the working directory is inside it; a failed library manifest restores the registry and no longer keeps its package name claimed.
- `mas-lab benchmark pipeline run` resolves a lab's own `library.yaml` steps from any directory, like the controller.
- The cot, react and reflection moderator overlays set `params.max_steps`, and the reflection overlay selects the reflection pattern.
- Removed the `inject_lab_context` alias. `register_lab_dir` returns the position the root held before, and `restore_lab_dir` undoes one registration. Both are safe to call from several threads.
- Threads the runtime and its libraries start run in an empty context on every Python build (`mas.runtime.aio.new_thread`). Python 3.14's free-threaded build starts a thread with a copy of its creator's context variables, so a worker thread started from inside a call saw that call's frames, event stream and transport. A test fails on any direct `threading.Thread` in the sources, and nothing depends on the `thread_inherit_context` setting. The free-threaded CI job no longer leaves a runtime test out.
- Removed dead code found while moving the threads: the unimportable `mas.lab.components.controller.backends` module (it imported a module that does not exist and nothing imported it), `ObservabilityOperator.record_kernel_snapshot` and `ObservabilityOperator.audit` with its `AuditReport` model (no caller), the KG viewer's unused `get_composer_js` / `get_composer_css` and its `kg_composer` assets, the viewer's `offline` option (it still loaded Cytoscape from a CDN), its temporary file, and an unused reply helper in the local group link.
- `mas-lab plot kg`: the viewer page escapes the title and a `</script>` in a node label can no longer end the script element that holds the graph; the thread that opens the browser starts in an empty context.
- Native and OTel knowledge graphs of one run now agree (calls, states, transitions, edges, session and MAS-call intervals, `Run`). The native fold keeps an agent's `user_response` as its output instead of the context transcript, starts a resumed visit when its delegate returned, and closes a cut-off call at the last event (OTel did so at the replay's wall clock). OTel links every call to the calls that enclose it, names the run, and takes the session and MAS-call interval from the task span. `boundary_egress` is mapped on both paths. `compare_trajectories` checks it field by field.
- A run that raises or is cancelled closes the engine calls it left open.
- The qa-agent example traces and graphs and the Tutorial 1 sample trace are recaptured with the current event shapes. `observability-otel-json.yaml` loads again (the otel plugin declares `output_path` as a run path).
- A run cut off by its step budget no longer leaves the cut-off call's frame as the parent of later calls.
- The native graph keeps the reply text of an OpenAI-shaped response that also carries tool calls, and keeps a falsy tool result such as `[]`.
- The OTel completion pass names offered tools like the native path and no longer reads `True` as a number.
- An unfinished or errored processing span or MAS call read from OTel is not successful, as in the native graph; a processing span with no parent is dropped (logged) when the file holds several sessions.
- Golden fixtures and `capture_golden_run` keep repo-relative paths in `run_info.json`.
- The trajectory plot reads an agent's reply from `output` first.
- The observe-sdk profile exports ProcessingCall as `*.processing` by default.
- `run_normalize_otel` reads LLM request and return fields, the MAS call outcome, and ProcessingCalls from `mas.*` span attributes, and rebuilds the trajectory with the native path's function.
- Spans carry structured tool calls and processing content, declared in the span spec.
- A structured tool result is JSON text, not a Python repr. `State` and `Transition` have no invented name; the `MASCall` is named like `norm`; `Agent` and `MAS` are declared.
- Both paths share the session anchor edges.
- Native KG and OTel name `LLMCall` the same way, take Session and MAS identity from the events, and treat a failed, cut-off, or interrupted call as unsuccessful.
- The trip-planner moderator allows 40 dispatch steps, not 10.
- A lab's own `library.yaml` steps (a lab-local `snapshot_kg`, say) now resolve when the controller daemon runs the lab from another directory, including `/api/pipeline/run`. Library discovery adds the lab root to its search, not only the process cwd, and plugins of a lab loaded after the registry was built are added to it. When two labs in one process declare a library of the same name, the one loaded last wins. Two labs that ship the same plugin package name are refused. Before, the post pipeline failed with `Unknown step type`.
- `mas-lab init` never writes `OPENAI_API_BASE` for a custom URL (an infra called `openai` used to) and never a name that starts with a digit.
- The adversarial and trip-planner LLM judges follow `OPENAI_BASE_URL`, then `OPENAI_API_BASE`, like the engine.
- A tool whose `on_execute_tool` returns a coroutine through a `functools.wraps` decorator is awaited on the loop instead of raising under `engine.tool_worker: inline`.
- The sync tool path (`call_tool`) finishes an async tool on one shared helper loop, so a client kept between calls stays valid. It used a new loop per call.
- A `None` answer from `on_aexecute_tool` / an async `on_execute_tool` falls back to the legacy `execute` like the sync path does.
- `mas-lab init` gives a second infra name with the same slug its own variable (`MY_PROVIDER_API_BASE_2`).
- A LLM address taken from the shell (`OPENAI_BASE_URL`, `*_API_BASE`) logs one warning with its host: the explicit `api_key` is sent there.
- Closing an MCP session after the event loop is gone does not raise.
- The http call transport under `openai` and `mcp` keeps what the old clients had: the row's headers (an MCP bearer token reaches the server), redirects, a 120s LLM and a 30s/300s MCP timeout, the `verify_ssl` / `ca_bundle` TLS settings, and plain `http://` on an internal host. It holds one async client per event loop and closes them on stop. An OpenAI transport failure drops that transport's client.
- Closing an MCP provider stops its http transport. Engine stop closes the tool provider. An SSE or stdio MCP row gets no http transport.
- MCP `call_tool` refuses a name that is not advertised. An empty `tools` list is nobody.
- A tool-server row may list `http` and `mcp`; the provider already built them. A Flavour `bind.tools` stack that names them is a bind error.
- Binding a Flavour twice replaces its `bind.llm` / `bind.tools` stack instead of adding a second copy.
- A cache answer replayed through the provider layers must be a completion; a bare JSON answer is an error, not a message.
- `retry` for both llm and tool calls keeps the tool default (2 attempts, only for tools declared safe to repeat). `require_idempotent` blocks every failure class for a tool that is not idempotent. A bare `429` in a message is a rate limit; `4290` is not.
- `retry` and `circuit_breaker` refuse `per: all` on the shared engine call, where they never saw a failure. The async llm path reports the failure class like the sync path.
- A probe runs only for a tool declared idempotent, and off the event loop on the async path. A session refusal (`SessionPaused`, `ControlDenied`) is never retried. A spec conflict is retried up to 64 times, not 4.
- `activate_skill`, `list_skill_files` and `read_skill_file` can be called through the local provider: a system plugin claims a tool from the turn context. A failed system-plugin load binds nothing.
- A bad `subagents` setting (`max_depth`, `max_spawns`, `templates`) is a load error `mas-ctl validate` reports. `max_spawns: null` is unlimited.
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
  leaf uses its own `api_key`.
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
- `mas-lab benchmark publish-hf` uploads an export archive to a Hugging Face
  dataset repo, and `import-hf` downloads and restores it. Publishing is
  private and review-based by default (`--visibility`, `--create-pr`) and
  prints the commit SHA. `import-hf` takes `--revision` to pin a commit and
  `--sha256` to verify the tarball before extraction; without `--sha256`
  nothing is verified. See [Sharing benchmarks](lab/docs/sharing-benchmarks.md).

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
  `--token-file`. If neither is set, ctl reads secret `hf` from the store
  (`HF_TOKEN` is not read). `import-hf --dry-run` uses a token when one
  resolves and does not require one when none does.
- `mas-lab benchmark export` builds `source/` from an allowlist of folders
  and file types, skips `.env*`, keys, and certificates, fails if a sensitive
  file is found, and writes archive-relative paths only to `MANIFEST.json`.
  `import` skips members and links that resolve outside the extraction root
  and aborts on an unsafe `MANIFEST.json` path.

- Public GitHub and docs URLs moved from `outshift-open/mas-lab` to
  `ioc-project/mas-lab` (`https://github.com/ioc-project/mas-lab`,
  `https://ioc-project.github.io/mas-lab/`). On an existing clone:
  `git remote set-url origin git@github.com:ioc-project/mas-lab.git`.
- Docs name the ontology SCOPE
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
- Deprecate plugin types `infra_middleware` and
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

- The runtime is async end to end. A driver, engine, call layer, LLM provider, context assembler, summarizer, tool dispatch, send, delegation, spawn, inbound serve chain and turn all await. The sync twins are removed: `ainvoke` replaces `invoke`, `achat_completion` replaces `chat_completion`, `aexchange_preview` and `aexchange_snapshot` replace their sync forms, `asend` replaces `AgentCommContract.send` and `Layer.send`, `adelegate`, `acall_delegate_tool`, `aspawn`, `acall` replace their sync forms, `aexecute_engine_tool` replaces `execute_engine_tool`, `atick` and `aprobe_tool` replace `tick` and `probe_tool`, and the registry and manifest provider keep only `acall_tool`. The session turn (`RunTurnFn`) and the inbound `Handler` are coroutine functions; `SessionTurn` is gone.
- A plugin may still be sync or async. A hook that returns an awaitable is awaited, and a plain `def` callback (a tool, an `on_request`, a `run_turn`) runs without blocking the loop where the plugin says so. An LLM provider must provide `achat_completion`.
- A caller that is not async uses `SessionController.run_turn`, `feed` or `submit_hitl`; each runs the one async implementation on its own private loop and refuses to run inside a running loop. The helper loop thread of the sync tool path is gone.
- The A2A client is async (`asend_message`, `aget_agent_card`; no `close`), and the HTTP layer opens an async client per send. A turn guard (`ChainHandles.turn_lock`) is a `TurnGuard` taken with `async with guard.ahold()`; it is re-entrant along one call chain.
- `check_layer`, `check_transport` and `check_agent_comm` are coroutines; `check_agent_comm_async` is removed.
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
