<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# `mas-ctl` command-line reference

`mas-ctl` runs, serves, and inspects a single agent or a multi-agent system (MAS) from YAML manifests. This page lists every flag. If this page and the code differ, `mas-ctl COMMAND --help` is correct.

Terms: a *manifest* is a YAML file that declares an agent or a MAS. An *overlay* is a small YAML file merged on top of a manifest. A *flavour* is a deployment preset. An *infra bundle* is a YAML file that says where services live. A *session* is one conversation with an agent. A *checkpoint* is a saved session state. See the [glossary](../glossary.md).

## The three commands most people run

```bash
mas-ctl chat agent.yaml -q "What is 2+2?"      # one agent answers one query; the reply goes to stdout
mas-ctl run-mas mas.yaml -q "Plan a trip from Celestia to Verdantia" --trace   # a MAS answers; --trace prints the AGENT/LLM/TOOL exchange to stderr
mas-ctl serve agent.yaml --infra-ref infra/a2a-qa.yaml   # exposes the agent over A2A and keeps running
```

Related: [CLI overview](index.md), [config.yaml](../references/config.yaml.md), [observability (`events.jsonl`)](observability.md), [TUI](../ctl/tui.md), [CLI overrides](overrides.md).

## Task guide

### Run an agent

```bash
mas-ctl chat agent.yaml -q "What is 2+2?"
mas-ctl chat agent.yaml -i
```

`-q` runs scripted turns. `-i` opens a multi-turn prompt. The reply goes to stdout.

### Run a MAS

```bash
mas-ctl run-mas mas.yaml -q "Plan a trip from Celestia to Verdantia"
```

### Pass an overlay

An overlay changes the manifest for one run, without editing the file.

```bash
mas-ctl chat agent.yaml -o overlays/tools.yaml
mas-ctl chat agent.yaml --tool calculator
```

### Patch one field

```bash
mas-ctl chat agent.yaml --override '.agent.spec.context.role="security expert"'
mas-ctl run-mas mas.yaml --override '.mas.spec.workflow.entry=moderator'
```

See [CLI overrides](overrides.md) for the selector syntax.

### Watch the exchange between agent, model, and tools

```bash
mas-ctl chat agent.yaml -i --trace
```

The exchange log goes to stderr. Add `--trace full` to include payloads.

### Turn on the event log

```bash
mas-ctl chat agent.yaml -q "hello" --events --events-file logs/events.jsonl
```

`events.jsonl` is the machine-readable run log. See [observability](observability.md).

### Save and resume a session

```bash
mas-ctl chat agent.yaml -i --checkpoint-dir ckpt --save-checkpoint
mas-ctl checkpoint list ckpt
mas-ctl checkpoint resume ckpt/<file>.json
```

`--save-checkpoint` writes one checkpoint after each turn.

### Serve an agent over A2A

```bash
mas-ctl serve agent.yaml --infra-ref infra/a2a-qa.yaml
```

The infra bundle must declare an Application endpoint for the agent with `usage: deploy`.

### Control a running session

```bash
mas-ctl control attach SESSION
mas-ctl control steer SESSION --text "be brief" --after
```

`SESSION` is the id printed when the session starts. See [`mas-ctl control`](#mas-ctl-control).

## Global flags

These flags go before the subcommand:

```bash
mas-ctl [-v] [--env FILE] COMMAND …
```

| Flag | Default | Description |
| --- | --- | --- |
| `-v` / `--verbose` | off | Repeatable. `-v` adds session hints and internal logs (cache hits, summarization). `-vv` also enables `--trace-engine` when tracing (`chat`, `run-mas`). |
| `--env FILE` | `.env` in the current directory and next to the manifest | Load this dotenv file. The file must exist. |

## Outputs

| Destination | What | Enable |
| --- | --- | --- |
| stdout | The conversation (`You:` and `Agent:`) | Always, for `chat` and `run-mas`. |
| stderr | The exchange log: human-readable AGENT, LLM, TOOL, and GOV hops | `--trace` or `mas_ctl.trace`. |
| stderr | `note: LLM response served from cache` | `-v` (cache hits and other internals; not on by default). |
| `events.jsonl` | The machine run log | `--events`, an overlay, or the manifest. See [observability.md](observability.md). |

Stdout always stays the conversation. Color is off unless you pass `--trace-color`.

### Exchange-log precedence

The first match wins:

1. CLI flags (`--trace`, `--no-trace`, `--trace-color`, and so on).
2. Workspace `config.yaml`, key `mas_ctl:`.
3. User `$XDG_CONFIG_HOME/mas/config.yaml`, key `mas_ctl:`.
4. Built-in default: off until `--trace` or config enables it. Then it prints a summary with timestamps and no color.

## `mas-ctl chat [MANIFEST]`

`chat` runs an interactive or scripted single-agent session. `MANIFEST` defaults to `agent.yaml`, found in the current directory or the workspace.

```bash
mas-ctl chat agent.yaml -q "What is 2+2?"
mas-ctl chat agent.yaml -i -o overlays/tools.yaml --trace
```

### Session

| Flag | Default | Description |
| --- | --- | --- |
| `-p` / `--prompt TEXT` | none | First user turn. |
| `-q` / `--query TEXT` | none | Extra turn. Repeatable. |
| `-i` / `--interactive`, `-I` / `--no-interactive` | on in a terminal with no scripted turns | Open a multi-turn prompt. |
| `--single-turn` | off | Exit after the first agent reply. |

### Manifest and overlays

| Flag | Default | Description |
| --- | --- | --- |
| `-o` / `--overlay PATH` | none | Overlay YAML. Repeatable. |
| `--tool NAME` | none | Inline overlay: add a tool by name. |
| `--skill NAME` | none | Inline overlay: add a skill by name. |
| `--memory ID` | none | Inline overlay: set the memory backend id. |
| `--set KEY=VALUE` | none | Inline overlay: set a `spec.context` value. |
| `--override PATH=VALUE` | none | Schema-backed session-tree patch. Applies last. |
| `--connect NAME=URI` | none | Shortcut for infra `usage: use` rows. Expands to `--override infra:…`. `--bind` is the hidden old name. |
| `--pattern ID` | manifest | Design-pattern plugin id. |
| `--flavour NAME` | `local` | Flavour from library-standard. |
| `--infra-ref REF` | workspace or user | Infra bundle. Repeatable. Wins over `config.yaml`. Deployed A2A endpoints for this agent bind in-process for interrogation. |
| `--runtime-ref REF` | workspace or user | `RuntimeEngine` ref. Repeatable. |
| `--model ID` | `spec.models`, `MAS_CTL_MODEL`, or `MAS_LLM_MODEL` | Force the engine model for this run. |
| `--max-tokens N` | none sent | Alias of `--override '.agent.spec.models[*].max_tokens=N'`. Infra `generation.max_output_tokens` bounds it. See [Output-token limits](../manifests/agent.md#output-token-limits). |
| `--no-validate` | off | Skip schema checks for seeds and checkpoints. |

How the patching flags work:

- `--override` uses the same overlay merge engine as `-o`. Paths start at the session tree (`.mas`, `.infra`, `.agent`) so the target document is explicit.
- `.mas.agent[reviewer]` is Overlay `target.kind: Agent` plus `target.name`. It applies to `{id, ref}` rows and to inline specs. See [Agents in a MAS](overrides.md#agents-in-a-mas).
- CLI patches outrank file overlays.
- `--tool`, `--skill`, `--memory`, and `--set` remain supported.
- `--connect NAME=URI` writes the same Application and ToolServerRegistry fields as `--infra-ref` YAML. See [Connect](overrides.md#connect). `--bind` is the hidden old name.
- An overlay file can group assignments under `spec.overrides`. They run after that file's `spec.patch`, in list order. See the [overlay manifest](../manifests/overlay.md).

### Cache, stream, envelope

| Flag | Default | Description |
| --- | --- | --- |
| `--cache-read` / `--no-cache-read` | RuntimeEngine, true | Look up the cache before the LLM call. |
| `--cache-write` / `--no-cache-write` | RuntimeEngine, true | Persist the result after the LLM call. |
| `--cache-read-only` | off | Exclusive replay: answer from the cache only, never write, fail at once on an unrecorded prompt; needs no API key. `mas_ctl.cache: read-only` in `config.yaml` makes it the workspace default. |
| `--stream` / `--no-stream` | RuntimeEngine, false | Stream responses over SSE. |
| `--without-obs` | off | Disable the observability summand (`M_obs`) and event recording. |
| `--without-gov` | off | Disable the governance summand (`M_gov`) and HITL chokepoints. |

### Checkpoints and memory seed

| Flag | Default | Description |
| --- | --- | --- |
| `--memory-seed PATH` | none | Seed working and persistent memory. |
| `--checkpoint-dir PATH` | none | Directory for checkpoints. |
| `--load-checkpoint PATH` | none | Restore a checkpoint. |
| `--save-checkpoint` / `--no-save-checkpoint` | off | Save a checkpoint after each turn. |

### Control attach

| Flag | Default | Description |
| --- | --- | --- |
| `--control-dir PATH` | `$XDG_RUNTIME_DIR/mas-ctl`, else `/var/run/mas-ctl`, else a temp dir | Advertise control so `mas-ctl control attach` can find the session id. |
| `--no-control` | off | Do not advertise a control endpoint. |
| `--debug-script` | none | gdb-like script: `@file` or inline (runtime plugin `spec.debug`). |

### Exchange log

`chat` and `run-mas` share these flags. `tui` does not have them, because its curses UI has its own pane.

| Flag | Default when tracing | Description |
| --- | --- | --- |
| `--trace` / `--trace summary` | none | Print headers and timestamps. AGENT to USER text is untruncated. |
| `--trace full` / `--trace-full` | none | Dump full payloads (the legacy `--trace` behaviour). |
| `--trace-summary` | none | Alias for `--trace` and `--trace summary`. |
| `--no-trace` | none | Force it off, even if `config.yaml` sets `mas_ctl.trace`. |
| `--trace-timestamps` / `--no-trace-timestamps` | on | Show UTC time and elapsed time per exchange. |
| `--trace-color` / `--no-trace-color` | off | Use ANSI color. `--trace` never turns it on. |
| `--trace-engine` | off (on with `-vv`) | Print raw `InvokeEngineIo` and `EngineIoReturn` JSON. |

Set defaults in the workspace or user `config.yaml`:

```yaml
mas_ctl:
  trace: summary          # off | summary | full  (true = summary)
  # trace_timestamps: true
  # trace_color: false
  # trace_engine: false
```

Field reference: [config.yaml](../references/config.yaml.md#mas_ctl).

### `events.jsonl` flags

`chat`, `run-mas`, and `tui` share these flags. Details: [observability.md](observability.md).

| Flag | Default | Description |
| --- | --- | --- |
| `--events` / `--no-events` | manifest | Force native observability on or off. |
| `--events-file PATH` | `traces/events.jsonl` | JSONL output path. |
| `--events-stderr` / `--events-stdout` | off | Also stream JSONL on stderr. `--events-stdout` is a legacy alias. |
| `--events-format` | manifest | One of `native`, `boundary`, `both`, `otel`. |

### Interactive commands

Type these at the `You:` prompt. They are also listed in `mas-ctl chat --help`.

| Input | Description |
| --- | --- |
| `/quit`, `/exit`, `/q` | End the session. |
| `/reset` | Clear working memory and turn history. |
| `/steer TEXT` | Inject operator steering. |
| `/skills`, `/skill NAME` | List skills, or activate one. |
| `/backtrack [N] [TEXT]` | Restore the checkpoint N steps back (default 1), with an optional steering note. Needs `--checkpoint-dir`. |

With `spec.governance.hitl_on_tool` set, interactive mode prompts SCHEDULE, BLOCK, or SKIP on stderr. Batch `-q` runs auto-approve unless the overlay sets `hitl_mode: auto-deny`.

## `mas-ctl run-mas [MANIFEST]`

`run-mas` composes the MAS, materializes it, and opens a session on the entry agent. `MANIFEST` defaults to `mas.yaml`. A2A Application endpoints on in-process agents bind for interrogation: `usage: deploy` on the entry agent, or on specialists that are not also `use`. The conversation stays on stdin.

```bash
mas-ctl run-mas mas.yaml -q "Plan a trip from Celestia to Verdantia" --trace
```

| Flag | Default | Description |
| --- | --- | --- |
| `-p` / `--prompt TEXT` | none | First user turn. |
| `-q` / `--query TEXT` | none | Extra turn. Repeatable. |
| `-o` / `--overlay PATH` | none | Overlay YAML. Repeatable. |
| `--override PATH=VALUE` | none | Schema-backed MAS, infra, or flavour patch. Applies last. |
| `--connect NAME=URI` | none | Shortcut for infra `usage: use` rows. Expands to `--override infra:…`. `--bind` is the hidden old name. |
| `-d` / `--deployment PATH` | workspace `mas_ctl.deployment` | Deployment manifest. |
| `--flavour NAME` | `local` | Flavour. |
| `--infra-ref REF` | none | Infra bundle. Repeatable. |
| `--kernel ID` | `mas-runtime-py` | Runtime id. It is the only value today. |
| `-i` / `--interactive` | off | Open an interactive session. |
| `--auto-hitl` / `--no-auto-hitl` | auto-hitl on | Auto-resolve human-in-the-loop (HITL) gates in batch mode. `--no-auto-hitl` uses the OperatorConsole. |
| `--single-turn` | off | Exit after the first reply. |
| `--no-validate` | off | Skip manifest validation. |

`run-mas` also takes the `--trace*` and `--events*` flags above.

## `mas-ctl serve AGENT`

`serve` runs a dedicated, blocking A2A process (or another `agent_expose` target). It does not return until the listener stops.

It needs an Application endpoint named after the agent's `metadata.name`, with `usage: deploy` or `use-and-deploy`. Declare it in workspace `infra_refs` or pass `--infra-ref`. It uses the same helper as `chat`, `tui`, and `run-mas` (`mas.ctl.session.exposure`).

```bash
mas-ctl serve agent.yaml --infra-ref infra/a2a-qa.yaml
```

The same without an infra file: `--listen NAME=a2a://host:port` is the shortcut for the
`usage: deploy` Application row, the mirror of `--connect` (which *uses* a remote peer).

```bash
mas-ctl serve agent.yaml --listen "qa-agent=a2a://127.0.0.1:9006"
```

| Flag | Default | Description |
| --- | --- | --- |
| `-o` / `--overlay PATH` | none | Overlay YAML. Repeatable. |
| `--override PATH=VALUE` | none | Schema-backed session-tree patch. Applies last. |
| `--connect NAME=URI` | none | Shortcut for infra `usage: use` rows. Expands to `--override infra:…`. `--bind` is the hidden old name. |
| `--listen NAME=a2a://HOST:PORT` | none | Shortcut for the agent's own Application endpoint with `usage: deploy`. Expands to `--override infra:…`. |
| `--infra-ref REF` | workspace or user | Infra bundle. It must include the agent's deployed Application endpoint. |
| `--cache-read-only` | off | Exclusive replay (see `chat`): answer from the cache only, no API key needed. |
| `--flavour NAME` | `local` | Flavour. |
| `--control-dir PATH` | `$XDG_RUNTIME_DIR/mas-ctl`, else `/var/run/mas-ctl`, else a temp dir | Advertise control so `mas-ctl control attach` can find the A2A `contextId`. |
| `--no-control` | off | Do not advertise a control endpoint. |

## `mas-ctl tui [MANIFEST]`

`tui` opens a curses UI. It uses the same bootstrap as `chat`: overlays, infra, HITL, and `--events*`. Deployed A2A endpoints for the in-process agent bind for interrogation. It has no `--trace`, because the TUI renders exchanges itself. Guide: [ctl/tui.md](../ctl/tui.md).

| Flag | Default | Description |
| --- | --- | --- |
| `-o` / `--overlay PATH` | none | Overlay YAML. Repeatable. |
| `--override PATH=VALUE` | none | Schema-backed session-tree patch. Applies last. |
| `--connect NAME=URI` | none | Shortcut for infra `usage: use` rows. Expands to `--override infra:…`. `--bind` is the hidden old name. |
| `--pattern ID` | manifest | Design-pattern plugin id. |
| `--flavour NAME` | `local` | Flavour. |
| `--single-turn` | off | Exit after the first reply. |
| `--memory-seed PATH` | none | Memory seed. |
| `--infra-ref` / `--infra REF` | none | Infra bundle. Repeatable. |
| `--runtime-ref REF` | none | `RuntimeEngine` ref. Repeatable. |
| `--no-validate` | off | Skip schema checks. |
| `--model ID` | `spec.models`, `MAS_CTL_MODEL`, or `MAS_LLM_MODEL` | Force the engine model. |
| `--max-tokens N` | none sent | Alias of `--override '.agent.spec.models[*].max_tokens=N'`. |

`tui` also takes the `--events*` flags.

## Other commands

| Command | Purpose | Typical flags |
| --- | --- | --- |
| `mas-ctl validate PATH …` | Schema-check manifests. | `-k/--kind`, `--strict/--no-strict`, `--no-validate`, `--resolve-refs/--no-resolve-refs`, `-o/--overlay` |
| `mas-ctl schemas` | List registered schema kinds. | none |
| `mas-ctl compile MANIFEST` | Print the resolved Agent or MAS spec after overlays and defaults. See [compile.md](compile.md). | `-o/--overlay`, `--override`, `-O/--output`, `--layout auto\|tree\|bundle`, `--no-defaults`, `--no-validate`, `--no-header` |
| `mas-ctl compose MANIFEST` | Print the effective bind and placement plan. | `-d/--deployment`, `-o/--overlay`, `--override`, `--connect`, `--infra-ref`, `--runtime-ref`, `--kernel`, `-O/--output`, `--no-validate` |
| `mas-ctl plan MANIFEST` | Print the placement plan only. | `-d/--deployment`, `--kernel`, `--no-validate` |
| `mas-ctl flavour list` | List flavour manifests from installed libraries. Deprecated: prefer `deployment/v1`. | none |
| `mas-ctl infra list` | List infra bundles. Alias: `mas-ctl list-bundles`. | `-v` |
| `mas-ctl list-bundles` | List infra bundles from installed libraries. | `-v` |
| `mas-ctl registry list`, `registry query PREFIX` | List registered runtime machines, ctl registries, and schema kinds. | `query`: `-l/--layer`, `--json` |
| `mas-ctl plugin list`, `doctor [MANIFEST …]`, `enable URN` | Show plugin availability from `library.yaml` `plugins:`. `enable` installs the missing extra. | `list`: `--type`, `--json`. `doctor`: `--json`. `enable`: `--dry-run`. |
| `mas-ctl checkpoint list DIR` | List checkpoint files. | none |
| `mas-ctl checkpoint show PATH` | Print one checkpoint as JSON. | none |
| `mas-ctl checkpoint fork PATH` | Create a new v2 checkpoint branch. The source file stays untouched. | `--output-dir`, `--session-id` |
| `mas-ctl checkpoint resume PATH` | Resume from a checkpoint, using its embedded manifest or the CLI chat setup. | none |

### `mas-ctl control`

`control` attaches to a running `chat` or `serve` session by its session id. The session id is the A2A `contextId`, printed when the session starts.

```bash
mas-ctl control attach SESSION                 # print inspect() view
mas-ctl control pause SESSION
mas-ctl control steer SESSION --text "be brief" --after
mas-ctl control SESSION pause persist --auto-stop   # chained verbs (script form)
```

| Part | Description |
| --- | --- |
| `-d` / `--directory DIR` | Goes before the verb. Default: `$MAS_CONTROL_DIR`, else `$XDG_RUNTIME_DIR/mas-ctl`, else `/var/run/mas-ctl`, else a temp dir. |
| `attach`, `inspect` | Print the session view. |
| `pause [--reason]`, `resume` | Pause or unpause a live session. |
| `snapshot [--label] [--auto-stop]` | Snapshot the session. It needs a stopped session, or pass `--auto-stop` to pause first. |
| `persist [--snapshot-id] [--label] [--auto-stop]` | Same stop rule as `snapshot`. Writes a checkpoint file for `mas-ctl chat --load-checkpoint`. |
| `steer --text TEXT [--replace \| --after] [--at tail\|head\|N]` | By default, preempt the current generation. `--replace` discards streamed tokens. `--after` (alias `--enqueue`) waits for the current generation. |
| `checkpoints` | List the session's checkpoints. |
| `script SESSION [VERBS…]` | Run the same verbs as a chain, with `-e TEXT` (repeatable), `-f FILE`, or `--data @file`. All are equivalent. `mas-ctl control SESSION VERBS…` is shorthand for `script`. |
