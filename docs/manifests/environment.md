<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Environment

**Package:** `lab/components/bench` · **Schema:** `fragments/environment.schema.yaml`

An **environment** is the world around a run: fixtures, a clock, timers,
triggers, and world actions. It is declared as `environment:` on the
experiment, a scenario, or a dataset item. Blocks merge experiment →
scenario → item. Item blocks cannot declare fixtures.

The kernel does not know about the world. `environment@v1` is a governance
observer: it always ALLOWs. When a trigger matches, the world action runs
before the kernel continues.

This is an overlay of the same key at three layers, not a new kind and not
a replacement for memory seeds, tool fixtures, pipeline `service_start`, or
gdb. See [Not a replacement](#not-a-replacement).

## Where it is declared

| Layer | YAML | Holds |
| --- | --- | --- |
| Experiment | `experiment.environment` | Fixtures, clock kind, shared timers and triggers |
| Scenario | `scenarios[].environment` | Overlay of the experiment block |
| Dataset item | `spec.items[].environment` | Per-case timers and triggers only |

Merge order is experiment → scenario → item. `triggers`, `timers`, and
`fixtures` concatenate (an item still cannot add fixtures). Nested mappings
such as `clock` overlay: the item wins on a key both sides set.
`allow_network` unions host names.

The item key is a sibling of `inputs`, not inside it — the same place as on
the experiment. Schema fragment
`urn:mas-lab:fragments:environment:v1` is the full block;
`urn:mas-lab:fragments:environment-item:v1` is that block without
`fixtures`, used by `dataset.schema.yaml` and `run-input.schema.yaml`.

Worked split: [Tutorial 15](../tutorials/15-world-events-and-fixtures/README.md)
(`labs/revocation.lab/`). Dataset field: [dataset.md §10](dataset.md#10-world-around-the-item-environment).

## Fields

| Key | Where | Meaning |
| --- | --- | --- |
| `clock.kind` | any | `wall` (default) or `virtual`. Virtual time moves only on `clock_advance` and optional `clock.tick`. |
| `timers` | any | Time conditions compiled as triggers with `when.on: timer`. |
| `triggers` | any | `when` → `do` lists. Structured YAML or a gdb `break` line. |
| `fixtures` | experiment / scenario | Paired start/stop. `kind: process` (own process group), `service`, or `step`. |
| `limits` | any | `max_sleep_s` (60), `action_timeout_s` (30), `max_events_per_run`. |
| `allow_network` | any | Extra hosts for `http_call`. Loopback is always allowed. |
| `actions` | any | Named do-lists. gdb `env NAME` runs one. |
| `script` | any | gdb script attached on the generated Control. |
| `replay` | any | `live` (default) or `recorded`. Recorded re-delivers D3 events at their anchors. |

## Fixtures

A fixture is one object. Start and stop are implied.

```yaml
environment:
  fixtures:
    - name: issuer
      kind: process
      command: [python3, fixtures/issuer.py, --port, "{port}"]
      port: auto
      health: {path: /health, timeout_s: 10}
      clock: {path: /clock}
```

`port: auto` binds 127.0.0.1:0, reads the port, and passes it. Ports never
enter the trace-cache hash; the command does. The process is a session
leader (`start_new_session`). Stop = SIGTERM to the group, wait
`stop_grace_s`, then SIGKILL. Pidfiles live under
`<output>/.environment/fixtures/<scope-key>/`. Two runs that both declare
`scope: run` get two processes, even when the fixture name is the same.
A leftover whose command digest still matches is reaped on the next
`benchmark run`.

The fixture library reads no environment variable itself: `mas-lab benchmark run` sends the lab child allowlist of its shell with the run (`fixture_env`; it is not shown in the worker listing). A process fixture gets that allowlist (`PATH`, `HOME`, locale, temp dirs, `TERM`, the CA and proxy variables, and a few `XDG_*`, `MAS_*` and Python variables), plus its declared `env` and `passthrough`; every other host variable stays out. `env: {NAME: value}` sets one; a value may be `{secret: NAME}`, revealed into this fixture's environment only when it starts. `passthrough: [NAME]` copies one key that is in the allowlist. `env_mode: minimal` narrows the base: only `PATH`, `HOME`, `LANG`, `LC_*`, `TMPDIR`, `TERM`, `USER`, `TZ` and `SYSTEMROOT` come along, plus `passthrough` and `env`. Health and clock probes and `http_call` to a loopback host never go through a proxy from
the environment (`http_proxy`). `health.path` and `clock.path` get a leading slash if written
without. A scope key never decides a path outside the fixture directory.

Output of a process fixture goes to `fixture.log` in its data directory;
the start error quotes the tail of it when the process exits early. With a
`health:` probe, `fixture.ready` is recorded only once the probe returns
`expect_status`; without one, a fixture is ready as soon as it is spawned.
A start that fails (probe timeout, early exit) leaves no process and no
pidfile behind. `fixture_stop` then `fixture_restart` brings the fixture
back on the same address, so urls already handed out stay valid, and the
holders of the fixture keep their references. A virtual-clock push reaches
only the clock-bound fixtures of the run that advanced the clock.

A fixture with `port: auto` whose process exits at once is started once more on a
new port, so a port taken between "free" and "bound" does not fail the run. A fixed port is
never retried.

`fixture_stop` and `fixture_restart` act on this run's instance of the fixture, never on
another run's of the same name. The cache key covers the files a fixture command names when
they sit inside the experiment, so editing a fixture script re-runs instead of serving a stale
trace, also without `--force` when the run folder still links to the old trace. Every environment is compiled by `mas-lab validate`, by `--dry-run` and before the first run
starts, so a bad block is reported once, with the scenario and item. Environment steps act on the first session bound to the run; a step can name another
with `session:` (a session id or the entry agent of a bound session), and an unknown name is
an error, not a fallback.

`kind: step` runs the declared `start` and `stop` environment steps
in-process. Both are required.

`service_start` uses the same batch-scoped ServiceManager as `service_stop`.
A process service is stopped at the end of its level unless
`keep_running: true`. That pair is pipeline infra, not an environment
fixture.

A fixture named `scripted-model` is also bound as LLMProxy `openai-direct`
for that run (`api_base` the loopback `/v1`, keyless: `api_key` names the optional secret `lab`, allowed
`scripted` and `any`). Pin `experiment.models.main` to a concrete id
(`scripted` in the example lab). If the agent still says `model: any` and
another LLMProxy leaf is already on the flavour, pin the agent too —
two leaves cannot both claim `any`.

## Clock

| | Wall | Virtual |
| --- | --- | --- |
| Source | real time since the environment opened | a float the environment owns |
| Advance | by itself | `clock_advance` and optional `tick` |
| `sleep` | allowed, bounded | error — use `clock_advance` |
| Determinism | recorded only (D3) | same `environment.jsonl` after dropping `wall` and `elapsed_ms` (D2) |

## Triggers

```yaml
triggers:
  - id: revoke-after-2
    when: {"on": tool_result, tool: pay_fare, occurrence: 2}
    do:
      - type: http_call
        config: {method: POST, fixture: issuer, path: /admin/revoke, expect_status: 200}
```

The same `when` as a gdb line: `when: "break tool_result pay_fare if 2"`.
Occurrence counts per run, narrowed by `agent:` when set. A non-repeat
trigger fires at most once; the first later match is recorded as
`trigger.skipped`, once per trigger. An `event:` name is folded to `user.<name>`
in lower case, the same way `emit` and `deliver_event` fold it.

A timer needs `at`, `every`, `delay` or `after`. `after: <trigger id>` waits
for that trigger to have fired, and `delay` then counts from that moment.
`every` implies repeat and catches up when a virtual clock jumps several
periods. A misspelled `after`, `every: 0s`, or a timer field on a non-timer
trigger is refused when the environment compiles.

`do` runs to completion at the governance synchronous point, bounded by
`limits.action_timeout_s`. A wall-clock timer runs its steps on its own thread
without holding the engine lock, so a step that calls the control API cannot
deadlock against the run.

## Environment steps

| Type | Does |
| --- | --- |
| `http_call` | Loopback (or allow-listed) `http`/`https`. Redirects are not followed (a 3xx is the recorded status). Records status and duration only. |
| `clock_advance` | Virtual clock only. Pushes time to clock-aware fixtures. |
| `sleep` | Wall pause, bounded. |
| `shell` | In a trigger `do:` list, `/bin/sh -c` in the experiment dir (world action). A level-hook `type: shell` is still the process step. |
| `emit` | A `user.*` event, value-free payload. |
| `fixture_restart` / `fixture_stop` | Bounce or drop a fixture. |
| `steer`, `inject`, `pause`, `resume`, `checkpoint`, `kill_switch`, `hitl_answer` | Session actions through the control API, on the run's own session unless `session:` names another bound one. Refused without the environment capability. `kill_switch` cancels the in-flight decode (something is in flight only on an `llm_call` trigger) and persists with `auto_stop`; persisting needs a checkpoint store, without one it pauses instead. Its `action.done` carries `cancelled` and `persisted` (0 or 1), because the pause alone does not stop a turn already past its gate. `hitl_answer` queues a user turn (`enqueue_input`, source `hitl`); it does not resolve a HITL hold. |
| `threshold_alarm` | Live `run.post` step: emit an event when a metric crosses a threshold. In a trigger `do:` list it stays quiet (there is no metric). |

## Event record

Each line of `{run_dir}/traces/environment.jsonl` is one record (`v`, `seq`,
`id`, `name`, `kind`, `source`, `time`, `run`, optional `anchor`, `outcome`,
`payload`). Payload is **value-free**: numbers, booleans, declared ids,
`sha256:` digests. Never a body, token, or model text.

The artefact type is `environment` (`stream = custom.environment`,
`plot = timeline`). A cache hit re-emits the file onto the channel and
starts no fixture.

## Three kinds of attack

| Kind | How it is expressed |
| --- | --- |
| In the input | dataset item (`inputs.user`, `inputs.tool_fixtures`) |
| A hostile party in the app | overlay that adds or patches an agent, tool, or infra row |
| A change in the world | `environment:` trigger + fixture / `http_call` / clock |

## Not a replacement

Nothing below is deprecated. `environment:` adds a world around the run.

| Slot | What it is | What it is not |
| --- | --- | --- |
| `inputs.memory_seeds` | Facts the agent already knows | A process or a clock |
| `inputs.tool_fixtures` | Mock payloads a tool reads | A live issuer or payments process |
| `inputs.hitl` | Scripted operator replies at the start | A mid-run `hitl_answer` |
| `service_start` / `service_stop` | Pipeline pre/post infra for a level | A run-scoped fixture (always stopped with the run) |
| `gdb@v1` `debug.script_file` | Operator debugger | Compiled world actions |
| Flavour / Control / Infra | How the runtime is deployed | The world around a run (`environment:`) |

The gdb `when` line and `environment.triggers` share one model on purpose.
gdb observes. A trigger `do:` list changes the world.

`kind: service` on a fixture is the world around a run. `service_start` is
a pipeline step that brings up analysis or tool-server infra for a level.
Use `keep_running: true` only on the pipeline pair. |

## Determinism and durability

- **D1** anchored: trigger at a kernel transition, virtual clock. Replay matches `seq` after dropping `wall` / `elapsed_ms`.
- **D2** virtual timers and clock_advance: same.
- **D3** wall timers, live pipeline events, observability patterns: recorded, delivered at the next synchronous point. `environment.replay: recorded` re-delivers them at the stored anchor.

A checkpoint restore does **not** roll back fixture state or occurrence
counts. Environment events are a view, not a journal. In a lab run with a
virtual clock, recorded `now` reads the run clock.

Planned formal rows (no TLA in this branch): occurrence counts are world
state; environment.jsonl is not a recovery journal; virtual `RunClock`
is the source of `now` for a lab run.

## Scenario checkpoints

`experiment.session_checkpoints` names persist points. Each entry is a
`when` (the same trigger as a world action) compiled to `checkpoint`
then `resume` (or `pause` when `after: pause`). Persist needs the
session stopped, so `checkpoint` pauses, writes, and `resume` continues
the producer. A scenario lists `produce_checkpoints` to publish those
files, and another lists `depends_on` plus `resume_from` to wait for
the file and load it at session start (not mid-turn). The consumer
waits outside `parallel_scenarios` and takes a slot only when it
starts. Two scenarios cannot publish the same id.

This is not the `experiment.checkpoints` starting-state axis.

## Common mistakes

- `executor.worker_processes` above 1 with an `environment:` — the batch refuses it: a worker process has no bound environment, so its triggers, fixtures, named actions and recorded replay would never run.
- Putting allocated ports in the cache key — they are excluded; the command is not.
- Expecting `service_start` to leave a process running after the level — set `keep_running: true`.
- `http_call` to a non-loopback host without `allow_network`, or expecting it to follow a redirect.
- `debug.script_file` is read relative to the experiment and must stay inside it; an absolute path or `..` is refused.
- `depends_on: producer` is one name, not a list of letters; `resume_index` must be a positive integer; `realtime:` must be `true` or `false`.
- The cache key hashes the files a fixture command names and the small files (up to 500, each under 5 MB) of the folder holding them. A file read from elsewhere is invisible: list it under `watch:` (paths or globs relative to the experiment, directories included) or an edit to it serves a stale cached trace.
- A limit, timeout, `stop_grace_s` or `health.timeout_s` that is zero, negative or not finite, or a fixture `port` outside 1 to 65535: refused when the manifest is compiled.
- A `user.*` event emitted after the run's last transition reaches no trigger: it is logged as `event.delivered` with `outcome: skipped` when the run closes.
- `payload` is checked by shape (numbers, booleans, lowercase ids, digests) to keep free text out of the log; it is not a secrets filter.
- `limits.max_events_per_run` too low: the log stops growing (and `environment.closed` carries `truncated`), the run is not failed.
- `clock_advance` on a wall clock, or `sleep` on a virtual clock.
- Looking for environment events in `events.jsonl` — they are in `environment.jsonl`.
- Declaring `fixtures` on a dataset item — the block is refused, including `fixtures: []`.
- Putting `environment` under `inputs` — it is a sibling of `inputs`.
- Using `tool_fixtures` in place of a live process — mock payloads stay in `inputs.tool_fixtures`.
- Omitting `scenarios[].overlays` — the loader infers a file named after the scenario id. Write empty lists when there is no overlay.

## See also

- [dataset.md §10](dataset.md#10-world-around-the-item-environment): item overlay
- [experiment.md](experiment.md#environment): experiment and scenario blocks
- [Tutorial 15](../tutorials/15-world-events-and-fixtures/README.md)
- Schema fragments `environment` and `environment-item` in [schemas.md](../references/schemas.md)
