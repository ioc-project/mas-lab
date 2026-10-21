<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Instrumentation Tools

This folder documents instrumentation utilities used to observe MAS traffic.

## What ships

Observability in this release uses native event recording (`events.jsonl`) and optional
OpenTelemetry export via manifest `observability` config; see
[architecture-instrumentation.md](../architecture-instrumentation.md) and
[trajectory-schema.md](../trajectory-schema.md).

## Not included

Proxy-style (man-in-the-middle) instrumentation of agent and tool-server traffic, and
cognitive fault-injection plugins, are not part of this release.

## Related

- [mealy-envelope.md](../mealy-envelope.md): native span emission on envelope symbols
