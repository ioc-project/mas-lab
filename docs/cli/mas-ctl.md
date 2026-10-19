<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# `mas-ctl` command-line reference

Complete flags for **`mas-ctl`**. `mas-ctl COMMAND --help` is always authoritative
if this page and the code diverge.

Related: [CLI overview](index.md) · [config.yaml](../references/config.yaml.md) ·
[observability (`events.jsonl`)](observability.md) · [TUI](../ctl/tui.md).

General override reference: [CLI overrides](overrides.md).

---

## Global flags

Apply before the subcommand:

```bash
mas-ctl [-v] [--env FILE] COMMAND …
```

| Flag | Default | Effect |
|------|---------|--------|
| `-v` / `--verbose` | off | Repeatable. `-v` session hints / extra logs; `-vv` also enables `--trace-engine` when tracing |
| `--env FILE` | workspace `mas_ctl.env` (default `.env`) | Load that dotenv file |

---

## Outputs

| Destination | What | Enable |
| ------------- | ------ | -------- |
| **stdout** | Conversation (`You:` / `Agent:`) | Always for `chat` / `run-mas` |
| **stderr** | Human exchange log (AGENT↔LLM↔TOOL↔GOV) | `--trace` or `mas_ctl.trace` |
| **`events.jsonl`** | Machine run log | `--events` / overlay / manifest — [observability.md](observability.md) |

Stdout stays the conversation. Color is never the default (`--trace-color`).

### Precedence (exchange log)

1. CLI (`--trace`, `--no-trace`, `--trace-color`, …)
2. Workspace `config.yaml` `mas_ctl:`
3. User `$XDG_CONFIG_HOME/mas/config.yaml` `mas_ctl:`
4. Built-in: off until `--trace` or config enables it; then **summary + timestamps**, color off

---

## `mas-ctl chat [MANIFEST]`

Interactive or scripted single-agent session. Manifest defaults to `agent.yaml`
when omitted (cwd / workspace walk).

```bash
mas-ctl chat agent.yaml -q "What is 2+2?"
mas-ctl chat agent.yaml -i -o overlays/tools.yaml --trace
```

### Session

| Flag | Default | Effect |
| ------ | --------- | -------- |
| `-p` / `--prompt TEXT` | — | First user turn |
| `-q` / `--query TEXT` | — | Extra turn(s); repeatable |
| `-i` / `--interactive` · `-I` / `--no-interactive` | TTY and no scripted turns | Multi-turn REPL |
| `--single-turn` | off | Exit after the first agent reply |

### Manifest / overlays

| Flag | Default | Effect |
| ------ | --------- | -------- |
| `-o` / `--overlay PATH` | — | Overlay YAML; repeatable |
| `--tool NAME` | — | Inline overlay: add a tool by name |
| `--skill NAME` | — | Inline overlay: add a skill by name |
| `--memory ID` | — | Inline overlay: memory backend id |
| `--set KEY=VALUE` | — | Inline overlay: `spec.context` |
| `--override ROOT:PATH=VALUE` | — | Schema-backed overlay patch; applied last |
| `--bind NAME=URI` | — | Shortcut for infra Application / ToolServerRegistry `usage: use` (expands to `--override infra:…`) |
| `--pattern ID` | manifest | Design-pattern plugin id |
| `--flavour NAME` | `local` | Flavour from library-standard |
| `--infra-ref REF` | workspace / user | Infra bundle; repeatable; wins over `config.yaml`. Deployed A2A endpoints for this agent bind in-process for interrogation. |
| `--runtime-ref REF` | workspace / user | `RuntimeEngine` ref; repeatable |
| `--model ID` | spec.models / `MAS_CTL_MODEL` / `MAS_LLM_MODEL` | Force the engine model for this run |
| `--max-tokens N` | none sent | Alias of `--override 'agent:spec.models[*].max_tokens=N'`; bounded by infra `generation.max_output_tokens` — [Output-token limits](../manifests/agent.md#output-token-limits) |
| `--no-validate` | off | Skip schema checks for seeds/checkpoints |

`--override` uses the same canonical Overlay merge engine as `-o`. Paths are
root-qualified so the target document is explicit:

```bash
mas-ctl chat agent.yaml --override 'agent:spec.context.role="security expert"'
mas-ctl run-mas mas.yaml \
  --override 'mas:spec.agency.agents[id=reviewer].spec.memory=long'
```

Selectors support numeric indexes, identity selectors such as `[id=reviewer]`,
and `[*]` wildcards. CLI patches have higher priority than file overlays.
Existing `--tool`, `--skill`, `--memory`, and `--set` flags remain supported.
`--bind NAME=URI` is a shortcut for the same Application / ToolServerRegistry
fields as `--infra-ref` YAML: it expands into `--override infra:…` and uses
that merge path (see [CLI override reference — Bind](overrides.md#bind)).
An overlay file may group the same assignments under `spec.overrides`; those
assignments run after that file's regular `spec.patch` in list order. See
[CLI override reference](overrides.md) and [Overlay manifest](../manifests/overlay.md).

### Cache, stream, envelope

| Flag | Default | Effect |
| ------ | --------- | -------- |
| `--cache-read` / `--no-cache-read` | RuntimeEngine / true | Lookup before the LLM call |
| `--cache-write` / `--no-cache-write` | RuntimeEngine / true | Persist after the LLM call |
| `--stream` / `--no-stream` | RuntimeEngine / false | SSE streaming |
| `--without-obs` | off | Disable observability summand (`M_obs`) and event recording |
| `--without-gov` | off | Disable governance summand (`M_gov`) and HITL chokepoints |

### Checkpoints / memory seed

| Flag | Default | Effect |
| ------ | --------- | -------- |
| `--memory-seed PATH` | — | Seed working / persistent memory |
| `--checkpoint-dir PATH` | — | Checkpoint directory |
| `--load-checkpoint PATH` | — | Restore a checkpoint |
| `--save-checkpoint` / `--no-save-checkpoint` | off | Save after each turn |

### Control attach

| Flag | Default | Effect |
|------|---------|--------|
| `--control-dir PATH` | `$XDG_RUNTIME_DIR/mas-ctl`, else `/var/run/mas-ctl`, else temp | Advertise control so `mas-ctl control attach` can use the session id |
| `--no-control` | off | Do not advertise a control endpoint |
| `--debug-script` | — | gdb-like script: `@file` or inline (runtime plugin `spec.debug`) |

### Exchange log

Shared with `run-mas`. **Not** on `tui` (curses has its own pane).

| Flag | Default when tracing | Effect |
| ------ | ---------------------- | -------- |
| `--trace` / `--trace summary` | — | Enable human summary: headers + timestamps; untruncated AGENT→USER |
| `--trace full` / `--trace-full` | — | Verbose payload dump (legacy `--trace` behaviour) |
| `--trace-summary` | — | Alias for `--trace` / `--trace summary` |
| `--no-trace` | — | Off even if `config.yaml` sets `mas_ctl.trace` |
| `--trace-timestamps` / `--no-trace-timestamps` | timestamps **on** | UTC + elapsed per exchange |
| `--trace-color` / `--no-trace-color` | **off** | ANSI color; never implied by `--trace` |
| `--trace-engine` | off (also `-vv`) | Raw `InvokeEngineIo` / `EngineIoReturn` JSON |

Workspace / user `config.yaml`:

```yaml
mas_ctl:
  trace: summary          # off | summary | full  (true = summary)
  # trace_timestamps: true
  # trace_color: false
  # trace_engine: false
```

Field reference: [config.yaml](../references/config.yaml.md#mas_ctl).

### `events.jsonl` (`--events*`)

Shared with `run-mas` and `tui`. Full semantics: [observability.md](observability.md).

| Flag | Effect |
| ------ | -------- |
| `--events` / `--no-events` | Force native observability on or off |
| `--events-file PATH` | JSONL path (default `traces/events.jsonl`) |
| `--events-stderr` / `--events-stdout` | Also stream JSONL on **stderr** (`--events-stdout` is a legacy alias) |
| `--events-format` | `native` · `boundary` · `both` · `otel` |

---

## `mas-ctl run-mas [MANIFEST]`

Compose → materialize → session on the MAS entry agent. Manifest defaults to
`mas.yaml`. Deployed A2A Application endpoints on in-process agents
(`usage: deploy` on the entry agent, or on specialists that are not also
`use`) bind for interrogation while the conversation stays on stdin.

```bash
mas-ctl run-mas mas.yaml -q "Plan a trip from Celestia to Verdantia" --trace
```

| Flag | Default | Effect |
| ------ | --------- | -------- |
| `-p` / `--prompt TEXT` | — | First user turn |
| `-q` / `--query TEXT` | — | Extra turn(s); repeatable |
| `-o` / `--overlay PATH` | — | Overlay YAML; repeatable |
| `--override ROOT:PATH=VALUE` | — | Schema-backed MAS / infra / flavour patch; applied last |
| `--bind NAME=URI` | — | Shortcut for infra Application / ToolServerRegistry `usage: use` (expands to `--override infra:…`) |
| `-d` / `--deployment PATH` | workspace `mas_ctl.deployment` | Deployment manifest |
| `--flavour NAME` | `local` | Flavour |
| `--infra-ref REF` | — | Infra bundle; repeatable |
| `--kernel ID` | package default | Runtime / kernel id |
| `-i` / `--interactive` | off | Interactive session |
| `--auto-hitl` / `--no-auto-hitl` | auto-hitl **on** | Batch auto-resolve HITL; `--no-auto-hitl` uses OperatorConsole |
| `--single-turn` | off | Exit after first reply |
| `--no-validate` | off | Skip manifest validation |

Plus the shared **`--trace*`** and **`--events*`** tables above.

---

## `mas-ctl serve AGENT`

Dedicated blocking A2A (or other `agent_expose`) process. Requires an
Application endpoint named after the agent `metadata.name` with
`usage: deploy` or `use-and-deploy` in workspace `infra_refs` or `--infra-ref`.
Same helper as `chat` / `tui` / `run-mas` (`mas.ctl.session.exposure`); this
command does not return until the listener stops.

```bash
mas-ctl serve agent.yaml --infra-ref infra/a2a-qa.yaml
```

| Flag | Default | Effect |
|------|---------|--------|
| `-o` / `--overlay PATH` | — | Overlay YAML; repeatable |
| `--override ROOT:PATH=VALUE` | — | Schema-backed overlay patch; applied last |
| `--bind NAME=URI` | — | Shortcut for infra Application / ToolServerRegistry `usage: use` (expands to `--override infra:…`) |
| `--infra-ref REF` | workspace / user | Must include the agent's deployed Application endpoint |
| `--flavour NAME` | `local` | Flavour |
| `--control-dir PATH` | `$XDG_RUNTIME_DIR/mas-ctl`, else `/var/run/mas-ctl`, else temp | Advertise control so `mas-ctl control attach` can use the A2A `contextId` |
| `--no-control` | off | Do not advertise a control endpoint |

---

## `mas-ctl tui [MANIFEST]`

Curses UI with the same bootstrap as `chat` (overlays, infra, HITL, `--events*`).
Deployed A2A endpoints for the in-process agent bind for interrogation, same
helper as `chat`. No `--trace` (the TUI renders exchanges itself).

| Flag | Default | Effect |
| ------ | --------- | -------- |
| `-o` / `--overlay PATH` | — | Overlay YAML; repeatable |
| `--override ROOT:PATH=VALUE` | — | Schema-backed overlay patch; applied last |
| `--bind NAME=URI` | — | Shortcut for infra Application / ToolServerRegistry `usage: use` (expands to `--override infra:…`) |
| `--pattern ID` | manifest | Design-pattern plugin id |
| `--flavour NAME` | `local` | Flavour |
| `--single-turn` | off | Exit after first reply |
| `--memory-seed PATH` | — | Memory seed |
| `--infra-ref` / `--infra REF` | — | Infra bundle; repeatable |
| `--runtime-ref REF` | — | RuntimeEngine ref; repeatable |
| `--no-validate` | off | Skip schema checks |
| `--model ID` | spec.models / `MAS_CTL_MODEL` / `MAS_LLM_MODEL` | Force the engine model |
| `--max-tokens N` | none sent | Alias of `--override 'agent:spec.models[*].max_tokens=N'` |

Plus **`--events*`**. Guide: [ctl/tui.md](../ctl/tui.md).

---

## Other commands

| Command | Purpose | Typical flags |
| --------- | --------- | --------------- |
| `mas-ctl validate PATH …` | Schema-check manifests | `-k/--kind`, `--strict/--no-strict`, `--no-validate`, `--resolve-refs/--no-resolve-refs`, `-o/--overlay` |
| `mas-ctl schemas` | List bundled JSON/YAML schemas | — |
| `mas-ctl compose MANIFEST` | Effective bind + placement plan | `-d/--deployment`, `-o/--overlay`, `--override`, `--bind`, `--infra-ref`, `--runtime-ref`, `--kernel`, `-O/--output`, `--no-validate` |
| `mas-ctl plan MANIFEST` | Placement plan only | `-d/--deployment`, `--kernel`, `--no-validate` |
| `mas-ctl flavour list` | Flavours from installed libraries | — |
| `mas-ctl infra list` | Infra bundles | `-v` |
| `mas-ctl list-bundles` | All library bundles | — |
| `mas-ctl registry …` | Plugin registry introspection | `mas-ctl registry --help` |
| `mas-ctl checkpoint list DIR` | List checkpoint files | — |
| `mas-ctl checkpoint show PATH` | Print one checkpoint JSON | — |
| `mas-ctl control attach SESSION` | Attach by session id (A2A `contextId`) | `-d/--directory` (default runtime dir). Verbs: `pause`, `resume` (unpause live), `snapshot`/`persist` (require stopped session or `--auto-stop`), `steer`, `checkpoints`, `inspect`. Chain: `mas-ctl control SESSION pause persist --auto-stop`. Scripts: `--data @file`, `-e`, `-f` (equivalent). |

---

## Interactive `chat` commands

At the `You:` prompt (see `mas-ctl chat --help` epilog):

| Input | Effect |
| ------- | -------- |
| `/quit` `/exit` `/q` | End the session |
| `/reset` | Clear working memory and turn history |
| `/steer TEXT` | Inject operator steering |
| `/skills` · `/skill NAME` | List / activate skills |

HITL (when `spec.governance.hitl_on_tool` is set): interactive mode prompts
SCHEDULE / BLOCK / SKIP on stderr. Batch `-q` auto-approves unless the overlay
sets `hitl_mode: auto-deny`.
