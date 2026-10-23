<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Tutorial 8 — Telemetry: native, OTel, and replay

> **Packages:** `mas-library-standard` (native plugin), `mas-library-telemetry`
> (OTel plugin, replay, collector, ClickHouse)
> **Prerequisite:** [Tutorials 0-3](../03-experiments-and-analysis/README.md)
> **Follow-on:** [Tutorial 9 — Knowledge graphs](../09-kg-scope/README.md)
> **Example traces:** [`library-telemetry/examples/qa-agent/`](https://github.com/ioc-project/mas-lab/tree/main/library-telemetry/examples/qa-agent)

## Goal

Every run writes a native trace, `events.jsonl`: one JSON line per model,
tool or agent call. You convert it to OpenTelemetry (OTel) spans, timed
operations with a name, attributes and a parent, so any OTel backend can read
the run. MAS Lab writes spans in the `ioa-observe-sdk` wire format.

## What you need

```bash
uv pip install -e "library-telemetry[convert,verify]"
# optional read-back from ClickHouse: library-telemetry[clickhouse]
```

Offline steps use the shipped qa-agent example: no LLM key, no collector. The
`mas-ctl chat` commands call a model and need your key from
[Tutorial 0](../00-environment-setup/README.md). Run from the repository root. To check them all:

```bash
pytest tests/tutorials/test_tutorial_08.py
pytest tests/tutorials/test_scenario_commands.py -k tuto-08
```

## How the pieces fit

| Step | What you do | What it produces |
| --- | --- | --- |
| 1 | Native trace (default) | `events.jsonl` |
| 2 | Live OTel | `otel_sdk_spans.jsonl`, or spans in a collector |
| 3 | Replay native to OTel | `otel_sdk_spans_replay.jsonl` |
| 4 | Pick a sink | file, OTel collector, ClickHouse |
| 5 | Compare live and replay | a passing test |
| 6 | Realtime spans | `otel_sdk_spans_realtime.jsonl` |

Live export and replay share one converter (`events_to_otel`).
`mas-lab telemetry convert` and `telemetry push` are shortcuts for the
pipeline steps `events_to_otel` and `push_otlp` (see
`telemetry:pipelines/native-to-otel-json.yaml` and `export-otel-collector.yaml`).

Spans fall into five categories: `structure`, `execution`, `semantic`,
`provenance` and `governance`. All but `governance` are on by default. Change
them with `--layer` / `--governance` (CLI), `export_layers:` (pipeline step)
or the plugin overlay. Details: the
[conversion reference](https://github.com/ioc-project/mas-lab/blob/main/library-telemetry/docs/conversion.md).

---

## Step 1 — Check the native trace

The `local` flavour (a deployment preset, see the [glossary](../../glossary.md))
enables the native plugin. Each boundary crossing becomes one `events.jsonl`
line. More on native traces: [Tutorial 1](../01-building-an-agent/README.md#step-7-inspecting-the-trace),
[Tutorial 3](../03-experiments-and-analysis/README.md#where-traces-are-stored).

```bash
mas-ctl chat docs/tutorials/01-building-an-agent/agent.yaml \
  -o pkg://mas.library.standard/overlays/observability-native.yaml \
  -q "What is 15 * 23?" --flavour local
```

You should see `docs/tutorials/01-building-an-agent/traces/events.jsonl`. A
captured copy is `library-telemetry/examples/qa-agent/events.jsonl`.

---

## Step 2 — Emit OTel spans live

The OTel plugin replaces the native plugin. You pick it with an overlay: a
small YAML file merged on top of a manifest with `-o`.

### 2a — Write a JSON file

```bash
mas-ctl chat docs/tutorials/01-building-an-agent/agent.yaml \
  -o library-telemetry/overlays/observability-otel-json.yaml \
  -q "What is 15 * 23?" --flavour local
```

You should see `traces/otel_sdk_spans.jsonl` and no `events.jsonl`. A
captured copy is `library-telemetry/examples/qa-agent/otel_sdk_spans_live.jsonl`.

### 2b — Send to an OTel collector

A collector is a server that receives spans over OTLP, the OpenTelemetry wire
protocol. Start one locally. With the env var set, you need no infra manifest.

```bash
docker compose -f library-telemetry/docker/compose.yaml up -d
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318

mas-ctl chat docs/tutorials/01-building-an-agent/agent.yaml \
  -o library-telemetry/overlays/observability-otel-collector.yaml \
  -q "What is 15 * 23?" --flavour local
```

`mas-lab telemetry push` sends an existing span or native file to a
collector. `--dry-run` needs no Docker.

```bash
mas-lab telemetry push library-telemetry/examples/qa-agent/otel_sdk_spans_live.jsonl --dry-run
mas-lab telemetry push library-telemetry/examples/qa-agent/otel_sdk_spans_live.jsonl \
  --infra library-telemetry/infra/local-otel.yaml --dry-run
```

For a shared collector, copy `local-otel.yaml` (an infra manifest: a YAML file
that says where a service lives), set its endpoint and pass it with `--infra`.

---

## Step 3 — Replay native events as OTel

To get spans from an existing `events.jsonl` without re-running the agent:

```bash
mas-lab telemetry convert library-telemetry/examples/qa-agent/events.jsonl \
  -o /tmp/otel_sdk_spans.jsonl --app-name qa-agent
```

You should see `"spans": 9`. `--app-name` becomes `application_id` and `service.name` on every span. It is
required unless the events carry a name. The CLI wraps a Python function:

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

You should see `replayed 84 events → /tmp/otel_sdk_spans_replay.jsonl`, maybe
after a harmless `duplicate_call_start` warning. To replay after every
experiment run, add this pipeline step (from `native-to-otel-json.yaml`):

```yaml
- type: events_to_otel
  config:
    converter_profile: observe_sdk
    output_filename: otel_sdk_spans_replay.jsonl
```

To keep native events and live spans side by side, use the overlay
`observability-native-and-otel.yaml`. You should see both `events.jsonl` and
`otel_sdk_spans.jsonl` in `traces/`.

```bash
mas-ctl chat docs/tutorials/01-building-an-agent/agent.yaml \
  -o library-telemetry/overlays/observability-native-and-otel.yaml \
  -q "What is 15 * 23?" --flavour local
```

---

## Step 4 — Choose a sink

Conversion is identical everywhere. Only the destination changes.

| Sink | You pass | Env shortcut |
| --- | --- | --- |
| JSON file | a path (`-o`, `output_filename`) | none |
| `OtelCollector` | `--infra library-telemetry/infra/local-otel.yaml` | `$OTEL_EXPORTER_OTLP_ENDPOINT` |
| `ClickHouse` (read-back) | `--infra library-telemetry/infra/local-clickhouse.yaml` | `$CLICKHOUSE_HOST` |

```bash
docker compose -f library-telemetry/docker/compose.yaml up -d
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
export CLICKHOUSE_HOST=localhost
```

See `library-telemetry/docker/README.md` for the services this starts.

---

## Step 5 — Compare live and replayed spans

Both paths start from the same native records, so they must produce the same
spans. Ids differ (live random, replay seeded); names, topology and
attributes match.

```bash
cd library-telemetry
python -m pytest tests/telemetry/plugins/test_live_plugin_vs_replay.py -q
```

Every case should pass. It runs on the trip-planner sample and example and
the qa-agent example.

---

## Step 6 — Emit spans while the run is going

The `.graph` span exists only after `session.end`. For a live dashboard,
realtime mode instead emits a point-in-time span when each boundary starts and
another when it completes:

| Boundary | Spans |
| --- | --- |
| Agent call | `topology.node.started`, `topology.node.completed` |
| Tool call | `tool.started`, `tool.completed` |
| LLM call | `llm.started`, `llm.completed` |

```bash
mas-lab telemetry convert library-telemetry/examples/qa-agent/events.jsonl \
  -o /tmp/otel_sdk_spans_realtime.jsonl --app-name qa-agent --realtime
python3 -c "
import json
for line in open('/tmp/otel_sdk_spans_realtime.jsonl'):
    print(json.loads(line)['name'])
"
```

You should see `topology.node.*`, `llm.*` and the session and agent spans, and
no `*.graph` span. The qa-agent example has no tool calls.

The same mode works live, through the shipped overlay or the environment
variable of the real `ioa-observe-sdk`:

```bash
mas-ctl chat docs/tutorials/01-building-an-agent/agent.yaml \
  -o library-telemetry/overlays/observability-otel-realtime.yaml \
  -q "What is 15 * 23?" --flavour local

# or, on top of any otel overlay:
export OBSERVE_REALTIME_OBSERVABILITY_ENABLED=true
```

Each `completed` span reuses the id of its `started` span. A reader such as
the `norm` normalizer of SCOPE then sees one
logical call (used in [Tutorial 9, Step 6](../09-kg-scope/README.md#step-6-keep-the-graph-current-in-realtime)).
Check the pairing:

```bash
cd library-telemetry
python -m pytest tests/telemetry/conversion/test_oxp_contract.py -k realtime -q
```

---

## What you learned

1. Live export and replay use one converter. `telemetry convert` and `push`
   are shortcuts for pipeline steps.
2. The sink is a file path or an infra manifest; env vars replace the YAML and
   `--dry-run` needs no collector.
3. `--realtime` (or `realtime: true`, or
   `$OBSERVE_REALTIME_OBSERVABILITY_ENABLED`) trades the `.graph` span for
   incremental spans.

## Next

[Tutorial 9 — Knowledge graphs](../09-kg-scope/README.md) turns these files into
a knowledge graph.
