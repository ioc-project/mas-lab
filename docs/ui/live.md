<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Live dashboard

The live dashboard shows a running multi-agent system (MAS), or a completed lab run, as topology, events, metrics, and chat. It is not the manifest studio in [`index.md`](index.md), which creates and edits libraries, experiments, and pipelines.

**Who needs it:** anyone who wants to watch a run as it happens, or replay one.
**First step:** run `task demo-ui`.

Contents:

- [Quick start](#run-it)
- [What you see](#what-you-see)
- [Replay](#replay-inputs)
- [Layouts](#layouts)
- [Offline replay and Pages export](#offline-replay-and-pages-export)
- Developer material (stream envelope, data sources, topics, plugins, pipeline): [Live dashboard reference](live-reference.md)

## Run it

```bash
task demo-ui
```

This starts `mas-lab serve` on port 8090 and the Vite app on port 5174. Open http://127.0.0.1:8090/. The root page lists every layout found. A hash such as `/#run-view.yaml` opens one dashboard. Ctrl+C stops both processes.

To add a library's dashboards to the gallery, point at the library, a `demo-ui/layouts` directory, or a layout YAML. Nested `*/demo-ui/layouts` under the path are found too.

```bash
task demo-ui -- /path/to/library
task demo-ui LIBRARY=/path/to/library      # DEMO_UI_LIBRARY is the same
task demo-ui LAYOUTS=/path/to/library/demo-ui/layouts
```

`MAS_LIBRARY_PATHS` also finds library layouts. A hash naming a layout that `serve` cannot see falls back to the gallery. Environment variables are listed in the [reference](live-reference.md#environment-variables).

## What you see

The browser connects to the controller (`mas-lab serve`) over one WebSocket. The controller owns the streams: specs, native events (the runtime's `events.jsonl` records), human-in-the-loop (HITL) approvals, metrics, and pipeline artefacts. OpenTelemetry (OTel) and knowledge-graph (KG) outputs arrive later as artefacts, not live.

- **Topology.** Active agents pulse. Packets travel the edge between agents (request or response).
- **Chat.** Conversational events appear as messages.
- **Metrics.** Tokens, cached tokens, duration, and call counts tick while the MAS runs. A key shows `—` until its first real value. Null is never shown as 0.
- **Structure.** Periodic `control.structure` frames show stream offset, digest, and event-kind counts.
- **Governance context.** Context size per LLM call, only if the layout mounts a plugin that asks for it.

Duration freezes on `mas_call_end` / `execution_end`. At run end, `metrics.json` overrides live ticks.

Optionally, add this lab pipeline step for run-level metrics:

```yaml
run:
  post:
    - name: live-stats
      type: extract_trace_stats
```

It publishes `kind: metric` artefacts with the post-run CSV counters (`duration_s`, `total_tokens`, `cached_tokens`). See [Metrics on the bus](live-reference.md#metrics-on-the-bus).

The Run button sends `replay` in `mode: replay`, `run-experiment` for an experiment id, and `run-mas` for a standalone MAS. Control verbs need `dataSource.session`.

## Replay inputs

Two inputs feed replay. Do not mix them. Neither runs the MAS, the LLM, or post-steps.

1. **Native `events.jsonl` (default).** `mas-lab replay PATH` posts those records to `/api/ingest`. A file is that jsonl. A directory uses `infra/.cache/*.events.jsonl`, else `**/events.jsonl`. Chat, topology, series, and story are folded live from each native event.
2. **Recorded ui-trace.** After `--record DIR`, `mas-lab replay DIR` plays `runtime.jsonl` plus `pipeline/<artefact>.jsonl` (already-folded snapshots).
    - `--recompute` replays runtime, keeps valid artefact shards, and folds the rest live.
    - `--trees pipeline` plays plots only.

If PATH is a ui-trace directory (`manifest.json` + `runtime.jsonl`), it wins over nested `events.jsonl`. Pass the jsonl file to force native replay.

In the dashboard, replaying a finished lab run plays a run coordinate. The controller finds its `events.jsonl` through the run-artifact registry. Reference ("gold") traces under `infra/.cache` are the fallback. Replay may interleave cached `metrics.json` / `progress.jsonl` with events. `eval_mce` and the judge LLM do not run.

## Layouts

A layout is a YAML file. Node types: `grid`, `hstack`, `vstack`, `tabs`, `panel`. Any node may `include` another file's `layout:` tree.

Root properties are general. `panel.props` overrides them per plugin. `windowed: false` on a panel drops the Golden Layout title bar.

```yaml
mode: live           # default chrome mode (first of `modes` if omitted)
modes: [live, replay]  # live | replay | passive; `both` means live+replay
topology:
  edges: curve       # straight | curve
  direction: unidirectional  # unidirectional (default) | bidirectional
```

- A replay-driven layout sets `mode: passive`.
- A topology panel overrides with `props.edges` / `props.direction`. Unidirectional draws arrowheads.
- The shell mounts only what the YAML names: `navbar.left` / `navbar.right`, `leftPanel`, `rightPanel`, and grid `plugin:` panels.
- `live-run` and `replay-controls` follow the active mode.

### Chrome plugins

| Plugin | Role |
| --- | --- |
| `title` | Navbar title (top left) |
| `run-state` | Run chip (`not started` / `running` / `paused` / `stopped`) and Reset |
| `alert-banner` | Expandable error log (job failures, spec load, command errors) |
| `session-picker` | Attach a prior run (default: last) or start New |
| `settings` | Gear (top right); hosts other plugins, usually `prompt-input` |
| `prompt-input` | Generic prompt selector + Run |
| `mode-switch` | Live / Replay chrome mode |
| `live-run` | Run button; visible in Live mode |
| `connection-status` | Controller health dot (yellow in offline) |
| `nav-links` | Optional layout switcher |
| `replay-controls` | Play / pause / step / speed; visible in Replay mode |

### Sample layouts

Demo-ui ships generic samples. Lab layouts live with their labs.

- `run-view.yaml`: topology and chat plots. Open http://127.0.0.1:5174/#run-view.yaml.
- `single-run.yaml`: the same pair with chrome.
- `compare-overlays.yaml`: two overlay lanes of `run-view.yaml`.

### Compare lanes

A side-by-side layout `ref`s a single-pane layout and binds ids per lane. Live ingest stamps `scenario` / `overlay` to isolate columns.

```yaml
ref: run-view.yaml
lanes:
  - title: Overlay A
    dataSource: { overlay: overlay-a }
  - title: Overlay B
    dataSource: { overlay: overlay-b }
```

### Plots

Demo-ui ships no plot plugins. Plot kinds come from `mas.lab.ui.plots`: chat and topology in mas-lab, timeseries in library-lab. Any library on `MAS_LIBRARY_PATHS` can add kinds. A panel names `plugin: plot` plus `kind` / `artefact` / `stream` (for example `artefact: chat`).

Realtime artefacts register `stream`, `plot`, `realtime` on the artifact type. Live URL: `/api/artefacts/{library}/{experiment}/{scenario}/{test}/{run}/{name}`, with `-` for unset parts. `GET /api/artefacts` lists them.

Live format is `embedded`. End-state plots may use `format: svg` or `png`. The host loads `GET /api/plots/{kind}?mode=embedded` and `GET /api/plots/{kind}/draw.js`, then subscribes to the stream. Detectors that emit a series register their artefact on load.

## Offline replay and Pages export

The same plugins run without a backend. A static site is a Vite build plus envelopes and the compiled layout. The player feeds the bus (`replay: false`) with play, pause, step, and speed controls.

```bash
mas-lab demo-ui export ~/.cache/mas/traces/<hash> -o /tmp/replay --layout run-view.yaml
python3 -m http.server 8766 --bind 127.0.0.1 --directory /tmp/replay
# open http://127.0.0.1:8766/
```

Or: `python3 -m mas.lab.controller.ui_stream.export TRACE -o /tmp/replay --serve`.

Serve over http (ES modules fail on `file://`). GitHub Pages hosts the export directory as is.

Next: [Live dashboard reference](live-reference.md), then [Tutorial 8: Telemetry](../tutorials/08-telemetry/README.md).
