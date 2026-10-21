<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Live dashboard reference

Developer reference for the [live dashboard](live.md): stream envelope, data sources, topics, plugins, metrics, and pipeline.

Contents:

- [Architecture](#architecture)
- [One stream](#one-stream)
- [Security](#security)
- [Environment variables](#environment-variables)
- [Metrics on the bus](#metrics-on-the-bus)
- [Governance / context stream](#governance-context-stream)
- [Plugin contract](#plugin-contract)
- [Common trace](#common-trace)
- [Pipeline (live + post)](#pipeline-live-post)

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

- Layout plugins never name a file. CLI replay does (default: native `events.jsonl`).
- Side-by-side panels override only the data-source fields they need (typically `overlay` or `scenario`).
- The dashboard `/ws` is not the pipeline `mas-stream/1` channel, which has its own hello token and TLS rule.

## One stream

Every message reaches the UI in one WebSocket envelope:

```text
{ topic, ts, ref, source, payload, replay, id, nonce? }
```

- The browser registers a **nonce** on the WebSocket hello. Telemetry it causes repeats the nonce, so two dashboards on one serve process stay apart.
- **Passive** mode (CLI `mas-lab replay` ingest) accepts envelopes without a nonce.

A **data source** (`DataSourceRef`) identifies what a panel shows. It holds ids only:

| Field | Meaning |
| --- | --- |
| `library` | Catalog / lab name (`demo-lab`) |
| `experiment` | Experiment id (`scenario-1-ab`), not a YAML path |
| `scenario` | Scenario id (`baseline`, `overlay-a`) |
| `test` | Dataset item id (today `item{id}`); the name predates the "Item" level and is kept for compatibility |
| `run` | Repeat index or controller job id |
| `session` | Control/session id for work outside an experiment |
| `overlay` | Overlay id (`baseline-round-cap`) |
| `mode` | `live`, `replay`, or `passive` |

Topics (the prefix of `topic`):

| Topic prefix | Origin / payload |
| --- | --- |
| `command.*` | Plugin and chrome intents (`run-experiment`, `replay`, `steer`, ...) |
| `result.*` | Controller acks and job ids for those commands |
| `native.*` / `telemetry.*` | Projected run records (`llm_call_start`, `tool_call_end`, ...) |
| `metric.*` | Lab-step and eval artefacts folded as metrics; `extract_trace_stats` streaming; LLM usage fallback; `metric.scope` for barrier progress |
| `governance.*` | Context size and token counts sampled at LLM_CALL (`inspect_context`) and folded from `context_assembled` |
| `artifact.*` | Other pipeline artefacts (eval tables, OTel/KG dumps, ...) |
| `control.*` | HITL attach results, `control.structure` (stream offset, rolling digest, kind counts) and `control.context` |
| `job.*` | Controller jobs re-published so the UI does not poll; `job.progress` for barrier progress |
| `io.*` | Process stdout/stderr when a sender forwards it |
| `spec.*` | One-shot `spec.snapshot` at run start (entry, agents, tools, skills, workflow edges) |

`send(command)` injects a `command.*` frame via `POST /api/commands` (the WebSocket accepts the same frames). Generic artefacts can `POST /api/stream`.

Plugins declare `gets` (snapshot on refresh) and `subscribes` (live frames only) in the hello. The controller snapshots `get` streams and forwards only `subscribe` topics. Topology gets `spec`. Chat and metrics subscribe.

## Security

- Loopback (`127.0.0.1`) may run without a token.
- `MAS_DEMO_UI_TOKEN` requires `Authorization: Bearer` on `/api/ingest`, `/api/commands`, `/api/plots`. It also requires a hello `token` (or `?token=`) on `/ws`.
- A non-loopback bind requires that token and TLS (`MAS_DEMO_UI_SSL_CERTFILE` / `MAS_DEMO_UI_SSL_KEYFILE`).
- The browser stores `?token=` in `sessionStorage` and sends it on every request.

## Environment variables

| Variable | Role |
| --- | --- |
| `MAS_CONTROLLER_URL` | Controller REST base (default `http://127.0.0.1:8090`) |
| `MAS_SESSION_DIRECTORY` | File-backed session advertisements for live **control** attach |
| `MAS_LIBRARY_PATHS` | Libraries whose `demo-ui/layouts` and plot kinds are found |
| `MAS_DEMO_UI_LAYOUTS` | YAML layout directory (defaults to `demo-ui/layouts`) |
| `MAS_DEMO_UI_DEV_ORIGIN` | Vite origin (`task demo-ui` sets `http://127.0.0.1:5174`) so `/` on serve shows the UI |
| `MAS_DEMO_UI_INGEST_URL` | Override ingest URL (default `http://127.0.0.1:8090/api/ingest`) |
| `MAS_DEMO_UI_TOKEN` | Shared secret for ingest, commands, plots, and `/ws` |
| `MAS_DEMO_UI_SSL_CERTFILE` / `MAS_DEMO_UI_SSL_KEYFILE` | Required with the token for a non-loopback bind |
| `MAS_LLM_CACHE` | Judge LLM cache (default `infra/.cache/eval.llm-cache.json` on a UI live job) |
| `MAS_UI_FOLDS` | Registers extra library folds for `mas-lab replay` |

`serve` proxies the Vite app. Vite proxies `/api`, `/ws`, and `/health` back.

## Metrics on the bus

Metric envelopes share the playback lane of native events, so they tick with the conversation. These cadences share `metric.snapshot`:

| Cadence | Source | Content |
| --- | --- | --- |
| Per LLM/tool call | Controller `NativeIngest` folds `llm_call_end`, timestamps, tool starts | Tokens, cached tokens, duration, call counts. The UI flashes the row on each change. |
| End of run | Pipeline artefacts (`extract_trace_stats`, `eval_mce`) | Same usage keys plus `mce.*` and nested keys from `metrics.json`. Wins on `job.completed`. |
| Eval progress | `kind: metric` named `eval_progress` (also `<run>/progress.jsonl`, `progress.json`, `protocol.json`) | Nested numeric/bool leaves flatten to dotted ids. `_` keys, `fragments_present` / `fragments_missing`, and strings (`challenge.scoring`, `note`, `snapshot_at`) stay off the strip. |

Rules:

- Placeholders stay `—` until a value exists. Null is not coerced to 0.
- `*_activated` is 1/0 only. Completeness and compliance are 1/0, or null when skipped, partial, or not activated.
- Nested metric documents flatten to dotted keys (`outcome.fix_applied`, `tokens.input`) on ingest.
- End-of-run `eval_mce` ids alias underscores onto live dotted keys (`outcome_fix_applied` to `outcome.fix_applied`; same for `stateful_*`). `challenge_*_confirmed` keeps its id.

`eval_mce` is a run-scoped judge. With `overwrite: false` (default) it writes `metrics.json` on the first live run, then republishes it on later runs and id-based replay, so no second judge call. Gold replay can load `{scenario}.metrics.json` next to `{scenario}.events.jsonl`.

`--force` wipes the output tree, so the step restores `metrics.json` from the run-hash trace cache. If it is missing, judge calls use `llm_cache` (`MAS_LLM_CACHE`). Judging runs in the post phase, never while agents talk.

## Governance / context stream

`inspect_context` and the control inspect surface are **pull-only**. Nothing is sampled unless a mounted plugin asks. The plugin declares the demand:

```js
{ id: "governance-context", subscribes: ["governance.context"], inspects: ["context"] }
```

The hello sends the union of mounted `subscribes` / `inspects`. Catch-all viewers such as `inspector` (`subscribes: ["*"]`) do not enable pulls. The controller then:

1. At `llm_call_end` / `context_assembled`, folds numeric fields onto `governance.context` only if that topic is subscribed.
2. If a live session is attached, calls `inspect_context` on those steps, again only while subscribed.
3. On replay, includes compact `context_assembled` records only when `inspects` contains `context`. Prompt text is stripped.

Unmounting the plugin stops the pull. Add new inspect families as a row in `ui_stream/inspect.py` `PULLS`. The runtime `context_usage` governance plugin (off in the demo lab) gives non-UI callers the same snapshot.

## Plugin contract

A plugin is one JavaScript module that draws one panel. The shell compiles the YAML layout into golden-layout components and passes matching frames to `update()`.

```js
mount(container, { dataSource, send, config }) → { update(envelope)?, resize()?, destroy() }
```

## Common trace

The envelope **is** the common trace. A recorded session is a directory of timestamped files, replayed through the live ingest path:

| File | Topic prefix | Content |
| --- | --- | --- |
| `events.jsonl` | `native.*` | The MAS projected record (`kind`, `timestamp`, `agent_id`, ...) |
| `metrics.json` / `progress.jsonl` | `metric.*` | Usage ticks plus eval artefacts (`mce.*`, `outcome.fix_applied`, ...) |
| `control.jsonl` | `control.*` | HITL / inspect / steer |
| `spec.json` | `spec.snapshot` | Topology, titles, agents; built from the spec or the trace |

Adapters convert other traces into envelopes. The UI never learns their schemas. `run-inspect` folds per-agent usage, metric keys, and governance counts from the stream. Topology comes from `spec.snapshot` (library YAML, else inferred from native events).

## Pipeline (live + post)

One engine (`mas.lab.benchmark.pipeline`) runs both phases:

1. **Live.** Native records go on the in-process telemetry channel. Steps with `on_batch` fold them. `extract_trace_stats` publishes `kind: metric` ticks to `/api/ingest`. `artefact-ticks` lists snapshots as they arrive. Cache-hit runs replay `events.jsonl` onto the channel so the strip still ticks.
2. **Post.** After the run ends, `finish` / `execute` writes the same artefacts. The scheduler drives that DAG (ordering, resource caps, skip-on-failed-dep).

A step whose artefact exists can skip (`overwrite: false` on `eval_mce`). Replay reads those files and never re-runs the judge. Chrome Replay may fold `trace_stats` counters locally and interleave them with events. Live re-runs on the LLM cache wait for matching live events (same kind, agent, count), never synthesized timestamps.
