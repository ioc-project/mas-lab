<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Tutorial 8 — Telemetry: native, OTel, and replay

> **Packages:** `mas-library-standard` (native plugin), `mas-library-telemetry`
> (OTel plugin, replay, collector, ClickHouse)
> **Prerequisite:** [Tutorial 0](../00-environment-setup/),
> [Tutorial 1](../01-building-an-agent/),
> [Tutorial 2](../02-creating-a-mas/),
> [Tutorial 3](../03-experiments-and-analysis/).
> **Follow-on:** [Tutorial 9 — Knowledge graphs](../09-kg-oxp/)
> **Example traces:** [`library-telemetry/examples/qa-agent/`](../../../library-telemetry/examples/qa-agent/)
> (same layout as `examples/trip-planner/`)

Every agent run already produces a native trace: `events.jsonl`. That format
is convenient inside MAS Lab, but nothing outside it can read JSONL events —
no OTel collector, no ClickHouse, no standard OTel-native tool. To hand a run
to anything outside MAS Lab you need the same run described as OpenTelemetry
spans, in the `ioa-observe-sdk` wire format. (That format is also what the
open-source Observe and eXplain Platform —
[outshift-open/observe-and-explain-platform](https://github.com/outshift-open/observe-and-explain-platform)
— consumes, but nothing in this tutorial is specific to it; any OTel-native
backend works the same way.)

That leaves four practical questions, which this tutorial answers in order:

1. How do I get OTel spans instead of (or alongside) native events, live, as
   the agent runs?
2. I already have `events.jsonl` from an earlier run (Tutorials 1–3) — can I
   get OTel spans for it after the fact, without re-running the agent?
3. If I do both, how do I know the live spans and the replayed spans really
   describe the same run?
4. Spans normally only appear once a call finishes — can I see something
   *while the run is still happening*, not only at the end?

Offline commands and functional tests use the shipped qa-agent example. Live
`mas-ctl chat` writes `traces/` next to [Tutorial 1](../01-building-an-agent/)'s
agent (chat cwd is the manifest directory).

| # | What you do |
| --- | --- |
| 1 | Native `events.jsonl` (default) — Tutorials 1–3 |
| 2 | Replace native with live OTel — JSON file or collector |
| 3 | Replay native → OTel after the fact |
| 4 | Serialize: JSON file vs OTel collector / ClickHouse |
| 5 | Equivalence: live OTel plugin vs replay pipeline |
| 6 | Realtime: incremental spans instead of one end-of-run summary |

```bash
uv pip install -e "library-telemetry[convert,verify]"
# optional read-back from ClickHouse:
# uv pip install -e "library-telemetry[clickhouse]"
```

```bash
# Offline replay of this tutorial's commands:
pytest tests/tutorials/test_tutorial_07.py tests/tutorials/test_scenario_commands.py -k tuto-07
```

Questions 1 and 2 above go through the same code path: live export and
replay both call the identical `events_to_otel` / `create_otel_export`
converter. `mas-lab telemetry convert` and `mas-lab telemetry push` are
**CLI shortcuts** for the `events_to_otel` / `push_otlp` pipeline steps
(`telemetry:pipelines/native-to-otel-json.yaml` and
`telemetry:pipelines/export-otel-collector.yaml`). There is no separate
"live" converter and "replay" converter to keep in sync — it's one
implementation used both ways.

Serialization is the last hop, and it's a separate choice from conversion:

| Sink | What you pass | Who chooses it |
| --- | --- | --- |
| JSON file | a path (`-o`, `output_filename`, `output_path`) | you |
| OTLP collector / ClickHouse | an infra manifest (`--infra`, `kind: OtelCollector` / `ClickHouse`) | the manifest |
| Env shortcut | `$OTEL_EXPORTER_OTLP_ENDPOINT` / `$CLICKHOUSE_HOST` | replaces a one-target YAML |

A converted run can carry more than one kind of span — structural (who
called whom), execution (timing, tokens), semantic (messages, arguments),
provenance, and governance. By default you get `structure`, `execution`,
`semantic`, and `provenance`; `governance` is off, since it's the heaviest
category to emit and not every pipeline needs it. Tune categories on the CLI
(`--layer`, `--governance`), on the pipeline step (`export_layers:`), or on
the plugin overlay — live and replay accept the same flags.

---

## 1 — Native (Tutorials 1, 2, 3)

Start with what you already have. The `local` flavour enables the **native**
observability plugin. Every boundary crossing becomes one JSON line in
`events.jsonl`.

| Tutorial | Where native shows up |
| --- | --- |
| [1 — Build an agent](../01-building-an-agent/README.md#step-7--inspecting-the-trace) | `--events` / `--events-file`, trajectory plot |
| [2 — Orchestrate a MAS](../02-creating-a-mas/README.md) | Shared trace across delegated agents |
| [3 — Experiments](../03-experiments-and-analysis/README.md#where-traces-are-stored) | Per-run `traces/events.jsonl` under the labs root |

```bash
mas-ctl chat docs/tutorials/01-building-an-agent/agent.yaml \
  -o pkg://mas.library.standard/overlays/observability-native.yaml \
  -q "What is 15 * 23?" --flavour local
```

Writes `docs/tutorials/01-building-an-agent/traces/events.jsonl`. A captured
copy of that run is
[`library-telemetry/examples/qa-agent/events.jsonl`](../../../library-telemetry/examples/qa-agent/events.jsonl)
(`15 * 23 equals 345.`).

---

## 2 — Replace native with live OTel

Now the first question: skip native entirely and have the agent emit OTel
spans directly, live. The OTel plugin is a peer of the native plugin — same
observability contract, different export, selected by overlay. Runtime
itself has no OTel of its own; everything goes through this plugin. Default
converter profile is `observe_sdk`, covering the `structure`, `execution`,
and `semantic` categories.

### 2a — JSON file

```bash
mas-ctl chat docs/tutorials/01-building-an-agent/agent.yaml \
  -o library-telemetry/overlays/observability-otel-json.yaml \
  -q "What is 15 * 23?" --flavour local
```

Writes `traces/otel_sdk_spans.jsonl` next to the agent. Native `events.jsonl`
is **not** produced. Captured copy:
[`library-telemetry/examples/qa-agent/otel_sdk_spans_live.jsonl`](../../../library-telemetry/examples/qa-agent/otel_sdk_spans_live.jsonl).

### 2b — OTel collector (server)

```bash
docker compose -f library-telemetry/docker/compose.yaml up -d
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
```

`$OTEL_EXPORTER_OTLP_ENDPOINT` **is** the infra manifest. You do not need
`library-telemetry/infra/local-otel.yaml` when the env is set:

```bash
mas-ctl chat docs/tutorials/01-building-an-agent/agent.yaml \
  -o library-telemetry/overlays/observability-otel-collector.yaml \
  -q "What is 15 * 23?" --flavour local
```

`telemetry push` is the serialize-to-infra shortcut: it accepts a span JSONL
**or** native `events.jsonl` (auto-detects and converts first). `--dry-run`
does not need Docker; it defaults to `http://localhost:4318` when env and
`--infra` are unset:

```bash
mas-lab telemetry push library-telemetry/examples/qa-agent/otel_sdk_spans_live.jsonl --dry-run
mas-lab telemetry push library-telemetry/examples/qa-agent/otel_sdk_spans_live.jsonl \
  --infra library-telemetry/infra/local-otel.yaml --dry-run
```

If your team runs a shared collector instead of a local one, write an infra
manifest for it (same shape as `local-otel.yaml`, with that collector's
endpoint) and point `--infra` at that file instead.

---

## 3 — Replay native → OTel

The second question: you already have `events.jsonl` from Tutorials 1–3 and
want OTel spans for it, without re-running the agent.
`mas-lab telemetry convert events.jsonl -o otel_sdk_spans.jsonl` is the
file-sink shortcut for this pipeline — the same converter the live plugin
uses. Pass `--governance` or `--layer structure --layer execution` to change
categories.

Convert with the Python API (what the CLI and the `events_to_otel` step
call):

```bash
python - <<'PY'
from pathlib import Path
from mas.library.telemetry.pipeline import convert_events_to_spans_file
src = Path("library-telemetry/examples/qa-agent/events.jsonl")
dst = Path("/tmp/otel_sdk_spans_replay.jsonl")
n = convert_events_to_spans_file(src, dst, app_name="qa-agent")
print(f"replayed {n} events → {dst}")
PY
```

Shipped replay of that file:
[`library-telemetry/examples/qa-agent/otel_sdk_spans_replay.jsonl`](../../../library-telemetry/examples/qa-agent/otel_sdk_spans_replay.jsonl).

Pipeline step (per run, after a native experiment):

```yaml
- type: events_to_otel
  config:
    converter_profile: observe_sdk
    output_filename: otel_sdk_spans_replay.jsonl
```

Shipped pipeline: `telemetry:pipelines/native-to-otel-json.yaml`.

Keep **both** sinks when you want to compare live OTel with replay:

```bash
mas-ctl chat docs/tutorials/01-building-an-agent/agent.yaml \
  -o library-telemetry/overlays/observability-native-and-otel.yaml \
  -q "What is 15 * 23?" --flavour local
```

---

## 4 — Serialization

| Artifact | File (always) | Service (infra manifest) | Env shortcut |
| --- | --- | --- | --- |
| OTel spans | `otel_sdk_spans.jsonl` | `OtelCollector` → `library-telemetry/infra/local-otel.yaml` | `$OTEL_EXPORTER_OTLP_ENDPOINT` |
| OTel read-back | JSONL dump | `ClickHouse` → `library-telemetry/infra/local-clickhouse.yaml` | `$CLICKHOUSE_HOST` |

```bash
docker compose -f library-telemetry/docker/compose.yaml up -d
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
export CLICKHOUSE_HOST=localhost
```

Images: `otel/opentelemetry-collector-contrib` and `clickhouse/clickhouse-server`.
See `library-telemetry/docker/README.md`.

---

## 5 — Equivalence: live OTel vs replay

The third question: do live spans and replayed spans actually agree? Same
native records, two exporters. Span ids differ (live is random, replay is
seeded); names, topology, and attributes match.

```bash
cd library-telemetry
python -m pytest tests/telemetry/plugins/test_live_plugin_vs_replay.py -q
```

That test parametrizes the trip-planner sample
(`library-samples/apps/trip-planner/traces/events.jsonl`), the trip-planner
example (`library-telemetry/examples/trip-planner/events.jsonl`), and the
qa-agent example (`library-telemetry/examples/qa-agent/events.jsonl`).

---

## 6 — Realtime: incremental spans instead of one end-of-run summary

The fourth question: everything so far — live or replayed — only tells you
the shape of a call once it's *finished*. Sections 2–3's `.graph` span is
built from the complete run, after `session.end`. If you want a dashboard
that updates as agents start and finish, that's too late.

Realtime mode changes what the converter emits, not how you trigger it:
instead of (or on top of) the normal `.agent`/`.tool`/`.chat` spans, it also
emits a tiny point-in-time span the moment each boundary starts and again
the moment it completes —  `topology.node.started`/`topology.node.completed`
for an agent call, `tool.started`/`tool.completed` for a tool call,
`llm.started`/`llm.completed` for an LLM call. The single end-of-run
`.graph` span is **not** emitted in realtime mode — it would need the whole
run to already be finished, which defeats the point.

```bash
mas-lab telemetry convert library-telemetry/examples/qa-agent/events.jsonl \
  -o /tmp/otel_sdk_spans_realtime.jsonl --app-name qa-agent --realtime
python3 -c "
import json
for line in open('/tmp/otel_sdk_spans_realtime.jsonl'):
    s = json.loads(line)
    print(s['name'])
"
```

That replays a whole file at once for inspection, but the same flag works
live, where it matters: add `realtime: true` to the `otel` plugin's config,
either via the shipped overlay or the real-time-specific env var (same name
the real `ioa-observe-sdk` uses, so it composes with anything already
reading it):

```bash
mas-ctl chat docs/tutorials/01-building-an-agent/agent.yaml \
  -o library-telemetry/overlays/observability-otel-realtime.yaml \
  -q "What is 15 * 23?" --flavour local

# or, on top of any otel overlay:
export OBSERVE_REALTIME_OBSERVABILITY_ENABLED=true
```

`topology.node.started` and `topology.node.completed` for the *same* agent
call — and likewise `tool.started`/`.completed`, `llm.started`/`.completed`
— are two independently started-and-ended OTel spans, which would normally
get two unrelated random span ids. The converter makes the "completed" half
reuse the "started" half's own span id, so anything reading these spans
(norm's realtime ingestion, see Tutorial 9 §6) sees one logical call, not
two disconnected ones:

```bash
cd library-telemetry
python -m pytest tests/telemetry/conversion/test_oxp_contract.py -k realtime -q
```

---

## Next

[Tutorial 9 — Knowledge graphs](../09-kg-oxp/) turns these files into a
knowledge graph (native→KG, first-party, and OTel→KG by reusing OXP's own
`norm` package) and checks the two KG paths match — including the realtime
spans from §6.

## Key takeaways

1. **Native** is the default flavour plugin — Tutorials 1–3 already use it.
2. **CLI commands** (`telemetry convert`, `telemetry push`) are shortcuts to
   pipeline steps. The live `otel` plugin uses the same converter.
3. **Serialization** is a file path or an infra manifest (collector /
   ClickHouse). `$OTEL_EXPORTER_OTLP_ENDPOINT` / `$CLICKHOUSE_HOST` skip the
   YAML. `--dry-run` does not need a running collector.
4. **Categories** (`structure` / `execution` / `semantic` / `provenance` /
   `governance`) are the same on CLI, pipeline `export_layers`, and the
   plugin. Governance is off unless you opt in.
5. **Realtime** (`--realtime` / `realtime: true` / `$OBSERVE_REALTIME_OBSERVABILITY_ENABLED`)
   trades the single end-of-run `.graph` span for incremental lifecycle
   spans, with the live/replay flag and the start/completed span-id pairing
   both working the same way everywhere.
