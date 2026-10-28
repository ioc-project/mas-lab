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

- Public GitHub and docs URLs moved from `outshift-open/mas-lab` to
  `ioc-project/mas-lab` (`https://github.com/ioc-project/mas-lab`,
  `https://ioc-project.github.io/mas-lab/`). On an existing clone:
  `git remote set-url origin git@github.com:ioc-project/mas-lab.git`.
- Docs name the former Observe and eXplain Platform as SCOPE
  (`https://ioc-project.github.io/scope/`). PyPI `oxp-ontology` and the
  `norm` git extra are unchanged.
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

- `--bind NAME=URI` is a hidden alias of `--connect`. It still works and prints
  a warning. It remains for one release.
- `/api/libraries/**`, `/api/jobs/**`, and `/api/experiments/**` (results tree).
  They remain for one release as adapters. `__MAS_LAB_DEFAULT_LIBRARY__` is an
  alias of `__MAS_LAB_DEFAULT_SCOPE__`.

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
