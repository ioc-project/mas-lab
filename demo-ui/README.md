#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0

The **demo UI** is a live dashboard for a running MAS or a completed lab run.
It is a separate application from the manifest authoring tool in `ui/`.

## Run locally

From the repo root:

```bash
task demo-ui
```

That starts the controller on port 8090 and this Vite app on port 5174.
Open http://127.0.0.1:5174/#run-view.yaml — the Vite proxy
forwards `/api` and `/ws` to the controller.

```bash
task demo-ui -- /path/to/library
task demo-ui LIBRARY=/path/to/library
```

Shipped layouts live in `layouts/examples/`. `compare-overlays.yaml` includes
`run-view.yaml` twice with different `dataSource.overlay` values so the same
run view can be reused side by side. Paths passed to `task demo-ui` (a
library, a repo that contains one, a `demo-ui/layouts` directory, or a
layout YAML) are added to `MAS_LIBRARY_PATHS` / `MAS_DEMO_UI_LAYOUTS`.

## Offline / Pages

The UI is dispatch and display. Offline mode loads `bundle.js` /
`bundle.json` (layout + envelopes) and plays them on the same plugin bus —
no `/api`, no WebSocket.

```bash
mas-lab demo-ui export ~/.cache/mas/traces/<hash> -o /tmp/replay --layout run-view.yaml --serve
```

Serve the export directory over http (ES modules).

## Replay

Default input is native **`events.jsonl`**. Experiment pipelines and the
MAS are not executed. Chat, topology, series, and story are live-folded
from those records on one discrete-event clock (merged by timestamp, then
emitted). `--speed 10` is 10× that clock; `--speed 0` dumps instantly.
Default `--replay-time 15` sets `--speed` to `duration / 15`.
`mas-lab replay TRACE --info` prints duration, event count, and that
factor without ingesting.

| Target | What plays | Folds |
| --- | --- | --- |
| `events.jsonl` file | that file | live (chat / topology / series / story) |
| lab directory | `infra/.cache/*.events.jsonl`, else `**/events.jsonl` | live |
| ui-trace directory | `runtime.jsonl` + `pipeline/<artefact>.jsonl` | recorded snapshots (`--recompute` re-folds missing shards) |

A ui-trace dir (`manifest.json` + `runtime.jsonl`) wins over nested
`events.jsonl`. Pass the jsonl file to force native replay.

`--recompute` does **not** re-run agents or `experiment.yaml` steps. It
replays runtime envelopes and live-folds plot artefacts that are missing
or stale. `--trees pipeline` plays recorded plots only.

```
ui-trace/
  manifest.json                 # kind, files[], runtime_digest
  runtime.jsonl                 # native / spec / job  (input)
  pipeline/<artefact>.jsonl     # one materialized fold / step output
```

```bash
# Default: ingest events.jsonl; live-fold plots as they play
mas-lab replay path/to/events.jsonl --speed 10

# Record the WS stream so the next pass can play artefacts from files
mas-lab replay path/to/events.jsonl --speed 10 --record /tmp/ui-trace

# Play recorded runtime + pipeline (speedup applies to plots)
mas-lab replay /tmp/ui-trace --speed 10

# Replay telemetry; reuse valid artefacts; fold only the rest
mas-lab replay /tmp/ui-trace --recompute --speed 0

# Force every fold (ignore artefact cache)
mas-lab replay /tmp/ui-trace --recompute --trees runtime --speed 0

# Plots only (skip native telemetry)
mas-lab replay /tmp/ui-trace --trees pipeline --speed 0
```

`--wait` (default) returns after `job.completed`. `--detach` returns as
soon as the job is posted. Extra trees can be added later via
`manifest.files`.
