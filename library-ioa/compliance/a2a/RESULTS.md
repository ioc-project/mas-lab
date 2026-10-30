# Compliance results

## A2A

Target: Tutorial 1 QA agent served by `mas-ctl serve --infra-ref` (Application
endpoint `usage: listen`).

Tools:

- official `a2a` CLI v0.3.0: card discovery and message send passed;
- official A2A TCK compatibility report captured on 2026-09-29;
- JSON-RPC, HTTP+JSON, and gRPC fixture run: `99` requirements passed, `0`
  failed, with `25` requirements not tested because the TCK has no mapped test;
- report: `a2a-tck-compatibility-2026-09-29.json`.

Passing areas include AgentCard discovery, declared JSON-RPC interface,
interface schema validation, task response schema, and basic message exchange.

The report summary is `79.8%` overall specification coverage, with `80.7%`
MUST, `63.6%` SHOULD, and `100.0%` MAY coverage. The untested requirements are
TCK coverage gaps, not recorded implementation failures. This is an honest
coverage result, not a full A2A compliance claim.

The deterministic fixture's latest dual-transport run has no failed executed
requirements. Skipped and untested cases correspond to capabilities or
transports not declared by the fixture.
