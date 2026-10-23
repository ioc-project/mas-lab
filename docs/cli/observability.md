<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# CLI observability

This page explains how to record what a run did. It covers the `events.jsonl` run log, the exchange log, and the flags and manifest settings that control them. Read it before you check an experiment result. Part of [References → CLI](../references/index.md#cli). Topic index: [command line](index.md).

Terms: a *manifest* is a YAML file that declares an agent or a MAS. An *overlay* is a small YAML file merged on top of a manifest. A *session* is one conversation with an agent. See the [glossary](../glossary.md).

## The three commands most people run

```bash
mas-ctl chat agent.yaml --events -q "hello"                 # writes traces/events.jsonl
mas-ctl chat agent.yaml -i --trace                          # prints a readable AGENT/LLM/TOOL exchange on stderr
mas-lab telemetry show traces/events.jsonl --filter tool_call   # summarises tool calls from the log
```

These flags apply to `mas-ctl chat`, `mas-ctl tui`, `mas-ctl run-mas`, and, for benchmarks, `mas-lab benchmark run`. The `--trace*` flags do not exist on `tui`.

Flag tables: [mas-ctl.md](mas-ctl.md). `config.yaml` keys: [config.yaml reference](../references/config.yaml.md).

## Three outputs

| Output | What it is | Main reader |
| --- | --- | --- |
| `events.jsonl` | Machine-readable run log. One JSON object per line. | Pipeline steps (`extract_trace_stats`, `eval_mce`), `mas-lab telemetry`, plots |
| Exchange log | Human-readable AGENT, LLM, and TOOL transcript on stderr. | You, while debugging (`mas-ctl chat --trace`) |
| OTel spans | OpenTelemetry export. An optional second sink. | External collectors (`--events-format otel`) |

In experiments, check structural claims in `events.jsonl`, never in the exchange log. Structural claims are, for example, that governance fired, tool counts, or that a human-in-the-loop (HITL) gate triggered.

## Turn on the event log

Pick one of three ways.

### Option 1: set it in the manifest

Add `observability` to the agent or MAS manifest. The setting persists across runs.

```yaml
spec:
  observability:
    enabled: true
    format: native          # native | boundary | both | otel
    events_file: traces/events.jsonl
```

### Option 2: use the observability overlay

Use this for experiments. The overlay is the standard patch that turns on native events.

```bash
mas-ctl chat agent.yaml -o docs/schemas/examples/overlays/observability-native.yaml
mas-ctl run-mas mas.yaml -o docs/schemas/examples/overlays/observability-native.yaml
```

Source: [observability-native.yaml](../schemas/examples/overlays/observability-native.yaml).

### Option 3: pass flags for one session

These flags work on `chat`, `tui`, and `run-mas`. They override the manifest for that session.

| Flag | Default | Description |
| --- | --- | --- |
| `--events` / `--no-events` | manifest | Force observability on or off. |
| `--events-file PATH` | `traces/events.jsonl` under the run directory | Write the JSONL log here. |
| `--events-stderr` | off | Also stream JSONL records on stderr. Stdout stays the conversation. `--events-stdout` is a legacy alias. |
| `--events-format FORMAT` | manifest | Choose `native`, `boundary`, `both`, or `otel`. |

`--events` equals the `observability-native` overlay for one run. For benchmarks, use the overlay or the manifest. Then every scenario sees the same settings.

```bash
mas-ctl chat agent.yaml -i \
  --events \
  --events-file logs/events.jsonl \
  --events-format native
```

### Which setting does what

| Goal | Manifest | Overlay | CLI |
| --- | --- | --- | --- |
| Native `events.jsonl` | `spec.observability.enabled: true` | `observability-native.yaml` | `--events` |
| Custom path | `events_file:` | Patch `events_file` | `--events-file PATH` |
| Stream JSONL to terminal | none | none | `--events-stderr` |
| Boundary and native | `format: both` | none | `--events-format both` |
| OTel spans in file | `format: otel` | none | `--events-format otel` |

## Read the exchange log

The exchange log is a pretty-printed view of structured `ExchangeRecord` hops. It goes to stderr for `mas-ctl chat` and `run-mas`. Stdout stays the conversation.

`--trace` prints a compact summary. `--trace full` also prints the payloads.

```bash
mas-ctl chat agent.yaml -i --trace
mas-ctl chat agent.yaml -i --trace full
mas-ctl chat agent.yaml -i --trace --trace-color
```

Headers show the raw hop, for example `TOOL[activate_skill]`. When the tool advertises `spec.semantics` or `list_tools().semantics`, the header adds an interpretation, such as `SKILL[answer-formatting]` or `MEMORY[write]`. The model id is the one the engine resolved (`spec.models`, `--model`). It is never a label-only fallback.

Full flag and `config.yaml` tables: [mas-ctl.md](mas-ctl.md#exchange-log) and [config.yaml `mas_ctl`](../references/config.yaml.md#mas_ctl).

## `events.jsonl` record shape

Each line is one record. Common `kind` values (native format):

| Kind | Meaning |
| --- | --- |
| `execution_start`, `execution_end` | Run lifecycle. |
| `llm_call_start`, `llm_call_end` | Model call. `_end` carries latency. `llm_call_end.tools` lists the function names sent to the model. |
| `context_assembled` | Prompt snapshot for that call. `tools` is the same function-name list. |
| `tool_call_start`, `tool_call_end` | Tool call. |
| `governance_event`, `governance_policy` | Policy and budget hooks. |
| `routing`, `routing_result` | Delegation between agents in a MAS. |

After a benchmark, logs live at:

```text
<output_dir>/<scenario>/item<N>/r<N>/traces/events.jsonl
```

With the trace cache on, the path can be a link to a shared copy. Pipeline steps resolve the link for you.

## Inspect a finished run

```bash
mas-lab telemetry show     path/to/traces/events.jsonl               # summary; add --filter tool_call
mas-lab telemetry convert  path/to/traces/events.jsonl -o spans.jsonl --app-name my-mas
mas-lab telemetry push     spans.jsonl --endpoint http://localhost:4318
```

`show` summarises the log. `convert` writes OpenTelemetry spans. `push` sends spans to a collector.

## Benchmarks

`mas-lab benchmark run experiment.yaml` does not take `--events`. Set observability in one of these places:

1. The flavour (`experiment.yaml`, instrumentation preset).
2. Overlays on scenarios, or on the whole experiment with `-x`.
3. Defaults in the MAS manifest.

The level hooks in `experiment.yaml` run after execution: `run.post`, `item.post`, `scenario.post`, and the experiment `post:`.

```bash
mas-lab benchmark run labs/lifecycle-control.lab/experiment.yaml --progress
```

For pipeline and figure workflows, see [Tutorial 3](../tutorials/03-experiments-and-analysis/README.md).

## Related

- [Event model](../references/event-model.md): the native announced stream
- [OpenTelemetry](../standards/opentelemetry.md)
- [ctl/tui.md](../ctl/tui.md): the same `--events*` flags
- [Web UI](../ui/index.md): browse run artifacts
- [glossary.md](../glossary.md): manifest terms
- [summarization.md](../manifests/summarization.md): summarizer and MCE judge models, and the summary system prompt in the log
- Example: [summarizer-override](https://github.com/ioc-project/mas-lab/tree/main/library-standard/examples/context/summarizer-override)
