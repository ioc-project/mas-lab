<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-lab-telemetry(1)

## NAME

`mas-lab telemetry` — convert, verify, push, and dump OTel spans

## SYNOPSIS

```text
mas-lab telemetry [COMMAND] [ARGS]...
mas-lab otel [COMMAND] [ARGS]...
```

## DESCRIPTION

Library CLI component (entry point `mas.lab.cli.components`). There is no
separate `mas-telemetry` binary. Commands call the same functions as the
[pipeline YAML](../../pipelines/README.md).

`mas-lab otel` is an alias of this group ([otel.md](otel.md)).

When this component registers it **replaces** the core `mas-lab telemetry`
stubs for `push` / `verify` / `dump` and **keeps** core inspection commands
(`show`, `apps`, `sessions`, `traces`) that have no OTel equivalent.

## COMMANDS

| Command | Summary | Page |
|---------|---------|------|
| `convert` | events.jsonl → otel_sdk_spans.jsonl | [convert](convert.md) |
| `verify` | Structural + SpanSpec | [verify](verify.md) |
| `inspect` | Span-file summary | [inspect](inspect.md) |
| `push` | Serialize to an OTLP collector | [push](push.md) |
| `dump` | ClickHouse → JSONL | [dump](dump.md) |
| `compare` | Span-file parity | [compare](compare.md) |
| `kinds` | Registered native event kinds | [kinds](kinds.md) |
| `list-apps` | Apps in ClickHouse `otel_traces` | [list-apps](list-apps.md) |

Core inspection (owned by mas-lab, merged into this group):

| Command | Page |
|---------|------|
| `show` | [mas-lab telemetry show](../../../lab/docs/cli/telemetry-show.md) |
| `apps` | [telemetry-apps](../../../lab/docs/cli/telemetry-apps.md) |
| `sessions` | [telemetry-sessions](../../../lab/docs/cli/telemetry-sessions.md) |
| `traces` | [telemetry-traces](../../../lab/docs/cli/telemetry-traces.md) |

Site index: [docs/cli/mas-lab.md](../../../docs/cli/mas-lab.md).

## OPTIONS

This command takes no extra flags. See `--help` for inherited group options.

## SEE ALSO

[conversion.md](../conversion.md), [otlp-collector.md](../otlp-collector.md),
[cli-pipelines.md](../../../lab/docs/cli-pipelines.md)
