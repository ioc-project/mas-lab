<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Live dashboard

A live dashboard that sits on top of a running MAS or a completed lab run.
It is **not** the manifest authoring studio in [`index.md`](index.md). That
tool stays on its own path (CRUD over libraries, experiments, and pipelines).

This surface consumes **controller-owned streams** — specs, native
observability, HITL, metrics, and pipeline artefacts — over one WebSocket.
OTel / KG are post-hoc artefact payloads on that same envelope when a
pipeline produces them. They are not the live bus.

## Architecture

```
Browser (golden-layout + YAML layouts)
        │  ids only: library / experiment / scenario / test / run / session
        ▼
mas-lab serve   /ws  /api/commands  /api/ingest  /api/runs
        ▲
        │  live: pipeline channel → ingest subscriber → POST /api/ingest
        │  replay: same channel records from the sealed run stream
live worker / completed run artefacts
```

Layout plugins never name a file. CLI replay does: the default input is
native `events.jsonl`. Chrome Replay of a sealed lab run is “play this run
coordinate” (the controller resolves that run’s `events.jsonl`). Gold
traces under `infra/.cache` are a discovery fallback, not a layout field.

See [Replay inputs](#replay-inputs).

Plugins declare topic globs (`command.*`, `result.*`, `native.*`,
`metric.*`, `control.*`, `job.*`, `artifact.*`, `spec.*`) and a
`DataSourceRef`. Side-by-side panels override only the fields they need
(typically `overlay` or `scenario`).

## Replay inputs

Two ways to feed the dashboard. They are not mixed, and neither executes
the MAS or an experiment.yaml pipeline.

1. **Native `events.jsonl` (default).** `mas-lab replay PATH` posts those
   records to `/api/ingest`.
   A file is that jsonl. A directory uses `infra/.cache/*.events.jsonl`,
   else `**/events.jsonl`. Chat, topology, series, and story are
   **live-folded** from each native event (same timestamp). The UI does
   not re-interpret the trace beyond projecting conversational kinds onto
   chat, spec-graph activity onto topology, and detector scores onto
   series.

2. **Recorded ui-trace.** After `--record DIR`, `mas-lab replay DIR`
   plays `runtime.jsonl` plus `pipeline/<artefact>.jsonl` (already-folded
   snapshots). `--recompute` replays runtime, keeps valid artefact shards,
   and live-folds the rest. `--trees pipeline` plays plots only. Still no
   MAS, no LLM, no experiment post-steps.

If PATH is a ui-trace dir (`manifest.json` + `runtime.jsonl`), that wins
over nested `events.jsonl`. Pass the jsonl file to force native replay.

Chrome Replay of a completed run may interleave cached `metrics.json` /
`progress.jsonl` with events. That is artefact playback, not pipeline
execution. `eval_mce` / judge LLM do not run.

## One stream

Toward the UI there is one envelope:

```text
{ topic, ts, ref, source, payload, replay, id, nonce? }
```

The browser registers a **nonce** on the WebSocket hello. Live and Replay
telemetry that the UI caused repeats that nonce so two dashboards on the
same serve process do not mix. **Passive** (CLI `mas-lab replay` ingest)
accepts envelopes with no nonce. A tagged envelope still stays on the
channel that registered that nonce.

`DataSourceRef` is ids only:

| Field | Meaning |
| --- | --- |
| `library` | Catalog / lab name (`demo-lab`) |
| `experiment` | Experiment id (`scenario-1-ab`), not a YAML path |
| `scenario` | Scenario id (`baseline`, `overlay-a`) |
| `test` | Dataset item id (today `item{id}`) |
| `run` | Repeat index or controller job id |
| `session` | Control/session id for work outside an experiment |
| `overlay` | Overlay id (`baseline-round-cap`) |
| `mode` | `live`, `replay`, or `passive` |

| `source` / topic prefix | Typical payload |
| --- | --- |
| `command.*` | Plugin intents (`run-experiment`, `replay`, `steer`, …) |
| `result.*` | Ack / job ids for those commands |
| `native.*` / `telemetry.*` | Projected run records |
| `metric.*` | Lab-step / eval artefacts folded as metrics |
| `artifact.*` | Other pipeline artefacts (eval tables, OTel/KG dumps, …) |
| `control.*` | HITL attach results plus periodic `control.structure` dumps (stream offset, digest, kind counts) |
| `job.*` | Worker / job status; `job.progress` / `metric.scope` for barrier progress |
| `io.*` | Process stdout/stderr when forwarded |
| `spec.*` | One-shot `spec.snapshot` at run start (entry, agents, tools, skills, workflow edges) |

Plugin `update(envelope)` is dispatch. `send(command)` injects a `command.*`
frame via `POST /api/commands` (the WebSocket also accepts the same frames).
Generic artefacts can `POST /api/stream`.

Loopback (`127.0.0.1`) may run without a token. Set `MAS_DEMO_UI_TOKEN` to
require `Authorization: Bearer` on `/api/ingest`, `/api/commands`, `/api/plots`,
and a hello `token` (or `?token=`) on `/ws`. A non-loopback bind requires that
token and TLS (`MAS_DEMO_UI_SSL_CERTFILE` / `MAS_DEMO_UI_SSL_KEYFILE`). The
browser stores `?token=` in `sessionStorage` and sends it on every request.

Live workers publish native records onto the in-process telemetry channel;
a subscriber adapter POSTs them to `/api/ingest`. Run-level KPIs tick live
as `kind: metric` snapshots from the same `extract_trace_stats` fold used
on replay. End-of-run pipeline artefacts (`metrics.json`) still win on
`job.completed` if they conflict with a live tick. Periodic
`control.structure` frames dump stream identity, rolling digest, and kind
counts so a panel can show content structure over time.

## Run it

```bash
task demo-ui
```

That starts `mas-lab serve` on port 8090 and the Vite app on port 5174.
Open http://127.0.0.1:8090/ — the root lists every discovered layout.
A hash such as `/#run-view.yaml` opens that dashboard. Library
layouts are discovered through `MAS_LIBRARY_PATHS`. Serve proxies the
Vite app; Vite still proxies `/api`, `/ws`, and `/health` the other way.

Point at a library (or a `demo-ui/layouts` directory, or a layout YAML)
so its dashboards appear in the gallery:

```bash
task demo-ui -- /path/to/library
task demo-ui LIBRARY=/path/to/library
task demo-ui LAYOUTS=/path/to/library/demo-ui/layouts
```

`DEMO_UI_LIBRARY` is the same as `LIBRARY`. Nested `*/demo-ui/layouts`
under the path are picked up.

Ctrl+C stops both processes.

Optional env:

| Variable | Role |
| --- | --- |
| `MAS_CONTROLLER_URL` | Controller REST base (default `http://127.0.0.1:8090`) |
| `MAS_SESSION_DIRECTORY` | File-backed session advertisements for live **control** attach |
| `MAS_DEMO_UI_LAYOUTS` | YAML layout directory (defaults to `demo-ui/layouts`) |
| `MAS_DEMO_UI_DEV_ORIGIN` | Vite origin (`task demo-ui` sets `http://127.0.0.1:5174`) so `/` on serve shows the UI |
| `MAS_DEMO_UI_INGEST_URL` | Override ingest URL (default `http://127.0.0.1:8090/api/ingest`) |
| `MAS_DEMO_UI_TOKEN` | Shared secret for ingest, commands, plots, and `/ws` |
| `MAS_DEMO_UI_SSL_CERTFILE` / `MAS_DEMO_UI_SSL_KEYFILE` | Required with the token for a non-loopback bind |

Chrome: `mode: replay` sends `replay`; otherwise an experiment id sends
`run-experiment` and a standalone MAS sends `run-mas`. Control verbs still
need `dataSource.session`.

The controller maps a run coordinate to artefacts (`events`, `metrics`, …)
via the run-artifact registry. When no lab output exists yet, replay may
fall back to a gold `{scenario}.events.jsonl` under the library cache.

Plug the same stream into a lab pipeline (optional; live experiment jobs
already ingest unfiltered kinds):

```yaml
run:
  post:
    - name: live-stats
      type: extract_trace_stats
```

`extract_trace_stats` publishes `kind: metric` artefacts with the same
counters as the post-run CSV (including `duration_s`, `total_tokens`,
`cached_tokens`). The metrics panel prefers those over a controller-side
LLM usage fold.

## Metrics on the bus

Two update cadences share the `metric.snapshot` topic:

| Cadence | Source | When |
| --- | --- | --- |
| Per LLM/tool call | Controller `NativeIngest` folds `llm_call_end` / timestamps / tool starts | Tokens, cached tokens, duration, call counts while the MAS runs. The UI keeps keys gray (`—`) until the first real value, then flashes the row on each change. Metric envelopes share the same playback lane as native events so they tick with the conversation instead of dumping finals at t=0. |
| End of run | Pipeline artefacts (`extract_trace_stats`, `eval_mce`) | Same usage keys plus `mce.*` and any nested keys folded from `metrics.json` |
| Live-cheap / end-of-run eval | `kind: metric` name `eval_progress` (also `<run>/progress.jsonl`, `progress.json`, `protocol.json`) | Nested numeric/bool leaves flatten to dotted KPI ids. `_` keys, `fragments_present` / `fragments_missing`, and strings (`challenge.scoring`, `note`, `snapshot_at`) stay off the strip. |

`eval_mce` is a run-scoped judge. `overwrite: false` (the default) writes
`metrics.json` on the first live run and republishes the cached file on
later runs and on id-based replay, so LLMaaJ scores appear immediately
without a second judge call. Gold replay can also load
`{scenario}.metrics.json` next to `{scenario}.events.jsonl`.

Placeholders stay `—` until a value exists (null is not coerced to 0).
`*_activated` is 1/0 only. Completeness / compliance are 1/0, or null when
skipped, partial, or not activated.

End-of-run `eval_mce` session ids alias underscores onto the live dotted
keys (`outcome_fix_applied` → `outcome.fix_applied`; same for
`stateful_*`). `challenge_*_confirmed` stays that id.

## Governance / context stream

`inspect_context` and the rest of the control inspect surface are
**pull-only and demand-driven**. The space of inspectable session state
is large; nothing is sampled unless a mounted UI plugin asked for it.

The layout plugin declares the demand:

```js
{ id: "governance-context", subscribes: ["governance.context"], inspects: ["context"] }
```

On WebSocket hello the shell sends the union of mounted `subscribes` /
`inspects` (not `*`). Catch-all viewer plugins such as `inspector`
(`subscribes: ["*"]`) do not enable inspect pulls. The controller then:

1. At `llm_call_end` / `context_assembled`, folds numeric fields onto
   `governance.context` **only if** that topic is subscribed.
2. If a live session is attached, calls `inspect_context` on those same
   steps — again only while subscribed.
3. On replay, includes compact `context_assembled` records only when
   `inspects` contains `context`. Prompt text is stripped.

Removing the plugin from the layout (or leaving that panel unmounted)
stops the pull. Additional inspect families get a row in
`ui_stream/inspect.py` `PULLS` when a plugin actually needs them.

The optional runtime `context_usage` governance plugin is not enabled on
the demo lab; it is for non-UI callers that want the same snapshot on
the native event stream.

## Layouts

Layouts are YAML. Node types: `grid`, `hstack`, `vstack`, `tabs`, `panel`.
Any node may `include` another file's `layout:` tree.

General properties sit on the layout root. Plugin properties sit on
`panel.props` and override the matching general keys. A panel may set
`windowed: false` (or the plugin manifest may) to drop the Golden Layout
title bar — lane headers like BASELINE / OVERLAY-A are not windows.

```yaml
mode: live           # default chrome mode (first of `modes` if omitted)
modes: [live, replay]  # live | replay | passive; `both` means live+replay
topology:
  edges: curve       # straight | curve
  direction: unidirectional  # unidirectional (default) | bidirectional
```

A replay-driven layout sets `mode: passive`.
Side-by-side compare layouts default to `live` (or `replay` if listed first).

A topology panel can override those with `props.edges` / `props.direction`.
Both edge modes work; unidirectional is the default and draws arrowheads.
Active agents pulse; inter-agent packets travel the edge (request vs response).

Plugins declare `gets` (current snapshot on refresh) and `subscribes`
(live frames only). Topology `get`s `spec`; chat/metrics `subscribe` to
observability and metrics. The WebSocket hello sends those lists; the
controller snapshots `get` streams and forwards only `subscribe` topics.

Live runs publish native records onto the telemetry channel while agents
talk. An ingest subscriber forwards them to `/api/ingest`.
`extract_trace_stats` folds the same stream into `kind: metric`
snapshots so the Metrics strip ticks per lane. Periodic `control.structure`
dumps (offset, rolling digest, kind counts) feed the Structure panel.
`artefact-ticks` lists metric/artefact snapshots as they arrive. After every
MAS run, post-phase steps (`extract_trace_stats`, `eval_mce`, …) still write
artefacts; `job.completed` folds `metrics.json` / `progress.jsonl` and that
fold wins on conflict. Replay drives the same lane from the recorded
stream (not a separate synthesizer). Cache-hit runs replay `events.jsonl`
onto the channel so the strip still ticks.

`eval_mce` with `overwrite: false` reuses `metrics.json`. A live Run with
`--force` wipes the output tree, so the step also restores/writes that
file next to the run-hash trace cache. If the artefact is missing, judge
LLM calls go through the same `llm_cache` infra as the MAS (`MAS_LLM_CACHE`,
default `infra/.cache/eval.llm-cache.json` on a UI live job). Cached
LLMaaJ is instant. Live judge stays in the post phase (not while agents
talk).

The live dashboard `/ws` is not the pipeline `mas-stream/1` channel. That
channel keeps its own hello token and TLS rule.

`mode-switch` shows the modes listed on the layout (`modes:`); `live-run` and
`replay-controls` follow the active chrome mode. The starting mode is `mode:`.

Chrome is layout-declared only. The shell mounts nothing unless the YAML
names it: `navbar.left` / `navbar.right`, `leftPanel`, `rightPanel`, and
grid `plugin:` panels. There is no implicit menu, catalog rail, or run bar.

Generic chrome plugins:

| Plugin | Role |
| --- | --- |
| `title` | Navbar title (top left) |
| `run-state` | Run chip (`not started` / `running` / `paused` / `stopped`) and Reset |
| `alert-banner` | Expandable error log (job failures, spec load, command errors) |
| `session-picker` | Attach a prior run (default: last) or start New |
| `settings` | Gear (top right); hosts other plugins, usually `prompt-input` |
| `prompt-input` | Generic prompt selector + Run |
| `mode-switch` | Live / Replay chrome mode (layout `modes`) |
| `live-run` | Run button; visible in Live mode |
| `connection-status` | Controller health dot (yellow in offline) |
| `nav-links` | Optional layout switcher |
| `replay-controls` | Play / pause / step / speed; visible in Replay mode |

Demo layouts (generic samples only):

- `run-view.yaml` — topology and chat plots
- `single-run.yaml` — the same pair with chrome
- `compare-overlays.yaml` — two overlay lanes of `run-view.yaml`

Lab-specific layouts live with those labs. Demo-ui ships no plot plugins
and has no `src/plugins` tree: chrome and one plot host live next to
`shell.js`. Plot kinds are discovered from `mas.lab.ui.plots` (chat and
topology in mas-lab, timeseries in library-lab). Extra kinds register
through `mas.lab.ui.plots` from any library on `MAS_LIBRARY_PATHS`.
Layouts name `plugin: plot` plus `kind` / `artefact` / `stream`.

Realtime artefacts register an address on the artifact type
(`stream`, `plot`, `realtime`). The live URL is

`/api/artefacts/{library}/{experiment}/{scenario}/{test}/{run}/{name}`

Unset coordinate parts are `-`. `GET /api/artefacts` lists registered
realtime artefacts. A panel names the artefact (`plugin: plot`, `artefact: chat`). Live
format is `embedded`. End-state pipeline plots may still request `svg` or `png`
(`format: svg`). The host loads `GET /api/plots/{kind}?mode=embedded` plus
`GET /api/plots/{kind}/draw.js` and subscribes to that artefact's stream.
Detectors that emit a series register that artefact on load.

Side-by-side layouts `ref` a single-pane layout and bind each lane's
ids — they do not repeat the nested tree:

```yaml
ref: run-view.yaml
lanes:
  - title: Overlay A
    dataSource: { overlay: overlay-a }
  - title: Overlay B
    dataSource: { overlay: overlay-b }
```

Open http://127.0.0.1:5174/#run-view.yaml. Duration and token counts come
from native timestamps; `duration_s` freezes on `mas_call_end` /
`execution_end`.

Compare two overlay lanes with `compare-overlays.yaml`. Each panel
`include`s `run-view.yaml` and binds a `dataSource.overlay`. Live ingest
stamps `scenario` / `overlay` so columns stay isolated.

`MAS_LIBRARY_PATHS` (or `MAS_DEMO_UI_LAYOUTS`) adds a lab's
`demo-ui/layouts`. Pass those as `task demo-ui -- /path/to/library` (or
`LIBRARY=` / `LAYOUTS=`). A hash that names a layout the serve process
cannot see falls back to the gallery instead of crashing.

## Plugin contract

```js
mount(container, { dataSource, send, config }) → { update(envelope)?, resize()?, destroy() }
```

Vanilla JS is enough. A plugin registers once; the shell compiles YAML into
golden-layout components and delivers matching stream frames to `update()`.

## Stream topics

| Namespace | Origin |
| --- | --- |
| `command.*` | Plugin / chrome intents on the shared stream |
| `result.*` | Controller acks after a command adapter runs |
| `native.*` | Projected native records (`llm_call_start`, `tool_call_end`, …) |
| `metric.*` | Pipeline metric artefacts (`extract_trace_stats` streaming) plus LLM usage fallback |
| `governance.*` | Context size / token counts sampled at LLM_CALL (`inspect_context`) and folded from `context_assembled` |
| `artifact.*` | Other lab-step artefacts posted as stream frames |
| `control.*` | HITL plus `control.structure` (stream offset, digest, kind counts) and `control.context` |
| `job.*` | Controller jobs re-published so the UI does not poll |
| `io.*` | Worker IO when a sender forwards it |
| `spec.*` | `spec.snapshot` at job start |

## Common trace

The websocket envelope **is** the common trace. Do not introduce a second
run format. A recorded session is a directory of timestamped JSONL files
that replay by being pushed through the same ingest path as live:

| File | Topic prefix | What |
| --- | --- | --- |
| `events.jsonl` | `native.*` | Already the MAS projected record (`kind`, `timestamp`, `agent_id`, …) |
| `metrics.json` / `progress.jsonl` | `metric.*` | Usage ticks plus eval artefacts (`mce.*`, `outcome.fix_applied`, …) |
| `control.jsonl` | `control.*` | HITL / inspect / steer |
| `spec.json` | `spec.snapshot` | Topology, titles, agents — built dynamically from spec or the trace |

Adapters (other custom traces) convert **into** envelopes. The demo UI never
learns those source schemas.

Nested metric documents (live-cheap progress snapshots, session scores) are
flattened to dotted keys (`outcome.fix_applied`, `tokens.input`) on ingest so a
plot or chrome chip can read them without a dedicated metrics plugin.

## Offline replay and Pages export

The same plugins run without a backend. A static site is a Vite build plus a
bundle of envelopes + the compiled layout. The player pumps envelopes through
the existing bus (`replay: false`) with play / pause / step / speed.

```bash
mas-lab demo-ui export ~/.cache/mas/traces/<hash> -o /tmp/replay --layout run-view.yaml
python3 -m http.server 8766 --bind 127.0.0.1 --directory /tmp/replay
# open http://127.0.0.1:8766/
```

Or `python3 -m mas.lab.controller.ui_stream.export TRACE -o /tmp/replay --serve`.

ES modules need http (not `file://`). GitHub Pages hosts the export directory
as-is.

Chrome plugin `replay-controls` is a no-op until offline mode. `run-inspect`
folds per-agent usage, extra metric keys, and governance counts from the
stream — no hardcoded agent table. Topology still comes from `spec.snapshot`
(library YAML when available, otherwise inferred from native events).

## Pipeline (live + post)

One pipeline engine (`mas.lab.benchmark.pipeline`) runs both phases:

1. **Live.** While the MAS runs, native records go on the in-process
   telemetry channel. Steps that define `on_batch` fold those batches;
   `extract_trace_stats` publishes `kind: metric` ticks to `/api/ingest`.
   Periodic `control.structure` dumps carry offset, digest, and kind counts.
2. **Post.** After the run is terminal, `finish` / `execute` writes the
   same artefacts from the known run plan. The scheduler drives that DAG
   (ordering, resource caps, skip-on-failed-dep).

Cache is unchanged: a step whose artefact already exists can skip
(`overwrite: false` on `eval_mce`, existing output checks). Live force
wipes still restore `metrics.json` from the run-hash cache. If that file
is gone, judge completions reuse `llm_cache`. Replay reads those files;
it does not re-run the judge.

Chrome Replay of a sealed run may fold incremental `trace_stats` counters
from the recording (same counters as `extract_trace_stats`) and interleave
them with events. That is a local fold, not executing the pipeline step.
`mas-lab replay events.jsonl` folds chat, topology, and series on each
native event. Extra library folds register through `MAS_UI_FOLDS`. Live
+ LLM-cache re-runs must not use synthesized timestamps — they have to
wait for matching live events (same kind + agent + count).
