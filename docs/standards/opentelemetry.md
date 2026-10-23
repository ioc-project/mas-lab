<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# OpenTelemetry

<p class="eyebrow">Open standards</p>

Every run writes a native trace, `events.jsonl`: one JSON line per model,
tool, or agent call. [OpenTelemetry](https://opentelemetry.io/) (OTel) is the
optional second export: timed spans with a name, attributes, and a parent, so
any OTel backend can read the run.

MAS-Lab writes those spans in the wire format of AGNTCY's
[`ioa-observe-sdk`](https://pypi.org/project/ioa-observe-sdk/). Conversion and
OTLP export live in `mas-library-telemetry`. The native stream does not
depend on OTel; you add the library when you want spans.

## How MAS-Lab implements it

Live export and replay share one converter (`events_to_otel`).

```bash
uv pip install -e "library-telemetry[convert,verify]"
mas-lab telemetry convert traces/events.jsonl -o otel_sdk_spans.jsonl
mas-lab telemetry push otel_sdk_spans.jsonl   # OTLP to a collector
```

`--events-format otel` on `mas-ctl` writes spans during the run. Pipeline
shortcuts: `telemetry:pipelines/native-to-otel-json.yaml` and
`export-otel-collector.yaml`.

The native event model (an announced stream, not a query interface) is
[Event model](../references/event-model.md). SCOPE consumes the same runs;
see [IoC integrations](../discover/integrations.md) and
[Tutorial 9](../tutorials/09-kg-scope/README.md).

## Dependencies

| Piece | Package |
| --- | --- |
| Conversion, replay, CLI | `mas-library-telemetry` extras `convert`, `verify`, `bench` |
| Span SDK | `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http` |
| AGNTCY observe-sdk | `ioa-observe-sdk>=1.0.49` |
| Native recorder | `mas-library-standard` observability plugin |

## Compliance

Spans follow the in-repo span contracts:

- [`library-telemetry/schemas/mas.spanspec.yaml`](https://github.com/ioc-project/mas-lab/blob/main/library-telemetry/schemas/mas.spanspec.yaml)
- [`library-telemetry/schemas/genai-observe-sdk.spanspec.yaml`](https://github.com/ioc-project/mas-lab/blob/main/library-telemetry/schemas/genai-observe-sdk.spanspec.yaml)

`mas-lab telemetry verify` checks a converted file against those specs.
There is no separate OpenTelemetry TCK in this repository.

## Related

- [Tutorial 8 — Telemetry](../tutorials/08-telemetry/README.md)
- [CLI observability](../cli/observability.md)
- [Event model](../references/event-model.md)
- [Tutorial 9 — KG and SCOPE](../tutorials/09-kg-scope/README.md)
